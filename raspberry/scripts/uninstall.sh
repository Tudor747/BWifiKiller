#!/usr/bin/env bash
set -euo pipefail
sudo systemctl disable --now bwifikill-console.service
sudo rm -f -- /etc/systemd/system/bwifikill-console.service
sudo systemctl daemon-reload
rm -f -- "$HOME/.config/autostart/bwifikill-console.desktop"
echo 'Service and desktop autostart removed. Source, configuration and session data retained.'