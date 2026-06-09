"""M2 dual-brain voice loop: mic -> Whisper -> (Hey Marcus / Hey Claude router) -> Kokoro -> speaker.

Sticky switching: say a name to switch who's listening; they stay until you call the other.
Run with the daemon already running:

    python -m twin.app
"""
import re
import time

import numpy as np
from reachy_mini import ReachyMini

from twin.config import CLAUDE_VOICE, MARCUS_VOICE
from twin.stt import STT
from twin.tts import KokoroTTS
from twin.brains import make_claude, MarcusBrain, strip_mood

SR = 16000
SILENCE_HANG = 80         # ~0.8 s trailing silence ends an utterance
SPEECH_START = 3          # consecutive loud chunks to trigger
MIN_CHUNKS = 30           # ignore blips shorter than ~0.3 s
EXIT_WORDS = ("goodbye", "good bye", "shut down", "go to sleep")
WAKE = {"marcus": re.compile(r"\bmarcus\b", re.I), "claude": re.compile(r"\bclaude\b", re.I)}


def _rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if len(x) else 0.0


def _mono(s):
    return s.mean(axis=1) if getattr(s, "ndim", 1) == 2 else s


def calibrate_floor(mini, n=40):
    vals = []
    for _ in range(n):
        s = mini.media.get_audio_sample()
        if s is not None and len(s):
            vals.append(_rms(_mono(s)))
    return max((float(np.median(vals)) if vals else 0.001) * 4.0, 0.012)


def capture_utterance(mini, thresh, max_seconds=15.0):
    buf, in_speech, speech_run, silence = [], False, 0, 0
    start = time.time()
    while time.time() - start < max_seconds:
        s = mini.media.get_audio_sample()
        if s is None or len(s) == 0:
            continue
        m = _mono(s)
        loud = _rms(m) > thresh
        if not in_speech:
            if loud:
                speech_run += 1
                buf.append(m)
                if speech_run >= SPEECH_START:
                    in_speech = True
            else:
                speech_run, buf = 0, []
        else:
            buf.append(m)
            silence = silence + 1 if not loud else 0
            if silence >= SILENCE_HANG:
                break
    if not in_speech or len(buf) < MIN_CHUNKS:
        return None
    return np.concatenate(buf).astype(np.float32)


def detect_switch(text, current):
    """Return the brain key named earliest in the utterance, else None."""
    hits = [(m.start(), k) for k, rx in WAKE.items() if (m := rx.search(text))]
    if not hits:
        return None
    return min(hits)[1]


def strip_wake(text):
    """Drop a leading 'hey <name>,' / '<name>,' so the brain doesn't hear its own name."""
    t = re.sub(r"^\s*(hey|hi|ok|okay)?\s*(marcus|claude)[\s,.:!-]*", "", text, count=1, flags=re.I)
    return t.strip()


def say(mini, tts, text, voice):
    samples = tts.synth(text, voice=voice)
    dur = len(samples) / SR
    mini.media.push_audio_sample(samples)        # non-blocking; drives head wobble
    t0 = time.time()
    while time.time() - t0 < dur + 0.3:          # drain mic so we don't hear ourselves
        mini.media.get_audio_sample()


def build_brains():
    brains, voices = {}, {}
    brains["claude"] = make_claude()
    voices["claude"] = CLAUDE_VOICE
    try:
        brains["marcus"] = MarcusBrain()
        voices["marcus"] = MARCUS_VOICE
    except Exception as e:
        print(f"[warn] Marcus unavailable ({e}) -- Claude only")
    return brains, voices


def main():
    stt = STT()
    tts = KokoroTTS()
    brains, voices = build_brains()
    active = "claude"
    print(f"[ready] brains: {', '.join(brains)} | active: {active}")

    with ReachyMini(media_backend="default") as mini:
        mini.media.start_recording()
        mini.media.start_playing()
        thresh = calibrate_floor(mini)
        print(f"[mic] noise-gate = {thresh:.4f}")
        say(mini, tts, "Hey, Claude here. Say my name or Marcus to switch. I'm listening.",
            voices[active])
        try:
            while True:
                audio = capture_utterance(mini, thresh)
                if audio is None:
                    continue
                text = stt.transcribe(audio)
                if not text or len(text) < 2:
                    continue
                print(f"YOU: {text}")
                if any(w in text.lower() for w in EXIT_WORDS):
                    say(mini, tts, "Okay, going quiet. Bye.", voices[active])
                    break

                switched = detect_switch(text, active)
                if switched and switched in brains and switched != active:
                    active = switched
                    print(f"[switch] -> {active}")
                msg = strip_wake(text) if switched else text
                if not msg:                       # just summoned by name, no question
                    say(mini, tts, f"{active.capitalize()} here. What's up?", voices[active])
                    continue

                reply = brains[active].reply(msg)
                _, reply = strip_mood(reply)        # don't speak the [mood] tag aloud
                print(f"{active.upper()}: {reply}")
                say(mini, tts, reply, voices[active])
        except KeyboardInterrupt:
            print("\n[stopped]")
        finally:
            mini.media.stop_recording()
            mini.media.stop_playing()


if __name__ == "__main__":
    main()
