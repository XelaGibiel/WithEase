#!/usr/bin/env bash
# =====================================================================
#  Builds the standalone WithEase app for Linux (X11).
#  Result:  dist/WithEase/WithEase   (ship the whole folder / a .tar.gz)
#
#  Prerequisites:
#    - Python 3.11+ with the project requirements installed
#    - PyInstaller:        pip install pyinstaller sounddevice requests
#    - System libraries:   sudo apt install libportaudio2 libxcb-cursor0
#  (The GitHub Actions "Build & attach release binaries" workflow does the
#   same automatically for every v* tag.)
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

python3 -m PyInstaller --noconfirm --clean --windowed --name WithEase \
  --paths src \
  --add-data "src/withease/locales:withease/locales" \
  --add-data "src/withease/assets:withease/assets" \
  --hidden-import pynput.keyboard._xorg \
  --hidden-import pynput.mouse._xorg \
  --collect-all sounddevice --collect-all requests \
  --hidden-import wave --hidden-import audioop \
  --hidden-import base64 --hidden-import random \
  --noupx src/withease/__main__.py

echo
echo "Done. Run with:  ./dist/WithEase/WithEase"
