import asyncio
import json
import time
from .catalog import SUPPORTED


class SerialBridge:
    def __init__(self, config, publish):
        self.config, self.publish = config, publish
        self.ready = False
        self.port = None
        self.state = {'connected': False, 'message': 'Connecting'}
        self.frame = None
        self.pending = {}
        self.next_id = 1
        self.lock = asyncio.Lock()
        self.io_lock = asyncio.Lock()
        self.task = None
        self.last_boot = None

    async def start(self):
        self.task = asyncio.create_task(self._demo() if self.config.get('demo') else self._run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self._disconnect()

    def _disconnect(self):
        self.ready = False
        if self.port:
            self.port.close()
            self.port = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError('ESP32 disconnected'))
        self.pending.clear()

    def _open(self):
        import serial
        from serial.tools.list_ports import comports
        name = self.config.get('serial_port', 'auto')
        if name == 'auto':
            candidates = [p.device for p in comports() if p.vid in (0x10c4, 0x1a86, 0x0403, 0x303a)]
            if len(candidates) != 1:
                raise ConnectionError('Connect one ESP32, or set serial_port to its /dev/serial/by-id path')
            name = candidates[0]
        port = serial.Serial(port=None, baudrate=self.config.get('baud', 115200), timeout=.05, write_timeout=1)
        port.dtr = False
        port.rts = False
        port.port = name
        port.open()
        return port

    async def _receive(self, raw):
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return  # ESP boot ROM output is not part of this protocol.
        if not isinstance(message, dict) or message.get('v') != 1 or not isinstance(message.get('data'), dict):
            return
        kind, data = message.get('type'), message['data']
        if not isinstance(kind, str):
            return
        boot = message.get('boot')
        if self.last_boot is not None and boot != self.last_boot:
            self.ready = False
            self.state = dict(connected=False, message='ESP32 restarted; reconnecting')
            await self.publish('esp32', 'connection', self.state)
        self.last_boot = boot
        if kind == 'hello':
            if data.get('protocol') != 1:
                return
            self.ready = True
            self.state = dict(connected=True, port=self.port.port if self.port else 'demo', firmware=data.get('firmware'), app=-1)
            await self.publish('esp32', 'connection', self.state)
        elif kind == 'ack':
            future = self.pending.get(data.get('id'))
            if future and not future.done():
                future.set_result(data)
            return
        elif kind == 'status':
            self.state.update(data)
        elif kind == 'frame':
            if isinstance(data.get('base64'), str) and len(data['base64']) <= 1400:
                self.frame = data
                await self.publish('esp32', 'frame', data, persist=False)
            return
        else:
            data = dict(data, device_boot=boot, device_sequence=message.get('seq'), uptime_ms=message.get('uptime_ms'))
        await self.publish('esp32', kind, data)

    async def _write(self, payload):
        async with self.io_lock:
            if self.port is None:
                raise ConnectionError('ESP32 disconnected')
            try:
                await asyncio.to_thread(self.port.write, payload)
            except OSError as exc:
                raise ConnectionError('USB write failed') from exc

    async def _run(self):
        while True:
            try:
                self.port = await asyncio.to_thread(self._open)
                await asyncio.sleep(1.5)
                buffer = b''
                sent, received = 0, time.monotonic()
                while True:
                    now = time.monotonic()
                    if now - sent > 1:
                        verb = 'STATUS' if self.ready else 'HELLO'
                        await self._write(f'0 {verb}\n'.encode())
                        sent = now
                    chunk = await asyncio.to_thread(self.port.read, max(1, min(self.port.in_waiting, 4096)))
                    if chunk:
                        received = now
                        buffer += chunk
                        while b'\n' in buffer:
                            raw, buffer = buffer.split(b'\n', 1)
                            if len(raw) <= 6000:
                                await self._receive(raw)
                        if len(buffer) > 6000:
                            buffer = b''
                    if now - received > 8:
                        raise ConnectionError('ESP32 did not respond; check companion firmware and baud rate')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._disconnect()
                state = dict(connected=False, message=str(exc))
                if self.state != state:
                    self.state = state
                    await self.publish('esp32', 'connection', state)
                await asyncio.sleep(2)

    async def command(self, verb, value=None, down=None):
        if verb == 'START':
            if type(value) is not int or value not in SUPPORTED:
                raise ValueError('This ESP32 tool is not supported by the companion build')
            line = f'START {value}'
        elif verb == 'KEY':
            if value not in ('UP', 'DOWN', 'OK', 'BACK', 'AUX') or type(down) is not bool:
                raise ValueError('Invalid key')
            line = f'KEY {value} {int(down)}'
        elif verb in ('STOP', 'STATUS'):
            line = verb
        else:
            raise ValueError('Unsupported command')
        if not self.ready:
            raise ConnectionError('ESP32 is not connected with companion firmware')
        if self.config.get('demo'):
            if verb == 'START':
                self.state['app'] = value
            if verb == 'STOP':
                self.state['app'] = -1
            await self.publish('esp32', 'status', dict(self.state, demo=True))
            return {'status': 'ok', 'demo': True}
        async with self.lock:
            ident = self.next_id
            self.next_id += 1
            future = asyncio.get_running_loop().create_future()
            self.pending[ident] = future
            try:
                await self._write(f'{ident} {line}\n'.encode())
                result = await asyncio.wait_for(future, 5)
                if result['status'] != 'ok':
                    raise ValueError('ESP32: ' + result['status'])
                return result
            finally:
                self.pending.pop(ident, None)

    async def _demo(self):
        self.ready = True
        self.state = dict(connected=True, port='SIMULATED', firmware='demo', app=-1, heap=160000, demo=True)
        await self.publish('esp32', 'connection', self.state)
        while True:
            await asyncio.sleep(2)
            await self.publish('esp32', 'status', dict(self.state))
            if self.state.get('app') == 0:
                await self.publish('esp32', 'wifi.network', dict(ssid='SIMULATED Lab', bssid='02:00:00:00:00:01', channel=6, rssi=-58, security=3, demo=True))