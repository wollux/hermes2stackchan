#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x /opt/homebrew/bin/python3.11 ]]; then
    PYTHON=/opt/homebrew/bin/python3.11
  else
    PYTHON=python3
  fi
fi

cd "$ROOT_DIR"
exec "$PYTHON" scripts/debug_cockpit.py "$@"
