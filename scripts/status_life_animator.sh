#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAIR_ID="${1:-desk}"
PID_FILE="$ROOT_DIR/life-animator.pid"
LOG_FILE="$ROOT_DIR/life-animator.log"
LABEL="de.wollux.hermes2stackchan.life-animator.$PAIR_ID"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if launchctl print "gui/$(id -u)/$LABEL" >/tmp/h2s-life-animator-status.txt 2>/dev/null; then
    echo "life animator launch agent running: label=$LABEL"
    grep -E 'pid =|state =' /tmp/h2s-life-animator-status.txt || true
  else
    echo "life animator launch agent is not running: label=$LABEL"
  fi
else
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "life animator running: pid=$PID"
    else
      echo "life animator pid file is stale: $PID"
    fi
  else
    echo "life animator is not running"
  fi
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "last log lines:"
  tail -n 20 "$LOG_FILE"
fi
