"""Speaker test: synthesize text with Piper and play it through Reachy's speaker.

Usage: python say.py [text...]
Requires the daemon running.
"""
import sys
import time

from reachy_mini import ReachyMini

from twin.config import PIPER_VOICE_DEFAULT
from twin.tts import Voice


def main():
    text = " ".join(sys.argv[1:]) or "Hello. I am alive. Two brains, one robot."
    voice = Voice(PIPER_VOICE_DEFAULT)
    samples = voice.synth(text)
    dur = len(samples) / 16000.0
    print(f"synth {len(samples)} samples (~{dur:.1f}s): {text!r}")

    with ReachyMini(media_backend="default") as mini:
        mini.media.start_playing()
        mini.media.push_audio_sample(samples)   # non-blocking; SDK adds audio-reactive head wobble
        time.sleep(dur + 0.8)
        mini.media.stop_playing()
    print("done")


if __name__ == "__main__":
    main()
