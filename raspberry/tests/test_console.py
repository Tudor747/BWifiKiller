import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer
from console.checks import Scope, ports_from, run_check
from console.receiver import Gate, EchoUDP, tcp_handler
from console.server import create_app, RUNTIME
from console.serial_link import SerialBridge
from console.storage import Store

class ScopeTests(unittest.TestCase):
    def test_scope_and_ports(self):
        scope = Scope(['127.0.0.0/8'])
        self.assertEqual(scope.check('127.0.0.1'), '127.0.0.1')
        for value in ['192.0.2.1', '224.0.0.1', '0.0.0.0']:
            with self.assertRaises(ValueError): scope.check(value)
        for value in ['0','65536','80;whoami',','.join(map(str,range(1,18)))]:
            with self.assertRaises(ValueError): ports_from(value)
        self.assertEqual(ports_from('80,443,80'),[80,443])
    def test_store_restart_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.db'
            first=Store(path); a=first.add('pi','start',{'text':'hello'}); first.close()
            second=Store(path); b=second.add('esp32','boot',{'text':'world'})
            self.assertNotEqual(a['session'],b['session'])
            self.assertEqual(second.events(a['id'])[0]['id'],b['id'])
            self.assertEqual(len(second.sessions()),2); second.close()
    def test_receiver_validation(self):
        gate=Gate('test-token')
        for data in [b'BW1 wrong hello\n',b'BW1 test-token '+b'a'*300+b'\n']:
            self.assertFalse(gate.accept(data,('127.0.0.1',1)))
        self.assertTrue(gate.accept(b'BW1 test-token hello\n',('127.0.0.1',1)))

class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.scope=Scope(['127.0.0.0/8']); self.events=[]
    async def emit(self,kind,data): self.events.append((kind,data))
    async def test_tcp_service_and_segmentation(self):
        async def handle(reader,writer):
            writer.write(b'LAB hello\n'); await writer.drain(); writer.close(); await writer.wait_closed()
        server=await asyncio.start_server(handle,'127.0.0.1',0)
        async with server:
            options={'target':'127.0.0.1','ports':str(server.sockets[0].getsockname()[1])}
            result=await run_check('services',options,self.scope,self.emit)
            self.assertIn('LAB',result['results'][0]['greeting'])
            result=await run_check('segmentation',dict(options,expected='allow'),self.scope,self.emit)
            self.assertTrue(result['results'][0]['matches'])
    async def test_http_reports_redirect(self):
        async def handle(reader,writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 302 Found\r\nLocation: http://192.0.2.1\r\nContent-Length: 0\r\n\r\n')
            await writer.drain(); writer.close(); await writer.wait_closed()
        server=await asyncio.start_server(handle,'127.0.0.1',0)
        async with server:
            result=await run_check('http',{'target':f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'},self.scope,self.emit)
            self.assertIn('302',result['status'])
    async def test_real_tcp_udp_receivers(self):
        gate=Gate('test-token'); loop=asyncio.get_running_loop()
        transport,_=await loop.create_datagram_endpoint(lambda:EchoUDP(gate),local_addr=('127.0.0.1',0))
        server=await asyncio.start_server(tcp_handler(gate),'127.0.0.1',0,limit=512)
        try:
            for kind,port in [('udp_echo',transport.get_extra_info('sockname')[1]),('tcp_echo',server.sockets[0].getsockname()[1])]:
                result=await run_check(kind,{'target':'127.0.0.1','ports':str(port),'token':'test-token','count':3},self.scope,self.emit)
                self.assertEqual(result['received'],3); self.assertEqual(result['loss_percent'],0)
        finally:
            transport.close(); server.close(); await server.wait_closed()
    async def test_test_limits(self):
        with self.assertRaises(ValueError): await run_check('discovery',{'target':'127.0.0.0/24'},self.scope,self.emit)
        with self.assertRaises(ValueError): await run_check('udp_echo',{'target':'127.0.0.1','ports':'9877','count':101,'token':'test-token'},self.scope,self.emit)

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_ack_reboot(self):
        bridge=SerialBridge({},AsyncMock())
        await bridge._receive(b'ESP boot log'); self.assertFalse(bridge.ready)
        await bridge._receive(json.dumps({'v':1,'boot':1,'type':'hello','data':{'protocol':1,'firmware':'test'}}))
        self.assertTrue(bridge.ready)
        future=asyncio.get_running_loop().create_future(); bridge.pending[3]=future
        await bridge._receive(json.dumps({'v':1,'boot':1,'type':'ack','data':{'id':3,'status':'ok'}}))
        self.assertEqual((await future)['status'],'ok')
        with self.assertRaises(ValueError): await bridge.command('START',7)
        await bridge._receive(json.dumps({'v':1,'boot':2,'type':'boot','data':{}})); self.assertFalse(bridge.ready)

class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app({'data_dir':self.tmp.name},Path(self.tmp.name)/'config.json')
        self.rt=self.app[RUNTIME]; self.rt.bridge.start=AsyncMock()
        self.client=TestClient(TestServer(self.app)); await self.client.start_server()
    async def asyncTearDown(self): await self.client.close(); self.tmp.cleanup()
    async def test_static_catalog_and_origin_checks(self):
        response=await self.client.get('/'); self.assertEqual(response.status,200)
        self.assertIn('FIELD CONSOLE',await response.text())
        snap=await(await self.client.get('/api/snapshot')).json()
        self.assertEqual(len(snap['catalog']),30); self.assertEqual(sum(x['supported'] for x in snap['catalog']),18)
        response=await self.client.post('/api/tests',json={},headers={'Origin':'https://unrelated.example'}); self.assertEqual(response.status,403)
        response=await self.client.get('/api/snapshot',headers={'Host':'unrelated.example'}); self.assertEqual(response.status,403)
        response=await self.client.post('/api/device',json={'command':'START','value':7}); self.assertEqual(response.status,400)
    async def test_websocket_replay_and_live_event(self):
        old=await self.rt.publish('pi','test.note',{'value':1})
        async with self.client.ws_connect('/ws?after=0') as ws:
            replay=await ws.receive_json(); self.assertTrue(any(x['id']==old['id'] for x in replay['data']))
            new=await self.rt.publish('esp32','wifi.network',{'ssid':'Lab','bssid':'test'})
            live=await ws.receive_json(); self.assertEqual(new['id'],live['id'])
        rows=await(await self.client.get('/api/events?after='+str(old['id']))).json(); self.assertEqual(rows[0]['id'],new['id'])
    async def test_cancellation_and_token_redaction(self):
        async def wait(*args): await asyncio.sleep(30)
        with patch('console.server.run_check',wait):
            response=await self.client.post('/api/tests',json={'kind':'tcp_echo','options':{'target':'127.0.0.1','token':'secret-value'}})
            self.assertEqual(response.status,202)
            duplicate=await self.client.post('/api/tests',json={'kind':'tcp','options':{}}); self.assertEqual(duplicate.status,400)
            stopped=await(await self.client.post('/api/tests/stop',json={})).json(); self.assertEqual(stopped['state'],'cancelled')
        rows=self.rt.store.events(); self.assertNotIn('secret-value',json.dumps(rows)); self.assertEqual(sum(x['type']=='test.finished' for x in rows),1)
    async def test_baseline_changes_and_false_positive_notes(self):
        await self.rt.publish('esp32','wifi.network',{'ssid':'Lab','bssid':'a','channel':1,'security':3})
        response=await self.client.post('/api/baseline',json={'kind':'wifi'})
        self.assertEqual(response.status,200)
        await self.rt.publish('esp32','wifi.scan',{'state':'scanning'})
        await self.rt.publish('esp32','wifi.network',{'ssid':'New','bssid':'b','channel':6,'security':3})
        await self.rt.publish('esp32','wifi.scan',{'state':'complete'})
        rows=self.rt.store.events()
        self.assertTrue(any(x['type']=='alert.wifi.new' for x in rows))
        missing=next(x for x in rows if x['type']=='alert.wifi.missing')
        self.assertIn('does not prove',missing['data']['note'])
    async def test_settings_and_demo(self):
        response=await self.client.post('/api/settings',json={'allowed_networks':['127.0.0.0/8','192.168.10.0/24']}); self.assertEqual(response.status,200)
        saved=json.loads((Path(self.tmp.name)/'config.json').read_text()); self.assertEqual(len(saved['allowed_networks']),2)
        self.rt.config['demo']=True
        response=await self.client.post('/api/tests',json={'kind':'tcp','options':{}}); self.assertEqual(response.status,400)

if __name__=='__main__': unittest.main()