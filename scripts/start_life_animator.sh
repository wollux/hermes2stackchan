#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
LOG_FILE="$ROOT_DIR/life-animator.log"
LABEL="de.wollux.hermes2stackchan.life-animator.$PAIR_ID"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x /opt/homebrew/bin/python3.11 ]]; then
    PYTHON=/opt/homebrew/bin/python3.11
  else
    PYTHON=python3
  fi
fi

cd "$ROOT_DIR"
if [[ "$(uname -s)" == "Darwin" ]]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>-u</string>
    <string>-m</string>
    <string>bridge.hermes2stackchan_bridge</string>
    <string>--env</string>
    <string>.env</string>
    <string>animate-life</string>
    <string>--pair</string>
    <string>$PAIR_ID</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$LOG_FILE</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  echo "life animator launch agent started: label=$LABEL log=$LOG_FILE"
  exit 0
fi

PYTHONUNBUFFERED=1 nohup "$PYTHON" -u -m bridge.hermes2stackchan_bridge --env .env animate-life --pair "$PAIR_ID" \
  >>"$LOG_FILE" 2>&1 &
echo "$!" > "$ROOT_DIR/life-animator.pid"
echo "life animator started: pid=$(cat "$ROOT_DIR/life-animator.pid") log=$LOG_FILE"
