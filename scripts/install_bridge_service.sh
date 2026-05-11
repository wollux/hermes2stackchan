#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${H2S_INSTALL_DIR:-/opt/hermes2stackchan}"
SERVICE_NAME="${H2S_SERVICE_NAME:-hermes2stackchan.service}"
PYTHON_BIN="${PYTHON:-python3}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo, for example: sudo H2S_INSTALL_DIR=$INSTALL_DIR $0" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude 'Ressourcen/' \
  --exclude 'firmware/build/' \
  "$ROOT_DIR/" "$INSTALL_DIR/"

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  echo "Created $INSTALL_DIR/.env from .env.example. Edit it before starting the service."
fi

"$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"

install -m 0644 "$INSTALL_DIR/systemd/hermes2stackchan.service.example" "/etc/systemd/system/$SERVICE_NAME"
sed -i "s#/opt/hermes2stackchan#$INSTALL_DIR#g" "/etc/systemd/system/$SERVICE_NAME"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "Installed $SERVICE_NAME."
echo "Edit $INSTALL_DIR/.env, then start with: sudo systemctl start $SERVICE_NAME"
echo "Logs: journalctl -u $SERVICE_NAME -f"
