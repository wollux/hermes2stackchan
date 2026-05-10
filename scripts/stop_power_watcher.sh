#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
PID_FILE="$ROOT_DIR/power-watcher.pid"
LABEL="de.wollux.hermes2stackchan.power-watcher.$PAIR_ID"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$(uname -s)" == "Darwin" && -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "power watcher launch agent stopped: label=$LABEL"
  exit 0
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "power watcher is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "power watcher stopped: pid=$PID"
else
  echo "stale power watcher pid: $PID"
fi
rm -f "$PID_FILE"
