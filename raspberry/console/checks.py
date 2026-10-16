import asyncio
import ipaddress
import os
import secrets
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit


class Scope:
    def __init__(self, networks):
        self.networks = [ipaddress.ip_network(n, strict=True) for n in networks]

    def check(self, value):
        ip = ipaddress.ip_address(value)
        if ip.is_multicast or ip.is_unspecified or str(ip) == '255.255.255.255':
            raise ValueError('A unicast target is required')
        if not any(ip in net for net in self.networks if ip.version == net.version):
            raise ValueError(f'{ip} is outside the configured target scope')
        return str(ip)

    async def resolve(self, host):
        if not isinstance(host, str) or not host or len(host) > 253 or any(c.isspace() for c in host):
            raise ValueError('Enter a valid target hostname or IP')
        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 4)
        addresses = list(dict.fromkeys(r[4][0] for r in results))
        # Validate every answer, then pin the chosen IP for the actual connection.
        for ip in addresses:
            self.check(ip)
        if not addresses:
            raise ValueError('No address found')
        return addresses[0]


def ports_from(value):
    try:
        values = [int(p.strip()) for p in str(value).split(',')]
    except ValueError as exc:
        raise ValueError('Ports must be comma-separated numbers') from exc
    if not 1 <= len(values) <= 16 or any(p < 1 or p > 65535 for p in values):
        raise ValueError('Choose 1 to 16 ports between 1 and 65535')
    return list(dict.fromkeys(values))


async def tcp_probe(ip, port, banner=False):
    writer = None
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 1.5)
        result = dict(ip=ip, port=port, reachable=True, latency_ms=round((time.monotonic()-start)*1000, 2))
        if banner:
            try:
                data = await asyncio.wait_for(reader.read(256), .4)
                result['greeting'] = data.decode('utf-8', 'replace')
            except TimeoutError:
                result['greeting'] = '(no unsolicited greeting)'
        return result
    except (OSError, TimeoutError) as exc:
        return dict(ip=ip, port=port, reachable=False, reason=type(exc).__name__)
    finally:
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, TimeoutError):
                pass


async def echo_check(kind, ip, port, count, token, emit):
    if not 1 <= count <= 100:
        raise ValueError('Packet count must be between 1 and 100')
    if not 8 <= len(token) <= 128 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Enter the receiver token (8-128 non-space ASCII characters)')
    loop = asyncio.get_running_loop()
    sock, writer, reader = None, None, None
    rtts = []
    try:
        if kind == 'udp_echo':
            sock = socket.socket(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            await loop.sock_connect(sock, (ip, port))
        else:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 2)
        async def exchange(data):
            if sock:
                await loop.sock_sendall(sock, data)
                # Ignore late replies from earlier probes, bounded by outer timeout.
                while True:
                    reply = await loop.sock_recv(sock, 512)
                    if reply == data:
                        return
            writer.write(data)
            await writer.drain()
            reply = await reader.readline()
            if reply != data:
                raise ValueError('Receiver returned an unexpected response')
        # Require a matching cooperating receiver before the paced sequence.
        hello = f'BW1 {token} hello {secrets.token_hex(8)}\n'.encode()
        await asyncio.wait_for(exchange(hello), 2)
        for seq in range(count):
            payload = f'BW1 {token} {seq} {secrets.token_hex(8)}\n'.encode()
            start = time.monotonic()
            try:
                await asyncio.wait_for(exchange(payload), .75)
                rtt = (time.monotonic() - start) * 1000
                rtts.append(rtt)
                await emit('test.sample', dict(sequence=seq, received=True, rtt_ms=round(rtt, 2)))
            except TimeoutError:
                await emit('test.sample', dict(sequence=seq, received=False))
                if writer:
                    raise ValueError('TCP echo timed out; stream stopped to avoid misaligned replies')
            await asyncio.sleep(.1)
        jitter = sum(abs(b-a) for a, b in zip(rtts, rtts[1:])) / max(1, len(rtts)-1)
        return dict(sent=count, received=len(rtts), loss_percent=round(100*(count-len(rtts))/count, 2),
                    mean_rtt_ms=round(sum(rtts)/len(rtts), 2) if rtts else None,
                    rtt_variation_ms=round(jitter, 2), note='Round-trip variation; not one-way jitter or throughput.')
    finally:
        if sock:
            sock.close()
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, TimeoutError):
                pass


async def run_check(kind, options, scope, emit):
    target = options.get('target', '')
    if kind == 'discovery':
        net = ipaddress.ip_network(target, strict=False)
        if net.version != 4 or net.num_addresses > 32:
            raise ValueError('Discovery accepts an IPv4 /27 or smaller network (at most 32 addresses)')
        hosts = [scope.check(str(ip)) for ip in net.hosts()]
        ports = ports_from(options.get('ports', '80,443'))
        if len(ports) > 4:
            raise ValueError('Discovery accepts at most four ports')
        found = 0
        for host in hosts:
            rows = await asyncio.gather(*(tcp_probe(host, p) for p in ports))
            open_ports = [r['port'] for r in rows if r['reachable']]
            found += bool(open_ports)
            await emit('test.host', dict(ip=host, responding_ports=open_ports))
        return dict(checked=len(hosts), responding=found, note='No open selected TCP port does not prove a host is offline.')
    if kind in ('http', 'tls'):
        url = urlsplit(target if '://' in target else 'https://' + target)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError('Use an HTTP(S) URL without credentials')
        if any(ord(c) < 33 for c in target):
            raise ValueError('URL contains whitespace or control characters')
        host = url.hostname.encode('idna').decode()
        ip = await scope.resolve(host)
        secure = url.scheme == 'https' or kind == 'tls'
        port = url.port or (443 if secure else 80)
        ctx = ssl.create_default_context() if secure else None
        writer = None
        start = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port, ssl=ctx,
                                                   server_hostname=host if ctx else None), 5)
            if kind == 'tls':
                obj = writer.get_extra_info('ssl_object')
                cert = obj.getpeercert()
                return dict(valid=True, protocol=obj.version(), expires=cert.get('notAfter'),
                            issuer=cert.get('issuer'), subject=cert.get('subject'))
            path = (url.path or '/') + ('?' + url.query if url.query else '')
            if not path.isascii():
                raise ValueError('URL path must be percent encoded')
            authority = f'[{host}]' if ':' in host else host
            authority += f':{port}'
            writer.write(f'HEAD {path} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(headers) > 16384:
                raise ValueError('Response headers exceed limit')
            status = headers.split(b'\r\n', 1)[0].decode('ascii', 'replace')
            return dict(status=status, latency_ms=round((time.monotonic()-start)*1000, 2), ip=ip)
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, TimeoutError):
                    pass
    ip = await scope.resolve(target)
    if kind in ('tcp', 'services', 'segmentation'):
        rows = []
        expected = options.get('expected', 'deny')
        if expected not in ('allow', 'deny'):
            raise ValueError('Expected must be allow or deny')
        for port in ports_from(options.get('ports', '80,443')):
            row = await tcp_probe(ip, port, kind == 'services')
            if kind == 'segmentation':
                row['expected'] = expected
                row['matches'] = row['reachable'] == (expected == 'allow')
                row['note'] = 'Unreachable can also mean no listener, routing failure, or timeout.'
            rows.append(row)
            await emit('test.port', row)
        return dict(results=rows)
    if kind == 'ping':
        flag = '-n' if os.name == 'nt' else '-c'
        args = ['ping', flag, '3', ip] if os.name == 'nt' else ['ping', '-n', '-c', '3', '-W', '1', ip]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 8)
            return dict(exit_code=proc.returncode, output=out.decode(errors='replace')[:4096])
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    if kind == 'dns':
        name = options.get('query', 'example.com').rstrip('.')
        labels = name.encode('idna').split(b'.')
        if len(name) > 253 or any(not 1 <= len(p) <= 63 for p in labels):
            raise ValueError('Invalid DNS query name')
        ident = secrets.randbelow(65536)
        packet = struct.pack('!6H', ident, 0x100, 1, 0, 0, 0) + b''.join(bytes([len(p)]) + p for p in labels) + b'\0\0\1\0\1'
        sock = socket.socket(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        start = time.monotonic()
        try:
            await loop.sock_connect(sock, (ip, 53))
            await loop.sock_sendall(sock, packet)
            reply = await asyncio.wait_for(loop.sock_recv(sock, 4096), 2)
            if len(reply) < 12:
                raise ValueError('Truncated DNS response')
            rid, flags, _, answers, _, _ = struct.unpack('!6H', reply[:12])
            if rid != ident or not flags & 0x8000:
                raise ValueError('Mismatched DNS response')
            return dict(rcode=flags & 15, answers=answers, truncated=bool(flags & 0x200), latency_ms=round((time.monotonic()-start)*1000, 2))
        finally:
            sock.close()
    if kind in ('tcp_echo', 'udp_echo'):
        port = ports_from(options.get('ports', '9877'))
        if len(port) != 1:
            raise ValueError('Echo test needs exactly one port')
        return await echo_check(kind, ip, port[0], int(options.get('count', 20)), options.get('token', ''), emit)
    raise ValueError('Unknown test')