"""Your voice bank: labelled clips of your own voice.

data/voicebank/<label>/*.wav|m4a|...   one folder per emotion/state (calm, stressed, ...)
data/voicebank/_voice/                  clips used only to learn your voice (no emotion label)

`train()` builds:
- data/models/voice_profile.npz        your speaker embedding, to find you in calls
- data/models/emotion_classifier.joblib your personal emotion classifier
- data/models/bank_metrics.json         held-out accuracy, confusion matrix, per-label voice stats
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from voicecoach import audio as A
from voicecoach.config import AUDIO_EXTENSIONS, Paths, VoiceCoachError, log, read_json, write_json

LABEL_RE = re.compile(r"^_?[a-z0-9][a-z0-9_-]*$")
METRICS_FILE = "bank_metrics.json"


def check_label(label: str) -> str:
    label = label.strip().lower()
    if not LABEL_RE.match(label):
        raise VoiceCoachError(f"Label {label!r}: use lowercase letters, digits, - or _ (e.g. calm, stressed).")
    return label


def clip_files(paths: Paths) -> dict[str, list[Path]]:
    out = {}
    for d in sorted(p for p in paths.voicebank.iterdir() if p.is_dir()):
        out[d.name] = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)
    return out


def status(paths: Paths) -> dict:
    labels = {}
    for label, files in clip_files(paths).items():
        labels[label] = {
            "clips": len(files),
            "seconds": round(sum(A.duration_seconds(f) for f in files), 1),
            "emotion_label": not label.startswith("_"),
        }
    return {
        "voicebank": str(paths.voicebank),
        "labels": labels,
        "voice_profile": (paths.models / "voice_profile.npz").exists(),
        "emotion_classifier": (paths.models / "emotion_classifier.joblib").exists(),
        "metrics": read_json(paths.models / METRICS_FILE),
    }


def _unique(dest: Path) -> Path:
    i = 2
    out = dest
    while out.exists():
        out = dest.with_name(f"{dest.stem}-{i}{dest.suffix}")
        i += 1
    return out


def add(paths: Paths, label: str, files: list[Path]) -> list[Path]:
    label = check_label(label)
    dest_dir = paths.voicebank / label
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for f in files:
        f = Path(f).expanduser()
        if not f.is_file():
            raise VoiceCoachError(f"File not found: {f}")
        if f.suffix.lower() not in AUDIO_EXTENSIONS:
            raise VoiceCoachError(f"Not an audio file this tool knows: {f.name}")
        dest = _unique(dest_dir / f.name)
        shutil.copy2(f, dest)
        out.append(dest)
    return out


def record(paths: Paths, cfg: dict, label: str, seconds: float, device: str | None = None) -> dict:
    from voicecoach.recorder import record_to_wav, resolve_device

    label = check_label(label)
    dest = _unique(paths.voicebank / label / f"{datetime.now():%Y-%m-%d_%H%M%S}.wav")
    rc = cfg["recording"]
    dev = resolve_device(device if device is not None else rc["device"])

    def tick(info):
        left = max(0, seconds - info["seconds"])
        lvl = info["level_dbfs"]
        meter = "-" if lvl is None else f"{lvl:5.1f} dBFS"
        print(f"\r  recording '{label}': {left:4.0f} s left, level {meter}   ", end="", flush=True)

    try:
        result = record_to_wav(dest, dev, int(rc["samplerate"]), int(rc["channels"]), seconds,
                               should_stop=lambda: False, on_tick=tick)
    except KeyboardInterrupt:
        result = {"file": str(dest), "seconds": None, "interrupted": True}
    print()
    return result


def train(paths: Paths, cfg: dict) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from voicecoach import emotion as E
    from voicecoach import prosody as P
    from voicecoach import speakers as S

    files = clip_files(paths)
    all_clips = [(label, f) for label, fs in files.items() for f in fs]
    if not all_clips:
        raise VoiceCoachError(f"The voice bank is empty. Add clips to {paths.voicebank}/<label>/ first.")

    ec = cfg["emotion"]
    seg_s = float(ec["segment_seconds"])
    emo_model = E.get_model(cfg)
    speaker_segments: list[np.ndarray] = []
    X, y, groups, voice_stats = [], [], [], {}
    total_speech = 0.0

    log(f"reading {len(all_clips)} clips")
    for label, f in all_clips:
        audio = A.trim_silence(A.load_audio(f))
        total_speech += len(audio) / A.SR
        speaker_segments += A.chunks(audio, 2.0, 1.0)
        if label.startswith("_"):
            continue
        for ch in A.chunks(audio, seg_s, 1.5):
            _, feats = emo_model.run(ch)
            X.append(feats)
            y.append(label)
            groups.append(str(f))
            vf = P.voice_features(ch)
            if vf:
                voice_stats.setdefault(label, []).append(vf)

    report: dict = {"trained_at": datetime.now().isoformat(timespec="seconds"),
                    "speech_seconds": round(total_speech, 1)}

    # Voice profile
    log("building your voice profile")
    embs = S.embed(speaker_segments, cfg)
    if len(embs) == 0:
        raise VoiceCoachError("The clips contain no usable speech.")
    profile = embs.mean(0)
    profile /= np.linalg.norm(profile) + 1e-9
    sims = embs @ profile
    threshold = float(np.percentile(sims, 5))
    S.save_profile(paths, profile, threshold, total_speech)
    report["voice_profile"] = {
        "windows": int(len(embs)),
        "speech_seconds": round(total_speech, 1),
        "self_similarity_p5": round(threshold, 3),
        "note": "Aim for 2+ minutes of speech, recorded the way you record calls." if total_speech < 120 else "",
    }

    # Personal emotion classifier
    clips_per_label = Counter(label for label, _ in all_clips if not label.startswith("_"))
    usable = sorted(lab for lab, n in clips_per_label.items() if n >= int(ec["min_clips_per_label"]))
    report["labels"] = {lab: {"clips": n, "used": lab in usable} for lab, n in sorted(clips_per_label.items())}
    keep = [i for i, lab in enumerate(y) if lab in usable]
    clf_file = paths.models / E.PERSONAL_FILE

    if len(usable) < 2:
        report["emotion_classifier"] = (
            f"skipped: needs at least 2 labels with {ec['min_clips_per_label']}+ clips each"
        )
        clf_file.unlink(missing_ok=True)
    else:
        import joblib

        log(f"training the emotion classifier on {len(keep)} chunks, labels: {', '.join(usable)}")
        Xk = np.stack([X[i] for i in keep])
        yk = np.array([y[i] for i in keep])
        gk = np.array([groups[i] for i in keep])
        pipe = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=5000, class_weight="balanced"))
        n_splits = min(5, min(clips_per_label[lab] for lab in usable))
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=0)
        pred = cross_val_predict(pipe, Xk, yk, groups=gk, cv=cv)
        metrics = {
            "labels": usable,
            "chunks": int(len(yk)),
            "folds": n_splits,
            "accuracy": round(float((pred == yk).mean()), 3),
            "balanced_accuracy": round(float(balanced_accuracy_score(yk, pred)), 3),
            "chance": round(1 / len(usable), 3),
            "confusion_matrix": confusion_matrix(yk, pred, labels=usable).tolist(),
            "confusion_note": "rows = true label, columns = predicted, in the order of 'labels'",
        }
        pipe.fit(Xk, yk)
        joblib.dump({
            "pipeline": pipe,
            "model": ec["model"],
            "layers": list(ec["feature_layers"]),
            "segment_seconds": seg_s,
            "trained_at": report["trained_at"],
            "metrics": metrics,
        }, clf_file)
        report["emotion_classifier"] = metrics

    report["voice_by_label"] = {
        lab: {k: round(float(np.median([v[k] for v in vs if k in v])), 2)
              for k in ("pitch_median_hz", "pitch_range_st", "loudness_var_db") if any(k in v for v in vs)}
        for lab, vs in sorted(voice_stats.items())
    }
    write_json(paths.models / METRICS_FILE, report)
    return report
