#!/usr/bin/env bash
# =====================================================================
#  WithEase launcher for Linux (developers/testers with Python installed).
#  Double-clickable or run: ./START_WithEase.sh
#  End users use the packaged binary from dist/WithEase/ instead.
#
#  Note: full functionality requires an X11/Xorg session – global key
#  handling is restricted under Wayland.
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"

# Prefer the project's own virtual environment if present.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "Python 3.11 or newer was not found. Please install it (e.g. sudo apt install python3)."
  exit 1
fi

# First-run dependency check.
if ! "$PY" -c "import PySide6, pynput" >/dev/null 2>&1; then
  echo "[WithEase] First-time setup, please wait ..."
  "$PY" -m pip install -r requirements.txt || {
    echo "Setup failed. Please check your internet connection."; exit 1; }
fi

exec "$PY" -m withease
