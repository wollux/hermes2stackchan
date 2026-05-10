#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
PID_FILE="$ROOT_DIR/power-watcher.pid"
LOG_FILE="$ROOT_DIR/power-watcher.log"
LABEL="de.wollux.hermes2stackchan.power-watcher.$PAIR_ID"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if launchctl print "gui/$(id -u)/$LABEL" >/tmp/h2s-power-watcher-status.txt 2>/dev/null; then
    echo "power watcher launch agent running: label=$LABEL"
    grep -E 'pid =|state =' /tmp/h2s-power-watcher-status.txt || true
  else
    echo "power watcher launch agent is not running: label=$LABEL"
  fi
else

  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "power watcher running: pid=$PID"
    else
      echo "power watcher pid file is stale: $PID"
    fi
  else
    echo "power watcher is not running"
  fi
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "last log lines:"
  tail -n 20 "$LOG_FILE"
fi
