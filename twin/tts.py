"""Text-to-speech -> 16 kHz float32 mono, ready for mini.media.push_audio_sample().

KokoroTTS is the primary engine (multi-voice, high quality). Piper Voice kept as fallback.
"""
import numpy as np
from scipy.signal import resample

from .config import SDK_SR, KOKORO_MODEL, KOKORO_VOICES


class KokoroTTS:
    """One model, many voices. synth(text, voice) -> (n,1) float32 @ SDK_SR."""

    def __init__(self, model: str = KOKORO_MODEL, voices: str = KOKORO_VOICES):
        from kokoro_onnx import Kokoro
        self._k = Kokoro(model, voices)

    def synth(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> np.ndarray:
        samples, sr = self._k.create(text, voice=voice, speed=speed, lang="en-us")
        samples = np.asarray(samples, dtype=np.float32)
        if sr != SDK_SR and len(samples) > 0:
            n = int(round(len(samples) * SDK_SR / sr))
            samples = resample(samples, n).astype(np.float32)
        return samples.reshape(-1, 1)


class Voice:
    def __init__(self, model_path: str):
        from piper import PiperVoice
        self._voice = PiperVoice.load(model_path)
        self.sr = int(self._voice.config.sample_rate)

    def synth(self, text: str) -> np.ndarray:
        """Return (n, 1) float32 @ SDK_SR for the given text."""
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return np.zeros((0, 1), dtype=np.float32)
        audio = np.concatenate([c.audio_int16_array for c in chunks]).astype(np.float32) / 32768.0
        if self.sr != SDK_SR and len(audio) > 0:
            n = int(round(len(audio) * SDK_SR / self.sr))
            audio = resample(audio, n).astype(np.float32)
        return audio.reshape(-1, 1)
