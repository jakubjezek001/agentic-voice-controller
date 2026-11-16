"""Audio loading and slicing. Decoding uses PyAV (bundled with faster-whisper), so no system ffmpeg is needed."""

from __future__ import annotations

from pathlib import Path

import numpy as np

SR = 16000


def load_audio(path: Path | str) -> np.ndarray:
    """Any audio/video file -> mono float32 at 16 kHz (decoded with PyAV, bundled ffmpeg)."""
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
    parts: list[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"{path} has no audio stream")
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                parts.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            parts.append(f.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


def load_channels(path: Path | str) -> list[np.ndarray]:
    """A mono or stereo file -> one float32 array per channel at 16 kHz (for two-sided recordings)."""
    import av

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"{path} has no audio stream")
        stream = container.streams.audio[0]
        n = 1 if (stream.channels or 1) == 1 else 2
        resampler = av.AudioResampler(format="s16p", layout="mono" if n == 1 else "stereo", rate=SR)
        parts: list[list[np.ndarray]] = [[] for _ in range(n)]
        frames = [f for fr in container.decode(stream) for f in resampler.resample(fr)]
        frames += list(resampler.resample(None))
        for f in frames:
            arr = f.to_ndarray().reshape(n, -1)
            for c in range(n):
                parts[c].append(arr[c])
    return [
        (np.concatenate(p).astype(np.float32) / 32768.0) if p else np.zeros(0, dtype=np.float32)
        for p in parts
    ]


def duration_seconds(path: Path | str) -> float:
    import av

    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1_000_000
            s = c.streams.audio[0]
            if s.duration and s.time_base:
                return float(s.duration * s.time_base)
    except Exception:
        pass
    return len(load_audio(path)) / SR


def cut(audio: np.ndarray, start: float, end: float) -> np.ndarray:
    return audio[max(0, int(start * SR)): max(0, min(len(audio), int(end * SR)))]


def trim_silence(audio: np.ndarray, top_db: float = 35.0, frame_seconds: float = 0.03) -> np.ndarray:
    """Drop quiet frames (relative to the loudest one) and join what is left."""
    n = int(frame_seconds * SR)
    frames = len(audio) // n
    if frames < 2:
        return audio
    x = audio[: frames * n].reshape(frames, n)
    db = 20 * np.log10(np.sqrt((x**2).mean(axis=1)) + 1e-10)
    return x[db > db.max() - top_db].reshape(-1)


def chunks(audio: np.ndarray, seconds: float, min_seconds: float) -> list[np.ndarray]:
    n, m = int(seconds * SR), int(min_seconds * SR)
    return [audio[i: i + n] for i in range(0, len(audio), n) if len(audio[i: i + n]) >= m]


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
