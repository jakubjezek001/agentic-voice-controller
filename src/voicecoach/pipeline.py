"""End-to-end analysis of one recording into a session folder.

data/sessions/<id>/
  original.<ext>   the audio
  whisper.json     raw transcription (reused when re-analyzing with the same model)
  transcript.md    readable transcript with speakers and timestamps
  analysis.json    all measurements (turns, per-speaker summary, emotion timeline)
  report.md        data report generated here
  coaching.md      written by Claude (voice-analyze skill), not by this code
  status.json      processing / done / failed
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from voicecoach import audio as A
from voicecoach.config import Paths, VoiceCoachError, log, read_json, with_overrides, write_json
from voicecoach.recorder import slug

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{6})_?(.*)$")


def new_session_dir(paths: Paths, src: Path, name: str | None) -> Path:
    m = TS_RE.match(src.stem)
    if m:
        ts, rest = m.group(1), m.group(2)
    else:
        ts = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d_%H%M%S")
        rest = src.stem
    label = slug(name or rest)
    sid = f"{ts}_{label}" if label else ts
    d, i = paths.sessions / sid, 2
    while d.exists():
        d, i = paths.sessions / f"{sid}-{i}", i + 1
    d.mkdir(parents=True)
    return d


def build_turns(words: list[dict], speakers: list[str], split_gap: float = 5.0) -> list[dict]:
    turns: list[dict] = []
    for w, spk in zip(words, speakers):
        if turns and turns[-1]["speaker"] == spk and w["start"] - turns[-1]["words"][-1]["end"] < split_gap:
            turns[-1]["words"].append(w)
        else:
            turns.append({"speaker": spk, "words": [w]})
    for t in turns:
        t["text"] = "".join(w["word"] for w in t["words"]).strip()
    return turns


def run_pipeline(src: Path, sdir: Path, paths: Paths, cfg: dict, n_speakers: int, me_label: str | None) -> dict:
    from voicecoach import emotion as E
    from voicecoach import prosody as P
    from voicecoach import speakers as S
    from voicecoach import transcribe as T

    log(f"decoding {src.name}")
    audio = A.load_audio(src)
    total = len(audio) / A.SR
    if total < 1:
        raise VoiceCoachError("The recording is empty or too short.")

    cached = read_json(sdir / "whisper.json")
    want_lang = cfg["general"].get("language") or None
    if cached and cached.get("model") == cfg["transcription"]["model"] and (want_lang in (None, cached.get("language"))):
        log("reusing the existing transcription")
        tr = cached
    else:
        log(f"transcribing {A.fmt_time(total)} with whisper '{cfg['transcription']['model']}' (the slow part)")
        tr = T.transcribe(audio, cfg)
        write_json(sdir / "whisper.json", tr)
    words = tr["words"]
    if not words:
        raise VoiceCoachError("No speech was found in the recording.")

    log("telling speakers apart")
    profile = S.load_profile(paths)
    try:
        dia = S.diarize(audio, words, cfg, profile, n_speakers, me_label)
    except ValueError as e:
        raise VoiceCoachError(str(e)) from e
    turns = build_turns(words, dia["word_speakers"])

    log("measuring pace, pauses, fillers and pitch")
    ac = cfg["analysis"]
    fillers = ac["fillers"].get(tr["language"], [])
    for t in turns:
        t.update(P.turn_stats(t["words"], float(ac["pause_seconds"])))
        t["fillers"] = dict(P.count_fillers([w["word"] for w in t["words"]], fillers))
        t["voice"] = P.voice_features(A.cut(audio, t["start"], t["end"]))

    log("scoring emotion")
    emotion = E.score_turns(audio, turns, cfg, paths)

    names = list(dict.fromkeys(t["speaker"] for t in turns))
    summary = {spk: P.summarize(turns, spk, float(ac["long_pause_seconds"])) for spk in names}

    return {
        "version": 1,
        "audio": {"file": src.name, "duration": round(total, 2)},
        "language": tr["language"],
        "language_probability": tr["language_probability"],
        "models": {
            "transcription": cfg["transcription"]["model"],
            "speakers": cfg["speakers"]["model"],
            "emotion": cfg["emotion"]["model"],
        },
        "speaker_method": dia["method"],
        "speakers": dia["speakers"],
        "profile_threshold": dia.get("profile_threshold"),
        "summary": summary,
        "turns": [{k: v for k, v in t.items() if k != "words"} for t in turns],
        "emotion": emotion,
        "fillers_checked": fillers,
    }


def render_transcript(a: dict) -> str:
    lines = [f"# Transcript: {a['session']['id']}", ""]
    for t in a["turns"]:
        lines += [f"**[{A.fmt_time(t['start'])}] {t['speaker']}:** {t['text']}", ""]
    return "\n".join(lines)


def analyze(target: Path, paths: Paths, cfg: dict, *, move: bool = False, name: str | None = None,
            speakers: int | None = None, language: str | None = None, me: str | None = None) -> Path:
    """Analyze a file (copied, or moved when move=True) or re-analyze a session folder in place."""
    from voicecoach.report import history, render_report

    target = Path(target).expanduser().resolve()
    if target.is_dir():
        originals = sorted(target.glob("original.*"))
        if not originals:
            raise VoiceCoachError(f"{target} has no original.* audio file.")
        sdir, src = target, originals[0]
    else:
        if not target.exists():
            raise VoiceCoachError(f"File not found: {target}")
        sdir = new_session_dir(paths, target, name)
        src = sdir / f"original{target.suffix.lower()}"
        try:
            if move:
                shutil.move(str(target), str(src))
            else:
                shutil.copy2(target, src)
        except FileNotFoundError:
            shutil.rmtree(sdir, ignore_errors=True)
            raise

    cfg = with_overrides(cfg, language=language, speakers=speakers)
    n_speakers = int(cfg["general"]["speakers"])
    status = {"state": "processing", "source": str(target), "started_at": datetime.now().isoformat(timespec="seconds")}
    write_json(sdir / "status.json", status)
    t0 = time.time()
    try:
        a = run_pipeline(src, sdir, paths, cfg, n_speakers, me)
        a["session"] = {"id": sdir.name, "analyzed_at": datetime.now().isoformat(timespec="seconds")}
        write_json(sdir / "analysis.json", a)
        (sdir / "transcript.md").write_text(render_transcript(a), encoding="utf-8")
        (sdir / "report.md").write_text(render_report(a, history(paths, sdir)), encoding="utf-8")
    except BaseException as e:
        status.update(state="failed", error=f"{type(e).__name__}: {e}")
        write_json(sdir / "status.json", status)
        raise
    status.update(state="done", finished_at=datetime.now().isoformat(timespec="seconds"),
                  processing_seconds=round(time.time() - t0, 1))
    write_json(sdir / "status.json", status)
    log(f"done in {time.time() - t0:.0f} s: {sdir}")
    return sdir


def list_sessions(paths: Paths) -> list[dict]:
    rows = []
    for d in sorted((p for p in paths.sessions.iterdir() if p.is_dir()), reverse=True):
        st = read_json(d / "status.json", {})
        a = read_json(d / "analysis.json", {}) if (d / "analysis.json").exists() else {}
        me = (a.get("summary") or {}).get("ME") or {}
        rows.append({
            "id": d.name,
            "path": str(d),
            "state": st.get("state", "unknown"),
            "duration": (a.get("audio") or {}).get("duration"),
            "your_share": me.get("talk_share"),
            "report": (d / "report.md").exists(),
            "coaching": (d / "coaching.md").exists(),
            "error": st.get("error"),
        })
    return rows
