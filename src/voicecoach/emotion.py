"""Emotion scoring.

Two layers:
- a generic speech-emotion model (neutral/happy/angry/sad), used for everyone;
- your personal classifier, trained on your voice bank (`vc bank train`) from the
  same model's hidden-layer features, used for your own speech only.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import pairwise

import numpy as np

from voicecoach import audio as A
from voicecoach.config import Paths, torch_device

PERSONAL_FILE = "emotion_classifier.joblib"
LABEL_NAMES = {"neu": "neutral", "hap": "happy", "ang": "angry", "sad": "sad"}


class EmotionModel:
    def __init__(self, name: str, device: str, layers: list[int]):
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        self.name = name
        self.device = device
        self.layers = [int(x) for x in layers]
        self.fe = AutoFeatureExtractor.from_pretrained(name)
        self.model = AutoModelForAudioClassification.from_pretrained(name).to(device).eval()
        id2label = self.model.config.id2label
        self.labels = [LABEL_NAMES.get(id2label[i], id2label[i]) for i in range(len(id2label))]

    def run(self, seg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """-> (class probabilities, pooled hidden-layer features)"""
        import torch

        inputs = self.fe(np.asarray(seg, dtype=np.float32), sampling_rate=A.SR, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.inference_mode():
            out = self.model(**inputs, output_hidden_states=True)
        probs = torch.softmax(out.logits, dim=-1)[0].cpu().numpy()
        hs = out.hidden_states
        feats = np.concatenate([hs[min(i, len(hs) - 1)][0].mean(0).cpu().numpy() for i in self.layers])
        return probs, feats


@lru_cache(maxsize=1)
def _cached(name: str, device: str, layers: tuple[int, ...]) -> EmotionModel:
    return EmotionModel(name, device, list(layers))


def get_model(cfg: dict) -> EmotionModel:
    ec = cfg["emotion"]
    return _cached(ec["model"], torch_device(cfg), tuple(ec["feature_layers"]))


def load_personal(paths: Paths, cfg: dict) -> dict | None:
    f = paths.models / PERSONAL_FILE
    if not f.exists():
        return None
    import joblib

    data = joblib.load(f)
    ec = cfg["emotion"]
    if data.get("model") != ec["model"] or list(data.get("layers", [])) != list(ec["feature_layers"]):
        return None  # trained with other features; retrain with `vc bank train`
    return data


def personal_probs(personal: dict, feats: np.ndarray) -> dict[str, float]:
    p = personal["pipeline"].predict_proba(feats[None, :])[0]
    return {str(k): round(float(v), 3) for k, v in zip(personal["pipeline"].classes_, p)}


def _weighted(items: list[tuple[float, dict]]) -> dict[str, float]:
    total = sum(w for w, _ in items)
    if not total:
        return {}
    acc: dict[str, float] = {}
    for w, d in items:
        for k, v in d.items():
            acc[k] = acc.get(k, 0.0) + w * v
    return {k: round(v / total, 3) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}


def score_turns(audio: np.ndarray, turns: list[dict], cfg: dict, paths: Paths) -> dict:
    ec = cfg["emotion"]
    model = get_model(cfg)
    personal = load_personal(paths, cfg)
    seg_s = float(ec["segment_seconds"])
    chunks = []
    for t in turns:
        is_me = t["speaker"] == "ME"
        if (not is_me and not ec["analyze_others"]) or t["duration"] < 1.5:
            continue
        n = max(1, round(t["duration"] / seg_s))
        for s, e in pairwise(np.linspace(t["start"], t["end"], n + 1)):
            if e - s < 1.0:
                continue
            probs, feats = model.run(A.cut(audio, s, e))
            item = {
                "start": round(float(s), 2),
                "end": round(float(e), 2),
                "speaker": t["speaker"],
                "generic": {lab: round(float(p), 3) for lab, p in zip(model.labels, probs)},
                "text": "".join(w["word"] for w in t["words"] if s <= (w["start"] + w["end"]) / 2 < e).strip(),
            }
            if personal and is_me:
                item["personal"] = personal_probs(personal, feats)
            chunks.append(item)

    by_speaker: dict[str, dict] = {}
    for spk in sorted({c["speaker"] for c in chunks}):
        mine = [c for c in chunks if c["speaker"] == spk]
        by_speaker[spk] = {"generic": _weighted([(c["end"] - c["start"], c["generic"]) for c in mine])}
        pers = [(c["end"] - c["start"], c["personal"]) for c in mine if "personal" in c]
        if pers:
            by_speaker[spk]["personal"] = _weighted(pers)

    return {
        "generic_model": model.name,
        "generic_labels": model.labels,
        "personal_model": {
            "labels": [str(x) for x in personal["pipeline"].classes_],
            "trained_at": personal.get("trained_at"),
            "cv_balanced_accuracy": personal.get("metrics", {}).get("balanced_accuracy"),
            "chance": personal.get("metrics", {}).get("chance"),
        } if personal else None,
        "by_speaker": by_speaker,
        "chunks": chunks,
    }
