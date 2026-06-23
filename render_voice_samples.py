"""Render Reachy voice samples at several pitch settings so David can pick by ear.
Outputs WAVs to voice_samples/. Throwaway helper."""
import os
import numpy as np
from scipy.io import wavfile
from twin.tts import KokoroTTS
from twin.config import SDK_SR, REACHY_VOICE

TEXT = "Hi David! I'm Reachy. I can talk in my new cute little voice now. How do I sound?"
VOICE = os.getenv("SAMPLE_VOICE", REACHY_VOICE)
TAG = os.getenv("SAMPLE_TAG", "")
SPEED = 1.06
PITCHES = [0, 4, 5, 7, 9, 12]

out = "voice_samples"
os.makedirs(out, exist_ok=True)
tts = KokoroTTS()
for p in PITCHES:
    s = tts.synth(TEXT, voice=VOICE, speed=SPEED, pitch=float(p))
    mono = s.reshape(-1)
    pcm = np.clip(mono, -1, 1)
    pcm = (pcm * 32767).astype(np.int16)
    path = os.path.join(out, f"reachy_{TAG}pitch_{p:02d}.wav")
    wavfile.write(path, SDK_SR, pcm)
    print(f"wrote {path}  ({len(mono)/SDK_SR:.1f}s)")
print("done")
