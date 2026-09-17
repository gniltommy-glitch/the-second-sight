#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(id -un)}"
if [[ "$APP_USER" == root ]]; then
  echo 'Run as the normal Pi account: bash scripts/install_service.sh' >&2
  exit 1
fi
if [[ ! "$APP_ROOT" =~ ^/[a-zA-Z0-9_./-]+$ || ! "$APP_USER" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo 'Use an installation path without spaces or special characters.' >&2
  exit 1
fi
"$APP_ROOT/.venv/bin/python" "$APP_ROOT/main.py" --config "$APP_ROOT/config/pi.json" --check
APP_SERVICE="$(mktemp)"
trap 'rm -f -- "$APP_SERVICE"' EXIT
sed -e "s|@ROOT@|$APP_ROOT|g" -e "s|@USER@|$APP_USER|g" \
  "$APP_ROOT/deploy/second-sight.service.in" > "$APP_SERVICE"
sudo install -m 0644 "$APP_SERVICE" /etc/systemd/system/second-sight.service
sudo systemctl daemon-reload
sudo systemctl enable --now second-sight.service
sudo systemctl status second-sight.service --no-pager
