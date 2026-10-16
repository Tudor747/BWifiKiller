"""Cooperating receiver for bounded TCP/UDP diagnostics. No amplification."""
import argparse
import asyncio
import hmac
import time
from pathlib import Path


class Gate:
    def __init__(self, token):
        self.prefix = ('BW1 ' + token + ' ').encode()
        self.peers = {}

    def accept(self, data, peer):
        if len(data) > 256 or not data.endswith(b'\n') or not hmac.compare_digest(data[:len(self.prefix)], self.prefix):
            return False
        # Bound per-source traffic and memory. Echo exactly the received bytes.
        now = time.monotonic()
        key = peer[0]
        seen, count = self.peers.get(key, (now, 0))
        if now - seen >= 1:
            seen, count = now, 0
        if count >= 20:
            return False
        if len(self.peers) >= 256 and key not in self.peers:
            self.peers = {k: v for k, v in self.peers.items() if now-v[0] < 2}
            if len(self.peers) >= 256:
                return False
        self.peers[key] = (seen, count+1)
        return True


class EchoUDP(asyncio.DatagramProtocol):
    def __init__(self, gate):
        self.gate = gate

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if self.gate.accept(data, addr):
            self.transport.sendto(data, addr)


def tcp_handler(gate):
    async def handle(reader, writer):
        peer = writer.get_extra_info('peername')
        try:
            async with asyncio.timeout(60):
                for _ in range(101):
                    data = await asyncio.wait_for(reader.readline(), 5)
                    if not data or not gate.accept(data, peer):
                        return
                    writer.write(data)
                    await writer.drain()
        except (OSError, ValueError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    return handle


async def serve(host, port, token):
    gate = Gate(token)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: EchoUDP(gate), local_addr=(host, port))
    server = await asyncio.start_server(tcp_handler(gate), host, port, limit=512)
    print(f'Cooperating TCP/UDP receiver on {host}:{port}; token is not logged.')
    try:
        async with server:
            await server.serve_forever()
    finally:
        transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='Bind address; use your LAN IP to receive from the Pi')
    parser.add_argument('--port', type=int, default=9877)
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    if not 8 <= len(token) <= 128 or not token.isascii() or any(c.isspace() for c in token):
        parser.error('Token must contain 8-128 non-space ASCII characters')
    try:
        asyncio.run(serve(args.host, args.port, token))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()