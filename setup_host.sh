#!/usr/bin/env bash
# setup_host.sh -- provision a macOS or Linux machine to run reachy-twin.
# Usage (from the repo root):  bash setup_host.sh
set -e
echo "== reachy-twin host setup =="

# 1. uv
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12

# 2. venv + dependencies
uv venv --python 3.12
uv pip install reachy-mini faster-whisper kokoro-onnx piper-tts anthropic \
               python-dotenv sounddevice reachy-mini-dances-library opencv-python

# 3. Kokoro voice model (~340 MB)
mkdir -p models/kokoro
base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
[ -f models/kokoro/kokoro-v1.0.onnx ] || curl -L "$base/kokoro-v1.0.onnx" -o models/kokoro/kokoro-v1.0.onnx
[ -f models/kokoro/voices-v1.0.bin ] || curl -L "$base/voices-v1.0.bin" -o models/kokoro/voices-v1.0.bin

# 4. Piper fallback voice (used by say.py)
mkdir -p models/piper
[ -f models/piper/en_US-lessac-medium.onnx ] || \
  ./.venv/bin/python -m piper.download_voices en_US-lessac-medium --download-dir models/piper

# 5. .env
[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example -- EDIT IT (set MARCUS_URL)"; }

# 6. Linux only: GStreamer + serial port permission are manual (see Pollen docs). macOS: wheels bundled.
echo ""
echo "Done. Next:"
echo "  - Edit .env  (MARCUS_URL=http://100.67.2.40:7860 for Marcus over Tailscale)"
echo "  - Plug in Reachy (USB-C + 7V-5A power), then:"
echo "      ./.venv/bin/reachy-mini-daemon            # one terminal"
echo "      ./.venv/bin/python -m twin.panel           # another -> http://localhost:8500"
