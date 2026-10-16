# Raspberry Pi Field Console

A local touchscreen application for Raspberry Pi 5, with a USB-connected ESP32 companion. The Pi runs the dashboard, TCP/UDP checks, GPIO controls, and session storage. The ESP32 retains its 128×64 OLED and runs wireless tools. No additional antennas or radios are required for this build.

## What is included

- A two-column, one-row menu: two large selectable tiles per page.
- Touch mode, an on-screen text keyboard, keyboard navigation, and configurable UP/DOWN/OK/BACK/AUX Pi buttons.
- All 30 original ESP32 menu entries, with support status shown explicitly.
- Eighteen USB-controllable ESP32 tools, including the five games inside Arcade.
- A live OLED viewer at up to four frames/second, structured Wi-Fi/BLE scanner results, automatic reconnects, and device status.
- Blue Team: ping, TCP reachability, DNS responses, HTTP health, TLS certificate validation, UDP echo diagnostics, and baseline observation alerts.
- Red Team: bounded TCP host discovery, service greetings, expected allow/deny reachability checks, and paced TCP/UDP tests against a cooperating receiver.
- SQLite session logs, WebSocket updates/replay, and JSON/CSV exports.
- Raspberry Pi OS installation, systemd service, desktop kiosk startup, and a separate receiver program.

The interface runs at `http://127.0.0.1:8765`. ESP32 data travels through USB, not Wi-Fi. The local WebSocket endpoint is `/ws`. This version does not expose a LAN or internet dashboard.

## Hardware and screen

Target: Raspberry Pi 5 (8 GB), Raspberry Pi OS Desktop, Python 3.11 or newer, a working touchscreen, and an ESP32 DevKit/WROOM with its existing OLED and nRF24 wiring. The firmware target is the classic ESP32 with 4 MB flash, not an ESP32-S3/C3.

Connect an ordinary **USB data cable** from a Pi USB-A port to the ESP32 USB connector. Keep the ESP32 OLED and both nRF24 modules wired as documented in the root README. Its old five physical buttons can be disconnected; the companion build never reads their pins.

The display model is currently unknown. Configure the screen using its manufacturer's Pi 5 instructions first. This installer does not change display overlays, rotation, SPI settings, or touch calibration. It adapts the menu down to a 480×320 viewport; small screens may need scrolling in diagnostic forms.

GPIO controls are **disabled by default**. A screen mounted on the 40-pin header can occupy pins that would otherwise be available for buttons. Do not enable button GPIOs until you identify the display and its occupied pins. Touchscreen and keyboard navigation work without GPIO configuration.

Use a suitable Pi 5 power supply. For the initial build, power the ESP32 from the Pi USB port; disconnect a separate ESP32 battery/VIN supply unless your board supports simultaneous supplies. Do not power nRF24 modules from 5 V.

## Install on the Pi

Copy this project onto the Pi, for example under `~/BWifiKill`. Do not use a path containing spaces. From the `raspberry` directory:

```bash
bash scripts/install.sh
```

The installer installs OS packages and Python dependencies, creates a virtual environment, adds your user to `dialout` and `gpio`, installs `bwifikill-console.service`, and creates a desktop autostart entry. Existing `config.local.json` and stored sessions are retained. Internet access is needed for this installation; runtime assets are local.

Open `http://127.0.0.1:8765` or, from a desktop terminal:

```bash
bash scripts/kiosk.sh
```

For boot-to-kiosk behavior, enable **desktop autologin** in Raspberry Pi OS. The service starts without a desktop, but the browser needs a logged-in graphical session. Log out and back in after installing if running tools manually and newly added group permissions have not taken effect.

Check service status:

```bash
systemctl status bwifikill-console
journalctl -u bwifikill-console -n 60
```

The kiosk supports the default loopback IPv4 host. Leave `host` as `127.0.0.1` when using the supplied kiosk script.

## Build and flash the ESP32

The original standalone environment remains `esp32dev`. The new environment is `pi_companion`:

```bash
# From the repository root, on a computer with PlatformIO installed:
pio run -e pi_companion
pio run -e pi_companion -t upload --upload-port YOUR_SERIAL_PORT
```

Stop the Pi console service before flashing a board attached to the Pi, since the service owns its serial port:

```bash
sudo systemctl stop bwifikill-console
# Flash the ESP32, then:
sudo systemctl start bwifikill-console
```

A release archive may include `firmware/pi-companion-full.bin`. It is merged for offset **0x0** on the classic ESP32 with 4 MB flash. Use esptool's `write_flash 0x0` with that full image. The PlatformIO `firmware.bin` alone is an application image for **0x10000**, not 0x0. Installing a different partition layout can make previous stored ESP32 data inaccessible; export anything needed before flashing. No device is flashed by the installer or packaging script.

After startup the OLED says `PI COMPANION`. Within a few seconds the Pi should show **ESP32 · USB connected**. If multiple USB serial boards are attached, set **Settings → Serial device** to the ESP32's stable `/dev/serial/by-id/...` path. Auto mode intentionally refuses to choose among multiple matching devices.

## ESP32 feature support

| Original entry | Companion behavior |
| --- | --- |
| Wi-Fi Scanner | New asynchronous scanner; up to 30 structured records and OLED browsing. AUX rescans. It does not join open networks. |
| Wi-Fi Radar, Channel Scan | Existing tool and OLED view over USB. |
| RF Analyzer, RF Heatmap, Channel Advisor | Existing nRF tools and OLED view over USB. |
| Packet Monitor, Sentinel | Existing monitoring tools and OLED view over USB. Sentinel's alert is a heuristic, not proof of an attack. |
| BLE Scanner | Existing OLED tool plus structured names, addresses, and RSSI. |
| BLE Analyzer, BT Spectrum | Existing OLED tools over USB. RF charts are carrier-detection visualizations, not calibrated measurements. |
| nRF Link, nRF Chat, BT/Wi-Fi Coexistence, Dual nRF Scope | Existing OLED tools over USB. Link/chat require a compatible peer. |
| BT Remote | Paired media controls only: volume up/down, play/pause, next track. Legacy keystroke scripts are not included. |
| Arcade, About | Existing OLED screens; Arcade contains Snake, Pong, Flappy, Invaders, and Dino. |
| IP Scanner | Routes to the Pi's bounded TCP discovery tool. |
| Web Dashboard | Routes to the new Pi dashboard. |
| View Logs | Routes to Pi diagnostic/session logs. Existing portal submissions are not imported. |
| Slave Control | Reference entry only; no legacy slave controller in this build. |
| Channel Jammer, Full Sweep, BT Jammer, Beacon Spam, BLE Spam, Hybrid Mode, Evil Portal, Deauther | Reference entries only. They have no remote handler and are not compiled into the companion firmware. |

Stop the current ESP32 tool before launching another. UP/DOWN/OK/BACK/AUX on its control page act on the ESP32. The page's **Stop tool** button always closes the tool; BACK may first close an internal subview. Leaving the page does not automatically stop a measurement. A seven-second loss of Pi heartbeats does stop the active tool. Remote held keys expire after 700 ms without refresh.

Stopping a BLE tool briefly restarts the ESP32 to reset its BLE stack; the Pi reconnects automatically. This avoids stale BLE service objects in the pinned Arduino/BLE Keyboard libraries. That hardware transition still needs validation on the actual board.

## Physical Pi buttons

Edit `config.local.json` only after identifying your screen's pin usage. Configure `gpio.pins` with exactly these five names using **BCM numbering**, not physical header numbers:

```json
{
  "enabled": false,
  "pins": {},
  "reserved_pins": [],
  "chip": null
}
```

Replace `pins` with your confirmed free GPIO assignments, list screen GPIOs in `reserved_pins`, and set `enabled` to `true`. Each switch connects its assigned GPIO to **GND**. The program uses pull-ups and debounce. It rejects duplicate pins and pins listed as reserved. It does not discover screen pin usage automatically.

`gpiozero` with the `lgpio` backend is used for Pi 5. Leave `chip` null for its automatic selection; a specific chip can be configured if needed for your OS/kernel. Restart the service after changing GPIO settings.

On menus: UP/DOWN selects entries across pages; OK opens; BACK returns; AUX opens the dashboard. On an ESP32 control page, all five keys are forwarded to the ESP32. On forms, UP/DOWN moves focus and OK activates; touchscreen plus the on-screen keyboard handles text entry. Keyboard equivalents: arrow up/down, Enter, Escape, F2.

## Blue Team and Red Team checks

The default target scope is loopback only (`127.0.0.0/8`). In Settings, add the exact CIDR networks you intend to test, such as your lab subnet. Hostnames are resolved once, all returned IPs are checked against the scope, and the chosen IP is pinned for the actual connection. HTTP redirects are reported rather than followed.

- One Pi test runs at a time; each has a 90-second overall timeout and a Stop button.
- TCP checks accept at most 16 selected ports. Service inspection reads at most 256 greeting bytes and does not probe vulnerabilities.
- Discovery accepts an IPv4 `/27` or smaller range and at most four selected TCP ports. A host without an open selected port is not necessarily offline.
- Segmentation checks compare connectivity with an expected allow/deny result. A timeout alone does not prove a firewall blocked traffic; the target may have no listener or an unavailable route.
- DNS sends a valid UDP A query to the selected resolver and reports response code/count/latency. It is not a generic UDP port scan.
- TLS checks use normal trust and hostname verification. Self-signed/untrusted certificates produce a failed result rather than being silently accepted.
- Echo checks require a matching receiver before proceeding. They send at most 100 small messages, paced to at most ten per second. They measure round-trip latency and loss; reported RTT variation is not one-way jitter or a throughput measurement.

### Cooperating TCP/UDP receiver

On your test endpoint, copy the `raspberry` directory. The receiver uses only Python's standard library. Create a token file and run it, replacing the example address with the endpoint's actual LAN address:

```bash
python3 -c "import secrets,pathlib; pathlib.Path('receiver.token').write_text(secrets.token_hex(16))"
python3 -m console.receiver --host 192.168.1.50 --port 9877 --token-file receiver.token
```

Allow TCP/UDP port 9877 on that endpoint's local firewall if required. In the Pi's echo-test form, enter the endpoint address, port, token from the file, and message count. The receiver binds to loopback unless you explicitly choose an address. It validates the token, caps message size/rate, and echoes exactly the received bytes. The console never saves the token in its event database.

### Observation alerts

Collect a Wi-Fi or BLE scan, then open **Blue Team → Observation alerts** and save the corresponding baseline. Baselines survive service restarts. Successful subsequent Wi-Fi scans produce new/missing/changed AP observations. BLE scanning reports newly observed addresses relative to its baseline. BLE address randomization and scan visibility can produce apparent changes; alerts do not establish malicious activity. RF/Sentinel activity remains available through the live OLED tools.

## Logs and protocol

SQLite stores up to approximately 20,000 events in `data/console.sqlite3`; older events are pruned automatically. Export sessions you want to retain. Scan records, connection changes, commands, alerts, and test progress/results are saved. Status heartbeats and display frames are transient. Each service start creates a new session; a test interrupted by a process crash has a start event without a terminal result in that previous session.

USB uses 115200 baud, 8N1. Pi commands are bounded ASCII lines:

```text
1 HELLO
2 START 0
3 KEY DOWN 1
4 KEY DOWN 0
5 STATUS
6 STOP
```

Responses/events are newline-delimited JSON with `v`, `boot`, `seq`, `uptime_ms`, `type`, and `data`. Replies correlate using `data.id`. The Pi performs a version handshake, applies command timeouts, and reconnects after cable loss/reboot. Binary boot-ROM output is ignored. A small FreeRTOS queue moves BLE observations to the firmware main loop before serial output.

The Pi timestamps and stores diagnostic events before WebSocket delivery. `/ws?after=EVENT_ID` replays retained events after a cursor. Slow clients reconnect and replay. OLED frames are base64-encoded 128×64 U8g2 vertical-LSB buffers. The browser is an additional renderer, not the owner of ESP32 radio state.

## Development and validation

On any computer with Python 3.11+:

```bash
python -m venv .venv
# Activate the environment for your shell, then:
python -m pip install -r requirements.txt
python -m console.server --demo
```

Demo mode is explicitly labeled and disables real network tests. Do not enable `demo` in the installed configuration when using actual hardware.

```bash
python -m unittest discover -s tests -v
node --check static/app.js
```

`tests/test_console.py` exercises real loopback TCP/UDP receivers, HTTP redirect handling, scope checks, cancellation, secret redaction, baseline alerts, SQLite replay, WebSocket delivery, and protocol handling. `scripts/browser_smoke.py` can inspect a separately launched headless Chromium with a debugging port at 9223 while the demo service runs. Its screenshots go into `artifacts/`.

Firmware compilation and desktop browser/service checks do not verify the actual Pi display, GPIO, USB serial hardware, BLE pairing, or radio measurements. Validate those on the assembled hardware before relying on readings.

## Maintenance

```bash
sudo systemctl restart bwifikill-console
# Remove only the service and autostart; preserve code and stored data:
bash scripts/uninstall.sh
```

The installer does not overwrite display configuration or flash the ESP32. To restore the original handheld firmware, build/upload the `esp32dev` environment and reconnect its original buttons as needed.

Implementation references: [gpiozero Pi 5 backend](https://gpiozero.readthedocs.io/en/stable/api_pins.html), [pySerial timeouts](https://pyserial.readthedocs.io/en/stable/shortintro.html), [aiohttp WebSocket server](https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.WebSocketResponse).
## Release packaging

After building and merging the companion image into `raspberry/artifacts/pi-companion-full.bin`, run this from `raspberry`:

```bash
python scripts/package.py
```

It produces `artifacts/bwifikill-pi-console.zip`, an adjacent archive checksum, and a per-file checksum manifest inside the ZIP. The archive contains source, installer, tests, documentation, and `firmware/pi-companion-full.bin`; it excludes local configuration, session data, browser profiles, and virtual environments. See [VALIDATION.md](VALIDATION.md) for the checks actually performed and the remaining hardware checks.