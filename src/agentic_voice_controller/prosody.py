"""Delivery measurements: pace, pauses, fillers, pitch and loudness."""

from __future__ import annotations

import re
from collections import Counter
from itertools import pairwise

import numpy as np

from agentic_voice_controller import audio as A

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def norm_word(w: str) -> str:
    return _PUNCT.sub("", w).strip().lower()


def voice_features(seg: np.ndarray) -> dict:
    """Pitch (Hz, semitone spread) and loudness spread for one stretch of speech."""
    if len(seg) < int(0.3 * A.SR):
        return {}
    import parselmouth

    snd = parselmouth.Sound(seg.astype(np.float64), sampling_frequency=A.SR)
    out: dict = {}
    f0 = snd.to_pitch(time_step=0.01, pitch_floor=70.0, pitch_ceiling=450.0).selected_array["frequency"]
    f0 = f0[f0 > 0]
    if len(f0) >= 10:
        st = 12 * np.log2(f0 / 100.0)
        out["pitch_median_hz"] = round(float(np.median(f0)), 1)
        out["pitch_range_st"] = round(float(np.percentile(st, 90) - np.percentile(st, 10)), 2)
        out["pitch_std_st"] = round(float(np.std(st)), 2)
    v = snd.to_intensity(minimum_pitch=70.0, time_step=0.01).values.flatten()
    v = v[np.isfinite(v)]
    if len(v) >= 10:
        loud = v[v > np.percentile(v, 25)]
        out["loudness_db"] = round(float(np.percentile(v, 75)), 1)
        out["loudness_var_db"] = round(float(np.std(loud)), 2)
    return out


def turn_stats(words: list[dict], pause_seconds: float) -> dict:
    start, end = words[0]["start"], words[-1]["end"]
    dur = max(end - start, 1e-3)
    pauses = [(a["end"], b["start"] - a["end"]) for a, b in pairwise(words)
              if b["start"] - a["end"] >= pause_seconds]
    speaking = max(dur - sum(g for _, g in pauses), 1e-3)
    n = len(words)
    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(dur, 2),
        "n_words": n,
        "wpm": round(n / dur * 60, 1),
        "articulation_wpm": round(n / speaking * 60, 1),
        "pauses": len(pauses),
        "pause_list": [[round(t, 2), round(g, 2)] for t, g in pauses],
    }


_REPEATS = re.compile(r"(\w)\1+", re.UNICODE)


def _squash(token: str) -> str:
    """'Umm' -> 'um', 'hmmm' -> 'hm', 'eeee' -> 'e': spelling variants of the same filler."""
    return _REPEATS.sub(r"\1", token)


def count_fillers(words: list[str], fillers: list[str]) -> Counter:
    tokens = [_squash(t) for t in (norm_word(w) for w in words) if t]
    canon = {_squash(f.lower()): f.lower() for f in fillers}
    single = {k for k in canon if " " not in k}
    multi = [k.split() for k in canon if " " in k]
    c: Counter = Counter()
    for i, t in enumerate(tokens):
        if t in single:
            c[canon[t]] += 1
        for m in multi:
            if tokens[i: i + len(m)] == m:
                c[canon[" ".join(m)]] += 1
    return c


def summarize(turns: list[dict], speaker: str, long_pause: float) -> dict | None:
    mine = [t for t in turns if t["speaker"] == speaker]
    if not mine:
        return None
    talk = sum(t["duration"] for t in mine)
    all_talk = sum(t["duration"] for t in turns) or 1.0
    n_words = sum(t["n_words"] for t in mine)
    wpm_turns = [t["wpm"] for t in mine if t["n_words"] >= 8]
    fillers: Counter = Counter()
    for t in mine:
        fillers.update(t["fillers"])
    long_pauses = sorted(
        ({"at": p[0], "seconds": p[1]} for t in mine for p in t["pause_list"] if p[1] >= long_pause),
        key=lambda p: -p["seconds"],
    )
    latencies = [b["start"] - a["end"] for a, b in pairwise(turns)
                 if b["speaker"] == speaker and a["speaker"] != speaker]
    voice = [t["voice"] for t in mine if t.get("voice") and t["duration"] >= 1.5]

    def med(key):
        vals = [v[key] for v in voice if key in v]
        return round(float(np.median(vals)), 2) if vals else None

    longest = max(mine, key=lambda t: t["duration"])
    return {
        "talk_seconds": round(talk, 1),
        "talk_share": round(talk / all_talk, 3),
        "turns": len(mine),
        "avg_turn_seconds": round(talk / len(mine), 1),
        "longest_turn": {"start": longest["start"], "seconds": longest["duration"]},
        "words": n_words,
        "wpm": round(n_words / talk * 60, 1) if talk else None,
        "wpm_median_turn": round(float(np.median(wpm_turns)), 1) if wpm_turns else None,
        "wpm_p90_turn": round(float(np.percentile(wpm_turns, 90)), 1) if wpm_turns else None,
        "articulation_wpm": round(float(np.median([t["articulation_wpm"] for t in mine if t["n_words"] >= 8])), 1)
        if wpm_turns else None,
        "pauses_per_minute": round(sum(t["pauses"] for t in mine) / (talk / 60), 2) if talk else None,
        "long_pauses": long_pauses[:10],
        "long_pause_count": len(long_pauses),
        "fillers_total": sum(fillers.values()),
        "fillers_per_100_words": round(100 * sum(fillers.values()) / n_words, 2) if n_words else None,
        "top_fillers": fillers.most_common(8),
        "response_latency_median": round(float(np.median(latencies)), 2) if latencies else None,
        "quick_responses": sum(1 for x in latencies if x < 0.3),
        "questions": sum(t["text"].count("?") for t in mine),
        "pitch_median_hz": med("pitch_median_hz"),
        "pitch_range_st": med("pitch_range_st"),
        "loudness_var_db": med("loudness_var_db"),
    }
