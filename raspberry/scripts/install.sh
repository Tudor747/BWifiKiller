#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -eq 0 ]]; then
  echo 'Run this script as your desktop user. It invokes sudo for system setup.' >&2
  exit 1
fi
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$APP_DIR" =~ [[:space:]\%\"] ]]; then
  echo 'Put this checkout in a path without spaces, percent signs, or quotes.' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-gpiozero python3-lgpio chromium curl iputils-ping
python3 -m venv --system-site-packages "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
if [[ ! -f "$APP_DIR/config.local.json" ]]; then
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.local.json"
fi
mkdir -p "$APP_DIR/data" "$HOME/.config/autostart"
sudo usermod -aG dialout,gpio "$(id -un)"
unit="$(mktemp)"
trap 'rm -f -- "$unit"' EXIT
cat > "$unit" <<EOF
[Unit]
Description=BWifiKill local Pi console
After=network.target

[Service]
Type=simple
User=$(id -un)
SupplementaryGroups=dialout gpio
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m console.server --config $APP_DIR/config.local.json
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 0644 "$unit" /etc/systemd/system/bwifikill-console.service
chmod +x "$APP_DIR/scripts/kiosk.sh"
cat > "$HOME/.config/autostart/bwifikill-console.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BWifiKill Field Console
Exec=$APP_DIR/scripts/kiosk.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bwifikill-console.service
echo 'Installed. Open http://127.0.0.1:8765 or run scripts/kiosk.sh from the desktop.'
echo 'Enable desktop autologin in Raspberry Pi OS if you want kiosk startup after boot.'
echo 'GPIO remains disabled until you configure pins appropriate for your screen.'