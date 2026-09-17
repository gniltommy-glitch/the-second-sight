#!/usr/bin/env bash
# Run once as the normal Pi account, after copying the project to this device.
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"
if [[ "$(uname -m)" != aarch64 ]]; then
  echo 'This installer targets Raspberry Pi OS 64-bit (aarch64).' >&2
  exit 1
fi
if [[ "$(id -u)" == 0 ]]; then
  echo 'Run as your normal user; the script calls sudo where required.' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-picamera2 python3-opencv \
  python3-libcamera tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng libopenblas-dev
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -r requirements-models.txt
sudo usermod -aG dialout,video,render "$(id -un)"
mkdir -p models/piper
(
  cd models/piper
  "$APP_ROOT/.venv/bin/python" -m piper.download_voices vi_VN-vais1000-medium
)
echo 'Setup complete. Re-login for groups. Configure UART, test hardware, then install service.'
