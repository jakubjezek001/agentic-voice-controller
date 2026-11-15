"""Who is speaking when, and which speaker is you.

Speech is cut into short overlapping windows, each window becomes a speaker embedding
(WavLM x-vector), the windows are clustered into N speakers, and the cluster closest
to your enrolled voice profile (data/models/voice_profile.npz) is labelled ME.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

import numpy as np

from voicecoach import audio as A
from voicecoach.config import Paths, torch_device

PROFILE_FILE = "voice_profile.npz"


@lru_cache(maxsize=1)
def _load(name: str, device: str):
    from transformers import AutoFeatureExtractor, WavLMForXVector

    fe = AutoFeatureExtractor.from_pretrained(name)
    model = WavLMForXVector.from_pretrained(name).to(device).eval()
    return fe, model


def embed(segments: list[np.ndarray], cfg: dict, batch: int = 16) -> np.ndarray:
    """L2-normalized speaker embeddings, one per segment."""
    import torch

    if not segments:
        return np.zeros((0, 512), dtype=np.float32)
    device = torch_device(cfg)
    fe, model = _load(cfg["speakers"]["model"], device)
    order = sorted(range(len(segments)), key=lambda i: len(segments[i]))  # batch similar lengths
    result: list[np.ndarray | None] = [None] * len(segments)
    for i in range(0, len(order), batch):
        idx = order[i: i + batch]
        b = [np.asarray(segments[j], dtype=np.float32) for j in idx]
        inputs = fe(b, sampling_rate=A.SR, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.inference_mode():
            e = model(**inputs).embeddings
        e = torch.nn.functional.normalize(e, dim=-1).cpu().numpy()
        for j, row in zip(idx, e):
            result[j] = row
    return np.stack(result)


def load_profile(paths: Paths) -> dict | None:
    f = paths.models / PROFILE_FILE
    if not f.exists():
        return None
    data = np.load(f)
    return {"embedding": data["embedding"], "threshold": float(data["threshold"])}


def save_profile(paths: Paths, embedding: np.ndarray, threshold: float, seconds: float) -> None:
    np.savez(paths.models / PROFILE_FILE, embedding=embedding, threshold=threshold, seconds=seconds)


def speech_regions(words: list[dict], gap: float = 0.5) -> list[tuple[float, float]]:
    regions: list[list[float]] = []
    for w in words:
        if regions and w["start"] - regions[-1][1] < gap:
            regions[-1][1] = max(regions[-1][1], w["end"])
        else:
            regions.append([w["start"], w["end"]])
    return [(s, e) for s, e in regions]


def make_windows(regions, total: float, win: float, hop: float, min_len: float = 0.8) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in regions:
        if e - s < win:
            if e - s < min_len:
                c = (s + e) / 2
                s, e = max(0.0, c - min_len / 2), min(total, c + min_len / 2)
            out.append((s, e))
            continue
        t = s
        while t + win <= e + 1e-6:
            out.append((t, t + win))
            t += hop
        if out[-1][1] < e - 0.3:
            out.append((e - win, e))
    return out


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def _merge_similar(embs: np.ndarray, labels: np.ndarray, threshold: float) -> np.ndarray:
    labels = labels.copy()
    while True:
        ids = sorted(set(labels.tolist()))
        if len(ids) < 2:
            return labels
        cents = {c: _normalize(embs[labels == c].mean(0)) for c in ids}
        best = max(((a, b) for i, a in enumerate(ids) for b in ids[i + 1:]),
                   key=lambda p: float(cents[p[0]] @ cents[p[1]]))
        if float(cents[best[0]] @ cents[best[1]]) < threshold:
            return labels
        a, b = best
        small, big = (a, b) if (labels == a).sum() < (labels == b).sum() else (b, a)
        labels[labels == small] = big


def _mode_filter(labels: np.ndarray, k: int = 3) -> np.ndarray:
    if len(labels) < k:
        return labels
    h = k // 2
    return np.array([Counter(labels[max(0, i - h): i + h + 1].tolist()).most_common(1)[0][0]
                     for i in range(len(labels))])


def diarize(audio: np.ndarray, words: list[dict], cfg: dict, profile: dict | None,
            n_speakers: int, me_label: str | None = None) -> dict:
    sc = cfg["speakers"]
    total = len(audio) / A.SR
    if not words:
        return {"word_speakers": [], "speakers": {}, "method": "no speech"}

    wins = make_windows(speech_regions(words), total, float(sc["window_seconds"]), float(sc["hop_seconds"]))
    embs = embed([A.cut(audio, s, e) for s, e in wins], cfg)
    n = max(1, min(int(n_speakers), len(wins)))
    if n == 1:
        labels = np.zeros(len(wins), dtype=int)
    else:
        from sklearn.cluster import KMeans

        labels = KMeans(n_clusters=n, n_init=10, random_state=0).fit_predict(embs)
        labels = _merge_similar(embs, labels, float(sc["merge_similarity"]))
        labels = _mode_filter(labels, 3)

    clusters = sorted(set(labels.tolist()), key=lambda c: int(np.argmax(labels == c)))  # order of appearance
    cents = {c: _normalize(embs[labels == c].mean(0)) for c in clusters}
    sims = {c: float(cents[c] @ profile["embedding"]) for c in clusters} if profile else {}

    names: dict[int, str] = {}
    if me_label:
        generic = {c: f"SPEAKER_{i + 1}" for i, c in enumerate(clusters)}
        me = next((c for c, v in generic.items() if v == me_label.upper()), None)
        if me is None:
            raise ValueError(f"--me {me_label}: this recording has {', '.join(generic.values())}")
        method = f"you chose {me_label.upper()}"
    elif profile:
        me = max(sims, key=sims.get)
        method = "voice profile"
    elif int(n_speakers) == 1:
        me = clusters[0]
        method = "single speaker (assumed you)"
    else:
        me = None
        method = "clustering (no voice profile)"

    if me is None:
        names = {c: f"SPEAKER_{i + 1}" for i, c in enumerate(clusters)}
    else:
        names[me] = "ME"
        others = [c for c in clusters if c != me]
        for i, c in enumerate(others):
            names[c] = "OTHER" if len(others) == 1 else f"OTHER_{i + 1}"

    centers = np.array([(s + e) / 2 for s, e in wins])
    word_speakers = []
    for w in words:
        j = int(np.argmin(np.abs(centers - (w["start"] + w["end"]) / 2)))
        word_speakers.append(names[int(labels[j])])

    speakers = {}
    for c in clusters:
        speakers[names[c]] = {
            "windows": int((labels == c).sum()),
            "similarity_to_you": round(sims[c], 3) if sims else None,
        }
    return {
        "word_speakers": word_speakers,
        "speakers": speakers,
        "method": method,
        "profile_threshold": round(profile["threshold"], 3) if profile else None,
    }
