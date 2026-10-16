# Build validation — 2026-10-16

These checks were performed in the Windows development workspace. No Raspberry Pi or ESP32 was connected or flashed.

| Check | Result |
| --- | --- |
| PlatformIO `pi_companion` build | Passed; 73,736 bytes static RAM, 1,579,721 bytes application flash |
| PlatformIO original `esp32dev` build | Passed; conditional companion changes preserve compilation |
| Python unittest suite | 13 tests passed |
| Real loopback TCP/UDP receiver exchange | Passed as part of the suite |
| Scope/limits, cancellation, token redaction | Passed |
| SQLite replay and WebSocket live delivery | Passed |
| Baseline new/missing observation alerts | Passed |
| JavaScript syntax | Passed |
| Headless Chromium browser checks | Passed; zero JavaScript exceptions |
| Browser views | Home, ESP32 categories/control page, streamed simulated Wi-Fi results, dashboard, Blue/Red menus, test forms, alerts, settings, logs, reports, on-screen keyboard |
| Viewport checks | 1024×700 and 480×320; two side-by-side tiles and paging controls fit the small viewport |
| Physical-button navigation logic | UP/DOWN/OK browser event path exercised; no physical GPIO hardware tested |
| Linux shell scripts | Bash syntax checks passed; installer was not run on a Pi |
| Full flash image | Merged from the companion bootloader, partition table, boot-app image, and application for offset 0x0 |
| Release archive | ZIP integrity and per-file SHA-256 checks verified; local config, runtime data, browser profiles, and development environments excluded |

Hardware verification still required: touchscreen driver/rotation, GPIO pin ownership and switch wiring, Pi startup/autologin, real USB reconnects and resets, OLED display, BLE pairing/transitions, and RF measurements with installed modules.

The browser preview is explicitly simulated. It is not evidence of radio reception. Most original ESP32 views are streamed OLED frames; only the companion Wi-Fi Scanner and BLE Scanner publish structured device inventories to the Pi dashboard.

The app lists 30 original entries: 18 companion tools, 3 Pi replacements, and 9 reference-only entries (the eight restricted entries plus Slave Control). The paired media remote is a limited replacement for the original BT Remote. See README.md for the support matrix.