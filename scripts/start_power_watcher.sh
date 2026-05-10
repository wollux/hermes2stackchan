#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
PID_FILE="$ROOT_DIR/power-watcher.pid"
LOG_FILE="$ROOT_DIR/power-watcher.log"
LABEL="de.wollux.hermes2stackchan.power-watcher.$PAIR_ID"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "power watcher already running: pid=$PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

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
    <string>watch-power</string>
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
  echo "power watcher launch agent started: label=$LABEL log=$LOG_FILE"
  exit 0
fi

PYTHONUNBUFFERED=1 nohup "$PYTHON" -u -m bridge.hermes2stackchan_bridge --env .env watch-power --pair "$PAIR_ID" \
  >>"$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"
echo "power watcher started: pid=$PID log=$LOG_FILE"
