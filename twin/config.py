"""Shared config for the reachy-twin app."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Reachy Mini media pipeline runs at 16 kHz, float32.
SDK_SR = 16000

# Piper voice (legacy fallback)
PIPER_VOICE_DEFAULT = str(ROOT / "models" / "piper" / "en_US-lessac-medium.onnx")

# Kokoro TTS (primary engine) + per-brain voices
KOKORO_MODEL = str(ROOT / "models" / "kokoro" / "kokoro-v1.0.onnx")
KOKORO_VOICES = str(ROOT / "models" / "kokoro" / "voices-v1.0.bin")
CLAUDE_VOICE = os.getenv("CLAUDE_VOICE", "am_michael")   # neutral male
MARCUS_VOICE = os.getenv("MARCUS_VOICE", "af_heart")     # warm female ("heart")

# STT
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")

# Brains
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
MARCUS_URL = os.getenv("MARCUS_URL", "")  # e.g. http://vr-2:PORT  (set per host)
