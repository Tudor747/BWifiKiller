#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="$("$APP_DIR/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port",8765))' "$APP_DIR/config.local.json")"
URL="http://127.0.0.1:$PORT"
for _ in $(seq 1 60); do
  if curl -fsS "$URL/api/snapshot" > /dev/null; then break; fi
  sleep 1
done
BROWSER="$(command -v chromium || command -v chromium-browser)"
exec "$BROWSER" --kiosk --no-first-run --noerrdialogs --disable-session-crashed-bubble --overscroll-history-navigation=0 --user-data-dir="$APP_DIR/data/chromium" "$URL"