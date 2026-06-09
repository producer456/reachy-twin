"""faster-whisper speech-to-text. Input: float32 mono @ 16 kHz. Output: text."""
import numpy as np
from faster_whisper import WhisperModel

from .config import WHISPER_MODEL, WHISPER_COMPUTE


class STT:
    def __init__(self, model: str = WHISPER_MODEL, compute: str = WHISPER_COMPUTE):
        # device="cpu" with int8 is fast enough for base.en on the laptop.
        self.model = WhisperModel(model, device="cpu", compute_type=compute)

    def transcribe(self, audio_16k_mono: np.ndarray) -> str:
        if audio_16k_mono.dtype != np.float32:
            audio_16k_mono = audio_16k_mono.astype(np.float32)
        segments, _ = self.model.transcribe(
            audio_16k_mono, language="en", vad_filter=True, beam_size=1,
        )
        return " ".join(s.text.strip() for s in segments).strip()
