from functools import lru_cache

from faster_whisper import WhisperModel

from .config import WHISPER_MODEL


@lru_cache(maxsize=1)
def _model() -> WhisperModel:
    # int8 on CPU is fast and accurate enough for dictation.
    return WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")


def transcribe(audio_path: str) -> str:
    segments, _ = _model().transcribe(audio_path, vad_filter=True)
    return " ".join(seg.text for seg in segments).strip()
