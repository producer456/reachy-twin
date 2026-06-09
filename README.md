# reachy-twin

A **dual-brain voice companion** for the [Reachy Mini](https://reachymini.net/) (Lite) desktop robot.
Two AI personalities share one robot body — switch between them by name — with local
speech-to-text, local text-to-speech, expressive emotion/dance moves, autonomous
behaviors, and a web control panel.

- 🧠 **Two brains, switchable by voice** — say *"Hey Claude"* or *"Hey Marcus"* to swap who's talking
  - **Claude** via the Claude Code CLI (runs on your subscription, no API key) or the Anthropic API
  - **Marcus** via any self-hosted LLM endpoint (`/api/chat` SSE)
- 🎙️ **Local & private** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper) ears,
  [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) voice (each brain its own voice)
- 💃 **Expressive** — 81 emotions + 20 dances from Pollen's HF libraries, triggerable from the panel
- 🤖 **Personable behaviors** — emotions-on-cue (the brain tags its own mood and the robot acts it
  out), turn-to-sound (faces the speaker via the mic array), face-tracking *(in progress)*
- 🎛️ **Web control panel** — chat by text, switch brains, volume, mic toggle, expression buttons,
  behavior toggles

## Architecture

```
mic -> faster-whisper -> ROUTER (Hey Claude / Hey Marcus) -> brain -> [mood] -> emotion move
                                          |                              |
                                    Claude CLI / API              Kokoro TTS -> speaker
                                    Marcus endpoint
```

- `twin/hub.py` — `RobotHub`: one shared `ReachyMini` connection, brains, mic loop, behaviors (thread-safe)
- `twin/panel.py` — FastAPI control panel (serves `twin/static/index.html`)
- `twin/app.py` — standalone voice loop + the wake-word router helpers
- `twin/brains.py` — Claude (CLI/API) + Marcus brains, mood tagging
- `twin/stt.py` / `twin/tts.py` — Whisper STT / Kokoro + Piper TTS
- `vol.py` — quick speaker-volume helper

## Setup

Requires Python 3.12 and a Reachy Mini with its [daemon](https://github.com/pollen-robotics/reachy_mini) running.

```bash
uv venv --python 3.12
uv pip install reachy-mini faster-whisper kokoro-onnx piper-tts anthropic \
               python-dotenv sounddevice reachy-mini-dances-library
# Kokoro voice model:
#   models/kokoro/kokoro-v1.0.onnx + voices-v1.0.bin
#   (from github.com/thewh1teagle/kokoro-onnx releases)
cp .env.example .env   # then edit
```

## Run

```bash
# 1. start the robot daemon (holds the motors + media)
reachy-mini-daemon

# 2a. the web control panel  ->  http://127.0.0.1:8500
python -m twin.panel

# 2b. or the standalone voice loop
python -m twin.app
```

## Notes

- The **Claude brain needs no API key** if you have Claude Code installed and logged in — it shells
  out to `claude -p` on your subscription. Set `ANTHROPIC_API_KEY` to use the API instead.
- **Marcus** expects an endpoint that accepts `POST /api/chat {"message": "..."}` and returns an SSE
  stream of `data: {"text": <cumulative>}` lines ending with `data: {"done": true}`.
