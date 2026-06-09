# setup_host.ps1 -- provision a Windows machine to run reachy-twin.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File .\setup_host.ps1
$ErrorActionPreference = "Stop"

Write-Host "== reachy-twin host setup ==" -ForegroundColor Cyan

# 1. uv
if (-not (Test-Path "$HOME\.local\bin\uv.exe")) {
  Write-Host "installing uv..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}
$env:Path = "$HOME\.local\bin;" + $env:Path
uv python install 3.12

# 2. venv + dependencies
uv venv --python 3.12
uv pip install reachy-mini faster-whisper kokoro-onnx piper-tts anthropic `
               python-dotenv sounddevice reachy-mini-dances-library opencv-python

# 3. Kokoro voice model (~340 MB)
New-Item -ItemType Directory -Force "models\kokoro" | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
$ProgressPreference = "SilentlyContinue"
if (-not (Test-Path "models\kokoro\kokoro-v1.0.onnx")) {
  Write-Host "downloading Kokoro model..."
  Invoke-WebRequest -UseBasicParsing "$base/kokoro-v1.0.onnx" -OutFile "models\kokoro\kokoro-v1.0.onnx"
}
if (-not (Test-Path "models\kokoro\voices-v1.0.bin")) {
  Invoke-WebRequest -UseBasicParsing "$base/voices-v1.0.bin" -OutFile "models\kokoro\voices-v1.0.bin"
}

# 4. Piper fallback voice (used by say.py)
New-Item -ItemType Directory -Force "models\piper" | Out-Null
if (-not (Test-Path "models\piper\en_US-lessac-medium.onnx")) {
  & ".venv\Scripts\python.exe" -m piper.download_voices en_US-lessac-medium --download-dir "models\piper"
}

# 5. .env
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "created .env from .env.example -- EDIT IT (set MARCUS_URL)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Green
Write-Host "  - On vr-2 (where Marcus runs): set MARCUS_URL=http://localhost:7860 in .env"
Write-Host "  - Plug in Reachy (USB + 7V-5A power), then:"
Write-Host "      .venv\Scripts\reachy-mini-daemon.exe        # in one terminal"
Write-Host "      .venv\Scripts\python.exe -m twin.panel       # in another -> http://127.0.0.1:8500"
