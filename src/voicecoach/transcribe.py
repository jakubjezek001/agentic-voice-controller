"""Speech to text with word timestamps (faster-whisper, runs locally)."""

from __future__ import annotations

import platform
import sys
from functools import lru_cache

import numpy as np

from voicecoach.config import log

# Whisper drops "um"/"uh" unless the prompt shows them.
FILLER_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking."


@lru_cache(maxsize=1)
def _model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    if compute_type == "auto":
        compute_type = "int8" if device == "cpu" else "float16"
    try:
        return WhisperModel(name, device=device, compute_type=compute_type)
    except Exception as e:
        if device == "cpu":
            raise
        log(f"whisper on {device} failed ({e}); using the CPU")
        return WhisperModel(name, device="cpu", compute_type="int8")


def whisper_device(cfg: dict) -> str:
    want = str(cfg["general"].get("device", "auto")).lower()
    if want != "auto":
        return want
    import ctranslate2

    try:
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mps"
    return "cpu"


def transcribe(audio: np.ndarray, cfg: dict) -> dict:
    t = cfg["transcription"]
    model = _model(t["model"], whisper_device(cfg), str(t["compute_type"]))
    language = cfg["general"].get("language") or None
    prompt = FILLER_PROMPT if t.get("keep_fillers") and language == "en" else None
    segments, info = model.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=int(t["beam_size"]),
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                words.append({
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": w.word,
                    "prob": round(float(w.probability), 3),
                })
    return {
        "model": t["model"],
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "duration": round(float(info.duration), 2),
        "words": words,
    }
