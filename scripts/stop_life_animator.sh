#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
PID_FILE="$ROOT_DIR/life-animator.pid"
LABEL="de.wollux.hermes2stackchan.life-animator.$PAIR_ID"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$(uname -s)" == "Darwin" && -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "life animator launch agent stopped: label=$LABEL"
  exit 0
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "life animator is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "life animator stopped: pid=$PID"
else
  echo "stale life animator pid: $PID"
fi
rm -f "$PID_FILE"
