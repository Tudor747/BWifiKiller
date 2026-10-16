"""Local browser smoke check using a separately launched headless Chromium."""
import asyncio
import base64
import json
from pathlib import Path
import aiohttp

async def main():
    async with aiohttp.ClientSession() as client:
        async with client.get('http://127.0.0.1:9223/json/list') as r:
            tabs=await r.json()
        tab=next(t for t in tabs if t['type']=='page')
        async with client.ws_connect(tab['webSocketDebuggerUrl']) as ws:
            counter=0
            errors=[]
            async def command(method,params=None):
                nonlocal counter
                counter+=1
                await ws.send_json({'id':counter,'method':method,'params':params or {}})
                while True:
                    msg=await ws.receive_json(timeout=15)
                    if msg.get('method')=='Runtime.exceptionThrown': errors.append(msg['params'])
                    if msg.get('id')==counter:
                        if 'error' in msg: raise RuntimeError(msg['error'])
                        return msg.get('result',{})
            async def evaluate(expression):
                result=await command('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
                if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
                return result.get('result',{}).get('value')
            await command('Runtime.enable')
            await command('Page.enable')
            await command('Emulation.setDeviceMetricsOverride',{'width':1024,'height':700,'deviceScaleFactor':1,'mobile':False})
            await command('Page.navigate',{'url':'http://127.0.0.1:8765'})
            for _ in range(40):
                if await evaluate("document.querySelectorAll('.tile').length === 2"): break
                await asyncio.sleep(.1)
            assert await evaluate("document.querySelectorAll('.tile').length") == 2
            screen=await command('Page.captureScreenshot',{'format':'png'})
            Path('artifacts/home-1024.png').write_bytes(base64.b64decode(screen['data']))
            await evaluate("go('esp'); go('group:Wi-Fi'); go('device:0')")
            assert await evaluate("!!document.querySelector('#oled')")
            await evaluate("api('/api/device',{command:'START',value:0})")
            await asyncio.sleep(2.2)
            await evaluate("go('dashboard')")
            assert await evaluate("document.querySelector('#wifi-table').textContent.includes('SIMULATED Lab')")
            for route in ['blue','red','test:tcp','test:udp_echo','test:segmentation','logs','settings','reports','alerts']:
                await evaluate('go('+json.dumps(route)+')')
                assert await evaluate("document.querySelector('#main').textContent.length > 20")
            await evaluate("go('settings'); document.querySelector('input').focus(); document.querySelector('#keyboard-toggle').click(); typeKey('x')")
            assert await evaluate("!document.querySelector('#keyboard').hidden")
            await evaluate("go('home')")
            await command('Emulation.setDeviceMetricsOverride',{'width':480,'height':320,'deviceScaleFactor':1,'mobile':False})
            await asyncio.sleep(.2)
            assert await evaluate('document.documentElement.scrollWidth <= 480')
            assert await evaluate("document.querySelector('.pager').getBoundingClientRect().bottom <= innerHeight"), 'Menu paging must fit on the small screen'
            await evaluate("physical('DOWN',true); physical('OK',true)")
            assert await evaluate("state.route === 'blue'")
            await evaluate("go('home')")
            screen=await command('Page.captureScreenshot',{'format':'png'})
            Path('artifacts/home-480.png').write_bytes(base64.b64decode(screen['data']))
            assert not errors, errors
            print('Browser smoke passed: two-tile menu, device controls, streamed demo scan, dashboard, team menus, forms, keyboard, logs, reports, 480px layout.')
            print('JavaScript exceptions:',len(errors))

if __name__=='__main__': asyncio.run(main())