import argparse
import asyncio
import csv
import io
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from .buttons import Buttons
from .catalog import catalog, TESTS
from .checks import Scope, run_check
from .serial_link import SerialBridge
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = dict(host='127.0.0.1', port=8765, serial_port='auto', baud=115200,
                demo=False, data_dir='data', allowed_networks=['127.0.0.0/8'], gpio={'enabled': False, 'pins': {}})
RUNTIME = web.AppKey('runtime', object)


class Runtime:
    def __init__(self, config, config_path):
        self.config, self.config_path = config, config_path
        data = Path(config['data_dir'])
        if not data.is_absolute():
            data = config_path.parent / data
        self.store = Store(data / 'console.sqlite3')
        self.clients = set()
        self.bridge = SerialBridge(config, self.publish)
        self.buttons = Buttons(config.get('gpio', {}), self.publish)
        self.job = None
        self.job_state = None
        self.wifi, self.ble = {}, {}
        self.alerted_ble = set()

    async def publish(self, source, kind, data, persist=True):
        if kind == 'wifi.scan' and data.get('state') == 'scanning':
            self.wifi.clear()
        if kind == 'wifi.network':
            self.wifi[data.get('bssid', data.get('ssid'))] = data
        if kind == 'ble.device':
            self.ble[data.get('address')] = data
            if len(self.ble) > 200:
                self.ble.pop(next(iter(self.ble)))
        if kind == 'wifi.scan' and data.get('state') == 'complete':
            baseline = self.store.baseline('wifi')
            if baseline is not None:
                for address in self.wifi.keys() - baseline.keys():
                    await self.publish('pi', 'alert.wifi.new', self.wifi[address])
                for address in baseline.keys() - self.wifi.keys():
                    await self.publish('pi', 'alert.wifi.missing', dict(baseline[address], note='Not observed in this scan; this does not prove the AP is offline.'))
                for address in self.wifi.keys() & baseline.keys():
                    before, after = baseline[address], self.wifi[address]
                    changed = [key for key in ('ssid', 'channel', 'security') if before.get(key) != after.get(key)]
                    if changed:
                        await self.publish('pi', 'alert.wifi.changed', dict(bssid=address, changed=changed, before=before, after=after))
        if kind == 'ble.device':
            baseline = self.store.baseline('ble')
            address = data.get('address')
            if baseline is not None and address not in baseline and address not in self.alerted_ble:
                if len(self.alerted_ble) < 1000:
                    self.alerted_ble.add(address)
                    await self.publish('pi', 'alert.ble.new', dict(data, note='BLE addresses may rotate; a new address need not mean a new physical device.'))
        if kind == 'status':
            # High-rate status and display updates are ephemeral; lifecycle and
            # measurement events are retained, so the database stays useful.
            persist = False
        event = self.store.add(source, kind, data) if persist else dict(source=source, type=kind, data=data, ts=time.time())
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow client must reconnect and replay the persisted cursor.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'type': 'resync', 'data': {}})
        return event

    async def execute(self, ident, kind, options):
        async def emit(event, data):
            await self.publish('pi', event, dict(data, job=ident))
        try:
            async with asyncio.timeout(90):
                result = await run_check(kind, options, Scope(self.config['allowed_networks']), emit)
            self.job_state = dict(id=ident, kind=kind, state='complete', result=result)
        except asyncio.CancelledError:
            self.job_state = dict(id=ident, kind=kind, state='cancelled')
        except Exception as exc:
            self.job_state = dict(id=ident, kind=kind, state='failed', error=str(exc) or type(exc).__name__)
        await self.publish('pi', 'test.finished', self.job_state)

    async def start(self):
        await self.publish('pi', 'system.start', {'demo': self.config['demo'], 'session': self.store.session})
        await self.bridge.start()
        try:
            await self.buttons.start()
        except Exception as exc:
            self.buttons.close()
            await self.publish('pi', 'gpio.error', {'message': str(exc)})

    async def close(self):
        if self.job and not self.job.done():
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
        self.buttons.close()
        await self.bridge.close()
        self.store.close()


@web.middleware
async def local_only(request, handler):
    # DNS rebinding / cross-origin protection for a local hardware-control app.
    try:
        host = urlsplit('http://' + request.host).hostname
    except ValueError:
        host = None
    if host not in ('localhost', '127.0.0.1', '::1'):
        raise web.HTTPForbidden(text='This console is local to the Pi')
    origin = request.headers.get('Origin')
    if origin and origin != f'{request.scheme}://{request.host}':
        raise web.HTTPForbidden(text='Cross-origin requests are not allowed')
    try:
        response = await handler(request)
    except (ValueError, ConnectionError, TimeoutError) as exc:
        response = web.json_response({'error': str(exc) or type(exc).__name__}, status=400)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    return response


async def snapshot(request):
    rt = request.app[RUNTIME]
    return web.json_response(dict(device=rt.bridge.state, demo=rt.config['demo'], catalog=catalog(), tests=TESTS,
                                 job=rt.job_state, session=rt.store.session, wifi=list(rt.wifi.values()), ble=list(rt.ble.values()),
                                 settings={k: rt.config[k] for k in ('serial_port', 'baud', 'allowed_networks', 'gpio')}))


async def command(request):
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError('Command must be an object')
    rt = request.app[RUNTIME]
    result = await rt.bridge.command(body.get('command'), body.get('value'), body.get('down'))
    if body.get('command') != 'KEY':
        await rt.publish('pi', 'device.command', body)
    return web.json_response(result)


async def start_test(request):
    rt = request.app[RUNTIME]
    if rt.config['demo']:
        raise ValueError('Demo mode does not run network tests. Restart without --demo to use real checks.')
    if rt.job and not rt.job.done():
        raise ValueError('A test is already running; stop it first')
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError('Test request must be an object')
    kind, options = body.get('kind'), body.get('options', {})
    if kind not in {t['id'] for t in TESTS} or not isinstance(options, dict):
        raise ValueError('Unknown test or invalid options')
    ident = uuid.uuid4().hex[:12]
    rt.job_state = dict(id=ident, kind=kind, state='running')
    # Never persist the receiver token.
    await rt.publish('pi', 'test.started', dict(rt.job_state, options={k: v for k, v in options.items() if k != 'token'}))
    rt.job = asyncio.create_task(rt.execute(ident, kind, options))
    return web.json_response(rt.job_state, status=202)


async def stop_test(request):
    rt = request.app[RUNTIME]
    if rt.job and not rt.job.done():
        rt.job.cancel()
        await asyncio.gather(rt.job, return_exceptions=True)
    return web.json_response(rt.job_state or {'state': 'idle'})


async def settings(request):
    rt = request.app[RUNTIME]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError('Settings must be an object')
    networks = body.get('allowed_networks', rt.config['allowed_networks'])
    if not isinstance(networks, list) or not 1 <= len(networks) <= 32:
        raise ValueError('Enter 1 to 32 target CIDR networks')
    Scope(networks)
    port = body.get('serial_port', rt.config['serial_port'])
    if not isinstance(port, str) or not 1 <= len(port) <= 256 or '\n' in port:
        raise ValueError('Invalid serial port')
    rt.config.update(allowed_networks=networks, serial_port=port)
    temporary = rt.config_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(rt.config, indent=2) + '\n', encoding='utf-8')
    temporary.replace(rt.config_path)
    await rt.publish('pi', 'settings.updated', {'allowed_networks': networks, 'serial_port': port})
    return web.json_response({'saved': True, 'message': 'Serial port changes apply on the next reconnect or service restart.'})


async def baseline(request):
    rt = request.app[RUNTIME]
    body = await request.json()
    kind = body.get('kind') if isinstance(body, dict) else None
    if kind not in ('wifi', 'ble'):
        raise ValueError('Choose wifi or ble')
    data = rt.wifi if kind == 'wifi' else rt.ble
    if not data:
        raise ValueError('Collect observations before saving a baseline')
    rt.store.save_baseline(kind, data)
    rt.alerted_ble.clear()
    await rt.publish('pi', 'baseline.saved', dict(kind=kind, count=len(data)))
    return web.json_response({'saved': True, 'count': len(data)})


async def events(request):
    rt = request.app[RUNTIME]
    return web.json_response(rt.store.events(int(request.query.get('after', 0)), int(request.query.get('limit', 500)), request.query.get('session')))


async def sessions(request):
    return web.json_response(request.app[RUNTIME].store.sessions())


async def export(request):
    rows = request.app[RUNTIME].store.events(limit=20000, session=request.query.get('session'))
    if request.query.get('format') == 'csv':
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['id', 'timestamp', 'session', 'source', 'event', 'data'])
        for row in rows:
            writer.writerow([row['id'], row['ts'], row['session'], row['source'], row['type'], json.dumps(row['data'])])
        return web.Response(text=out.getvalue(), content_type='text/csv', headers={'Content-Disposition': 'attachment; filename="session.csv"'})
    return web.Response(text=json.dumps(rows, indent=2), content_type='application/json', headers={'Content-Disposition': 'attachment; filename="session.json"'})


async def websocket(request):
    rt = request.app[RUNTIME]
    after = int(request.query.get('after', 0))
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024)
    await ws.prepare(request)
    queue = asyncio.Queue(maxsize=256)
    rt.clients.add(queue)
    replay = rt.store.events(after, 20000)
    async def send():
        await ws.send_json({'type': 'replay', 'data': replay})
        if rt.bridge.frame:
            await ws.send_json({'type': 'frame', 'source': 'esp32', 'data': rt.bridge.frame})
        while True:
            item = await queue.get()
            await ws.send_json(item)
            if item['type'] == 'resync':
                await ws.close()
                return
    sender = asyncio.create_task(send())
    try:
        async for message in ws:
            if message.type == WSMsgType.ERROR:
                break
    finally:
        rt.clients.discard(queue)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    return ws


def create_app(config=None, config_path=None):
    cfg = dict(DEFAULTS, **(config or {}))
    if cfg['host'] not in ('127.0.0.1', '::1'):
        raise ValueError('This build binds only to loopback; LAN authentication is not implemented')
    Scope(cfg['allowed_networks'])
    path = Path(config_path or ROOT / 'config.local.json').resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    app = web.Application(middlewares=[local_only], client_max_size=8192)
    rt = Runtime(cfg, path)
    app[RUNTIME] = rt
    async def lifecycle(app):
        await rt.start()
        yield
        await rt.close()
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/api/snapshot', snapshot), web.post('/api/device', command),
                    web.post('/api/tests', start_test), web.post('/api/tests/stop', stop_test),
                    web.post('/api/settings', settings), web.post('/api/baseline', baseline), web.get('/api/events', events),
                    web.get('/api/sessions', sessions), web.get('/api/export', export), web.get('/ws', websocket)])
    async def index(request):
        return web.FileResponse(ROOT / 'static' / 'index.html')
    app.router.add_get('/', index)
    app.router.add_static('/static/', ROOT / 'static', show_index=False)
    return app


def main():
    parser = argparse.ArgumentParser(description='BWifiKill Pi Console')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.local.json')
    parser.add_argument('--demo', action='store_true', help='Explicit simulated device; real network tests disabled')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config.exists() else {}
    if args.demo:
        cfg['demo'] = True
    app = create_app(cfg, args.config)
    runtime = app[RUNTIME]
    web.run_app(app, host=runtime.config['host'], port=runtime.config['port'], print=print)


if __name__ == '__main__':
    main()