"""The data report (report.md) for one session, with comparison to your earlier sessions."""

from __future__ import annotations

from pathlib import Path

from agentic_voice_controller.audio import fmt_time
from agentic_voice_controller.config import Paths, read_json

# (summary key, label, format, note)
METRICS = [
    ("talk_share", "Your share of talking", "pct", ""),
    ("wpm", "Pace (words/min, pauses included)", "{:.0f}", "conversational English is often ~130-170"),
    ("articulation_wpm", "Pace while actually talking (words/min)", "{:.0f}", ""),
    ("wpm_p90_turn", "Pace in your fastest turns (90th pct)", "{:.0f}", "big gap to the median = you speed up under pressure"),
    ("avg_turn_seconds", "Average turn length (s)", "{:.1f}", ""),
    ("pauses_per_minute", "Pauses per minute of your speech", "{:.1f}", ""),
    ("long_pause_count", "Long pauses", "{:.0f}", ""),
    ("fillers_per_100_words", "Filler words per 100 words", "{:.1f}", "Whisper drops some fillers; the true count is higher"),
    ("questions", "Questions you asked", "{:.0f}", ""),
    ("response_latency_median", "Your response time after the other person (s)", "{:.2f}", ""),
    ("quick_responses", "Times you came in within 0.3 s", "{:.0f}", "may mean you cut them off"),
    ("pitch_median_hz", "Pitch, median (Hz)", "{:.0f}", ""),
    ("pitch_range_st", "Pitch variation (semitones, 10-90 pct)", "{:.1f}", "under ~4 tends to sound flat"),
    ("loudness_var_db", "Loudness variation (dB)", "{:.1f}", ""),
]


def history(paths: Paths, current: Path | None = None, limit: int = 10) -> list[dict]:
    """Your summaries from sessions recorded before the current one."""
    rows = []
    for d in sorted(p for p in paths.sessions.iterdir() if p.is_dir()):
        if current is not None and d.name >= current.name:
            continue
        me = ((read_json(d / "analysis.json", {}) or {}).get("summary") or {}).get("ME")
        if me:
            rows.append({"session": d.name, **me})
    return rows[-limit:]


def _fmt(v, fmt: str) -> str:
    if v is None:
        return "-"
    if fmt == "pct":
        return f"{v:.0%}"
    return fmt.format(v)


def _top(d: dict | None, n: int = 4) -> str:
    if not d:
        return "-"
    return ", ".join(f"{k} {v:.0%}" for k, v in list(d.items())[:n])


def _snip(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def render_report(a: dict, hist: list[dict]) -> str:
    L: list[str] = []
    summ = a["summary"]
    turns = a["turns"]
    focus = ["ME"] if "ME" in summ else [s for s in summ if summ[s]]

    L += [f"# Voice report: {a['session']['id']}", ""]
    L += [
        f"- Recording: `{a['audio']['file']}`, {fmt_time(a['audio']['duration'])}",
        f"- Language: {a['language']} ({a['language_probability']:.0%} sure)",
        f"- Speakers: {a['speaker_method']}",
        f"- Models: whisper `{a['models']['transcription']}`, speakers `{a['models']['speakers']}`, emotion `{a['models']['emotion']}`",
    ]
    if "ME" in a["speakers"] and a["speakers"]["ME"].get("similarity_to_you") is not None:
        sim, thr = a["speakers"]["ME"]["similarity_to_you"], a.get("profile_threshold")
        note = "" if thr is None or sim >= thr - 0.1 else " (low: check the speaker labels in the transcript)"
        L.append(f"- Match with your voice profile: {sim:.2f}{note}")
    if "ME" not in summ:
        L += ["", "> Your voice is not enrolled, so speakers are numbered. Build a voice bank and run "
                  "`vc bank train`, or re-run with `vc analyze <session folder> --me SPEAKER_1`."]
    L.append("")

    L += ["## Talk balance", "", "| Speaker | Talk time | Share | Turns | Avg turn | Longest turn | Questions |",
          "|---|---|---|---|---|---|---|"]
    for spk, s in summ.items():
        if s:
            L.append(f"| {spk} | {fmt_time(s['talk_seconds'])} | {s['talk_share']:.0%} | {s['turns']} | "
                     f"{s['avg_turn_seconds']:.0f} s | {s['longest_turn']['seconds']:.0f} s at "
                     f"{fmt_time(s['longest_turn']['start'])} | {s['questions']} |")
    L.append("")

    for spk in focus:
        s = summ[spk]
        title = "Your delivery" if spk == "ME" else f"Delivery: {spk}"
        use_hist = spk == "ME" and hist
        L += [f"## {title}", ""]
        if use_hist:
            L += [f"| Metric | This call | Your average ({len(hist)} earlier calls) | Note |", "|---|---|---|---|"]
        else:
            L += ["| Metric | This call | Note |", "|---|---|---|"]
        for key, label, fmt, note in METRICS:
            v = s.get(key)
            if use_hist:
                vals = [h[key] for h in hist if h.get(key) is not None]
                avg = sum(vals) / len(vals) if vals else None
                L.append(f"| {label} | {_fmt(v, fmt)} | {_fmt(avg, fmt)} | {note} |")
            else:
                L.append(f"| {label} | {_fmt(v, fmt)} | {note} |")
        if s["top_fillers"]:
            L += ["", "Fillers: " + ", ".join(f"*{w}* x{n}" for w, n in s["top_fillers"])]
        L.append("")

    emo = a["emotion"]
    L += ["## Emotion", ""]
    pm = emo.get("personal_model")
    if pm:
        acc = pm.get("cv_balanced_accuracy")
        L.append(f"Personal model labels: {', '.join(pm['labels'])}. Tested accuracy on held-out clips: "
                 f"{_fmt(acc, 'pct')} (chance: {_fmt(pm.get('chance'), 'pct')}).")
    else:
        L.append("No personal emotion model yet: only the generic model (neutral/happy/angry/sad) is used. "
                 "It was trained on acted speech, so read it as a rough hint.")
    L += ["", "| Speaker | Generic model | Personal model |", "|---|---|---|"]
    for spk, d in emo["by_speaker"].items():
        L.append(f"| {spk} | {_top(d.get('generic'))} | {_top(d.get('personal'))} |")
    L.append("")

    for spk in focus:
        chunks = [c for c in emo["chunks"] if c["speaker"] == spk]
        if not chunks:
            continue
        L += [f"### Over time ({spk}, 5-minute blocks)", "", "| Time | Personal | Generic |", "|---|---|---|"]
        blocks: dict[int, list] = {}
        for c in chunks:
            blocks.setdefault(int(c["start"] // 300), []).append(c)
        for b, cs in sorted(blocks.items()):
            def avg(key):
                acc: dict = {}
                for c in cs:
                    for k, v in (c.get(key) or {}).items():
                        acc[k] = acc.get(k, 0) + v / len(cs)
                return dict(sorted(acc.items(), key=lambda kv: -kv[1]))
            L.append(f"| {fmt_time(b * 300)} | {_top(avg('personal'), 2)} | {_top(avg('generic'), 2)} |")
        L.append("")

    L += ["## Moments worth a look", ""]
    for spk in focus:
        mine = [t for t in turns if t["speaker"] == spk]
        s = summ[spk]
        prefix = "" if spk == "ME" else f"{spk}: "
        fast = sorted((t for t in mine if t["n_words"] >= 15 and s.get("wpm_median_turn")
                       and t["wpm"] > 1.2 * s["wpm_median_turn"]), key=lambda t: -t["wpm"])[:3]
        for t in fast:
            L.append(f"- {prefix}**Fast** [{fmt_time(t['start'])}] {t['wpm']:.0f} wpm: \"{_snip(t['text'])}\"")
        for t in sorted(mine, key=lambda t: -t["duration"])[:2]:
            if t["duration"] >= 45:
                L.append(f"- {prefix}**Long monologue** [{fmt_time(t['start'])}] {t['duration']:.0f} s: \"{_snip(t['text'])}\"")
        for p in s["long_pauses"][:3]:
            L.append(f"- {prefix}**Long pause** [{fmt_time(p['at'])}] {p['seconds']:.1f} s")
        chunks = [c for c in emo["chunks"] if c["speaker"] == spk]
        if chunks and chunks[0].get("personal"):
            overall = emo["by_speaker"][spk].get("personal") or {}
            base = next(iter(overall), None)
            ranked = sorted(chunks, key=lambda c: -max(v for k, v in c["personal"].items() if k != base)
                            if len(c["personal"]) > 1 else 0)
            for c in ranked[:3]:
                lab, v = max(((k, v) for k, v in c["personal"].items() if k != base), key=lambda kv: kv[1])
                if v >= 0.5:
                    L.append(f"- {prefix}**Sounds {lab}** ({v:.0%}) [{fmt_time(c['start'])}]: \"{_snip(c['text'])}\"")
        elif chunks:
            ranked = sorted(chunks, key=lambda c: c["generic"].get("neutral", 0))
            for c in ranked[:3]:
                lab, v = max(((k, v) for k, v in c["generic"].items() if k != "neutral"), key=lambda kv: kv[1])
                if v >= 0.4:
                    L.append(f"- {prefix}**Sounds {lab}** ({v:.0%}, generic model) [{fmt_time(c['start'])}]: \"{_snip(c['text'])}\"")
    L.append("")

    L += [
        "## Caveats", "",
        "- Speaker labels come from voice clustering; a misattributed short phrase is normal. Check the transcript when a number looks off.",
        "- Fillers are counted from the transcript. Whisper removes many \"um\"/\"uh\", and words like *like* or *so* are sometimes real words.",
        "- Emotion scores describe how the voice sounds, not how anyone feels. Treat them as prompts to re-listen, not verdicts.",
        "- Pitch and loudness depend on the microphone. Compare calls recorded the same way.",
        "",
    ]
    return "\n".join(L)
