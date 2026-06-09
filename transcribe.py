"""STT test (NO motor movement): record from Reachy mic, transcribe with Whisper, print text.

Captures via sounddevice directly (daemon must be stopped). Pure input test.
Usage: python transcribe.py [seconds]
"""
import sys

import numpy as np
import sounddevice as sd

from twin.stt import STT


def find_reachy_input():
    for i, d in enumerate(sd.query_devices()):
        if "Reachy Mini Audio" in d["name"] and d["max_input_channels"] > 0:
            return i
    return None


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    dev = find_reachy_input()
    sr = 16000
    print(f"loading Whisper... (first run downloads the model)")
    stt = STT()
    print(f"Recording {secs}s from device {dev} -- SPEAK NOW!")
    rec = sd.rec(int(secs * sr), samplerate=sr, channels=2, device=dev, dtype="float32")
    sd.wait()
    mono = rec.mean(axis=1).astype(np.float32)
    rms = float(np.sqrt(np.mean(mono ** 2)))
    print(f"captured rms={rms:.4f}")
    text = stt.transcribe(mono)
    print(f"\nHEARD: {text!r}")


if __name__ == "__main__":
    main()
