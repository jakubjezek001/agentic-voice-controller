"""Command line: `vc <command>`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from agentic_voice_controller import __version__
from agentic_voice_controller.config import (
    AUDIO_EXTENSIONS, Paths, VoiceControllerError, get_paths, go_offline_if_cached, hf_repo_cached,
    load_config, setup_env, whisper_repo,
)


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# --- commands -------------------------------------------------------------------

def cmd_init(args, paths: Paths, cfg: dict) -> int:
    print(f"data folder: {paths.root}")
    print(f"settings:    {paths.config_file}")
    print("Drop phone recordings into data/inbox/ or run `vc analyze <file>`.")
    return 0


def cmd_doctor(args, paths: Paths, cfg: dict) -> int:
    import importlib
    import platform

    ok = True

    def row(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {label}{': ' + detail if detail else ''}")

    print(f"agentic-voice-controller {__version__} on {platform.system()} {platform.machine()}, Python {platform.python_version()}")
    print(f"data: {paths.root}")
    print("\nPackages")
    for mod in ("numpy", "torch", "transformers", "faster_whisper", "ctranslate2", "av", "parselmouth", "sklearn"):
        try:
            m = importlib.import_module(mod)
            row(True, mod, getattr(m, "__version__", ""))
        except Exception as e:
            row(False, mod, str(e))

    print("\nMicrophone")
    try:
        from agentic_voice_controller.recorder import list_input_devices

        devs = list_input_devices()
        default = next((d["name"] for d in devs if d["default"]), "none")
        row(bool(devs), f"{len(devs)} input device(s)", f"default: {default}")
    except Exception as e:
        print(f"  [--] recording not available here: {e}")

    print("\nConversation mode (speech output)")
    try:
        from agentic_voice_controller.tts import make_tts, pick_voice

        make_tts(cfg).check()
        configured = cfg["conversation"].get("voices") or {}
        langs = sorted({cfg["general"].get("language") or "en", *(cfg["conversation"].get("languages") or [])})
        row(True, "text-to-speech", ", ".join(f"{lang}: {pick_voice(lang, configured) or 'system default'}"
                                              for lang in langs))
    except Exception as e:
        row(False, "text-to-speech", str(e))

    print("\nModels (downloaded once into data/cache)")
    for label, repo in (("transcription", whisper_repo(cfg["transcription"]["model"])),
                        ("speakers", cfg["speakers"]["model"]), ("emotion", cfg["emotion"]["model"])):
        cached = hf_repo_cached(paths, repo)
        row(cached, f"{label}: {repo}", "" if cached else "run `vc models download`")

    print("\nYour voice bank")
    print(f"  [{'ok' if (paths.models / 'voice_profile.npz').exists() else '--'}] voice profile "
          "(lets reports find you in a call)")
    print(f"  [{'ok' if (paths.models / 'emotion_classifier.joblib').exists() else '--'}] personal emotion model")
    print("\n" + ("All good." if ok else "Some checks failed (see [!!] above)."))
    return 0 if ok else 1


def cmd_devices(args, paths, cfg) -> int:
    from agentic_voice_controller.recorder import list_input_devices

    devs = list_input_devices()
    if args.json:
        _print_json(devs)
    else:
        for d in devs:
            print(f"{'*' if d['default'] else ' '} {d['index']:>3}  {d['name']}  ({d['channels']} ch, {d['samplerate']} Hz)")
        print("\n* = default. Set [recording] device in data/config.toml, or pass --device.")
    return 0


def cmd_models_download(args, paths, cfg) -> int:
    from agentic_voice_controller.config import log, torch_device

    log(f"whisper '{cfg['transcription']['model']}'")
    from faster_whisper.utils import download_model

    download_model(cfg["transcription"]["model"])
    log(f"speaker model {cfg['speakers']['model']}")
    from agentic_voice_controller import speakers

    speakers._load(cfg["speakers"]["model"], torch_device(cfg))
    log(f"emotion model {cfg['emotion']['model']}")
    from agentic_voice_controller import emotion

    emotion.get_model(cfg)
    log(f"models are in {paths.cache}")
    return 0


def cmd_record_start(args, paths, cfg) -> int:
    from agentic_voice_controller import recorder

    st = recorder.start(paths, cfg, name=args.name, device=args.device, max_minutes=args.max_minutes,
                        system_device=args.system_device)
    if args.json:
        _print_json(st)
    else:
        print(f"Recording to {st['file']}")
        if st.get("system_device"):
            print(f"you: {st.get('device')} (channel 1), incoming audio: {st['system_device']} (channel 2). "
                  "Stop with `vc record stop`.")
        else:
            print(f"device: {st.get('device')}. Stop with `vc record stop`.")
    return 0


def cmd_record_stop(args, paths, cfg) -> int:
    from agentic_voice_controller import recorder

    last = recorder.stop(paths)
    f = Path(last["file"])
    out = {"file": str(f), "seconds": last.get("seconds")}
    if args.split and recorder.sides_file(f).exists():  # two-sided: also one file per side
        out["tracks"] = {k: str(v) for k, v in recorder.split_sides(f).items()}
    if args.inbox:
        from agentic_voice_controller.recorder import sides_file

        dest = paths.inbox / f.name
        shutil.move(str(f), str(dest))
        if sides_file(f).exists():
            shutil.move(str(sides_file(f)), str(sides_file(dest)))
        out["file"] = str(dest)
        out["note"] = "moved to the inbox for the watcher"
    elif args.analyze:
        from agentic_voice_controller.pipeline import analyze

        out["session"] = str(analyze(f, paths, cfg, move=True))
    if args.json:
        _print_json(out)
    else:
        print(f"Stopped. {out['seconds']} s saved to {out['file']}")
        if out.get("tracks"):
            print(f"you: {out['tracks']['me']}\nothers: {out['tracks']['others']}")
        if out.get("session"):
            print(f"Session: {out['session']}")
    return 0


def cmd_record_status(args, paths, cfg) -> int:
    from agentic_voice_controller import recorder

    st = recorder.status(paths)
    if args.json:
        _print_json(st)
    elif st.get("running"):
        lvl = st.get("level_dbfs")
        shown = lambda v: v if v is not None else "-"  # noqa: E731
        if st.get("system_device"):
            print(f"Recording {st.get('seconds', 0):.0f} s to {st['file']} (you: {st.get('device')}, level "
                  f"{shown(lvl)} dBFS; incoming audio: {st['system_device']}, level "
                  f"{shown(st.get('system_level_dbfs'))} dBFS)")
        else:
            print(f"Recording {st.get('seconds', 0):.0f} s to {st['file']} (device {st.get('device')}, "
                  f"level {shown(lvl)} dBFS)")
        if lvl is not None and lvl < -60:
            print("Very quiet input: is the right microphone selected and allowed?")
    elif st.get("stale"):
        print(f"The recorder died. Partial file: {st.get('file')}")
    else:
        last = st.get("last") or {}
        print("Not recording." + (f" Last: {last.get('file')} ({last.get('seconds')} s)" if last else ""))
    return 0


def cmd_record_split(args, paths, cfg) -> int:
    from agentic_voice_controller.recorder import RecorderError, split_sides

    f = Path(args.file).expanduser()
    if not f.is_file():
        raise RecorderError(f"File not found: {f}")
    tracks = split_sides(f)
    if args.json:
        _print_json({k: str(v) for k, v in tracks.items()})
    else:
        print(f"you: {tracks['me']}\nothers: {tracks['others']}")
    return 0


def cmd_record_run(args, paths, cfg) -> int:
    from agentic_voice_controller.recorder import run_background

    run_background(paths, cfg, Path(args.out), args.device, args.max_minutes, args.system_device)
    return 0


# --- conversation mode ------------------------------------------------------------

def _text_arg(value: str | None) -> str | None:
    """`-` reads the text from stdin (as UTF-8 on every platform)."""
    if value == "-":
        return sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
    return value


def cmd_talk_start(args, paths, cfg) -> int:
    from agentic_voice_controller import talk

    st = talk.start(paths, cfg, name=args.name, barge_in=args.barge_in, language=args.language,
                    device=args.device, input_file=args.input_file, tts=args.tts)
    st.pop("token", None)
    if args.json:
        _print_json(st)
        return 0
    print(("Already running: " if st.get("already_running") else "Listening. ") + f"Folder: {st['dir']}")
    voices = ", ".join(f"{k}: {v or 'system default'}" for k, v in (st.get("voices") or {}).items())
    print(f"mic: {st.get('input')} (level {st.get('level_dbfs')} dBFS); language: {st.get('language')}; "
          f"voice {voices}")
    print(f"Your turn ends after {st.get('end_silence_seconds')} s of silence. "
          f"Barge-in: {'on' if st.get('barge_in') else 'off (the mic is muted while the agent talks)'}.")
    for w in st.get("warnings") or []:
        print(f"WARNING: {w}")
    return 0


def _print_turn(res: dict) -> None:
    reply = res.get("reply") or {}
    if reply.get("status") == "not spoken":
        print("note: your reply was NOT spoken because the user was talking. Answer what they said below.")
    elif reply.get("status") == "interrupted":
        print(f"note: the user interrupted you. They heard only: \"{reply.get('heard')}\"")
    for w in res.get("warnings") or []:
        print(f"WARNING: {w}")
    if res.get("silence"):
        print(f"(silence: the user said nothing for {res.get('waited', 0):.0f} s. Run `turn` without --say to "
              "keep listening, or end the conversation.)")
    else:
        print(f"USER ({res.get('language')}): {res.get('text')}")


def cmd_talk_turn(args, paths, cfg) -> int:
    from agentic_voice_controller import talk

    res = talk.turn(paths, _text_arg(args.say), args.lang, args.wait)
    if args.json:
        _print_json(res)
    else:
        _print_turn(res)
    return 0


def cmd_talk_say(args, paths, cfg) -> int:
    from agentic_voice_controller import talk

    text = _text_arg(args.text) or ""
    if talk.status(paths).get("running"):
        res = talk.say(paths, text, args.lang)
    else:
        lang = args.lang or cfg["general"].get("language") or (cfg["conversation"].get("languages") or ["en"])[0]
        res = talk.speak_once(cfg, text, lang)
    if args.json:
        _print_json(res)
    else:
        r = res.get("reply", res) or {}
        print(f"spoken with voice {r.get('voice') or 'system default'}" if r else "nothing to say")
    return 0


def cmd_talk_stop(args, paths, cfg) -> int:
    from agentic_voice_controller import talk

    res = talk.stop(paths, _text_arg(args.say), args.lang)
    if args.analyze and res.get("audio") and Path(res["audio"]).exists():
        from agentic_voice_controller.pipeline import analyze

        from collections import Counter

        from agentic_voice_controller.config import read_json

        name = "talk-" + Path(res["dir"]).name.split("_", 2)[-1] if "_" in Path(res["dir"]).name else "talk"
        conv = read_json(Path(res["dir"]) / "conversation.json") or {}
        langs = Counter(t.get("lang") for t in conv.get("turns", []) if t.get("who") == "you" and t.get("lang"))
        language = langs.most_common(1)[0][0] if langs else None
        res["session"] = str(analyze(Path(res["audio"]), paths, cfg, move=False, name=name, speakers=1,
                                     language=language))
    if args.json:
        _print_json(res)
        return 0
    mins = (res.get("seconds") or 0) / 60
    print(f"Stopped after {mins:.1f} min and {res.get('turns', 0)} turns.")
    print(f"folder: {res.get('dir')}\n  transcript: {res.get('transcript')}")
    if res.get("audio"):
        print(f"  your audio: {res['audio']}")
    if res.get("session"):
        print(f"  voice analysis: {res['session']}/report.md")
    return 0


def cmd_talk_status(args, paths, cfg) -> int:
    from agentic_voice_controller import talk

    st = talk.status(paths)
    st.pop("token", None)
    if args.json:
        _print_json(st)
    elif st.get("running"):
        print(f"Conversation running since {st.get('started_at')}: {st.get('turns')} turns, "
              f"{'you are talking' if st.get('in_speech') else 'quiet'}, mic level {st.get('level_dbfs')} dBFS.")
        print(f"folder: {st.get('dir')}")
    elif st.get("stale"):
        print(f"The conversation process died. Its files are in {st.get('dir')}. See data/state/talk.log.")
    else:
        print("No conversation running.")
    return 0


def cmd_talk_voices(args, paths, cfg) -> int:
    from agentic_voice_controller.tts import is_novelty, list_voices, pick_voice

    voices = list_voices()
    configured = cfg["conversation"].get("voices") or {}
    langs = [args.lang] if args.lang else sorted({cfg["general"].get("language") or "en", "en",
                                                   *(cfg["conversation"].get("languages") or []), *configured})
    if args.json:
        _print_json({"voices": voices, "picked": {lang: pick_voice(lang, configured) for lang in langs}})
        return 0
    for lang in langs:
        picked = pick_voice(lang, configured)
        print(f"{lang}: using {picked or 'the system default voice'}")
        for v in (v for v in voices if v["lang"] == lang and (args.all or not is_novelty(v["name"]))):
            mark = "*" if v["name"] == picked else " "
            print(f"   {mark} {v['name']:<32} {v['locale']:<8} {v['quality']}")
    print("\nSet one in data/config.toml under [conversation.voices], e.g. en = \"Ava (Premium)\".")
    if sys.platform == "darwin":
        print("Better voices: System Settings > Accessibility > Spoken Content > System Voice > Manage Voices "
              "(pick a Premium or Enhanced one).")
    return 0


def cmd_talk_run(args, paths, cfg) -> int:
    from agentic_voice_controller.talk import run_daemon

    barge = None if args.barge_in is None else args.barge_in == "1"
    run_daemon(paths, cfg, Path(args.dir), name=args.name, barge_in=barge, language=args.language,
               device=args.device, input_file=args.input_file, tts=args.tts)
    return 0


def cmd_import(args, paths, cfg) -> int:
    for f in args.files:
        f = Path(f).expanduser()
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTENSIONS:
            raise VoiceControllerError(f"Not an audio file: {f}")
        name = f.name if not args.name else f"{args.name}{f.suffix.lower()}"
        dest = paths.inbox / name
        shutil.copy2(f, dest)
        print(dest)
    return 0


def _latest_recording(paths: Paths) -> Path:
    files = sorted((f for f in paths.recordings.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS),
                   key=lambda f: f.stat().st_mtime)
    if not files:
        raise VoiceControllerError("No recordings in data/recordings/.")
    return files[-1]


def cmd_analyze(args, paths, cfg) -> int:
    from agentic_voice_controller.pipeline import analyze

    targets: list[tuple[Path, bool]] = [(Path(t), False) for t in args.targets]
    if args.latest:
        targets.append((_latest_recording(paths), True))
    if args.inbox:
        targets += [(f, True) for f in sorted(paths.inbox.iterdir())
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
    if not targets:
        raise VoiceControllerError("Nothing to analyze: give a file or session folder, --latest, or --inbox.")
    results = []
    for t, inside_data in targets:
        move = inside_data or _is_within(t, paths.recordings) or _is_within(t, paths.inbox)
        sdir = analyze(t, paths, cfg, move=move, name=args.name, speakers=args.speakers,
                       language=args.language, me=args.me)
        results.append({"session": str(sdir), "report": str(sdir / "report.md"),
                        "transcript": str(sdir / "transcript.md"), "analysis": str(sdir / "analysis.json")})
    if args.json:
        _print_json(results)
    else:
        for r in results:
            print(f"Session: {r['session']}\n  report: {r['report']}\n  transcript: {r['transcript']}")
    return 0


def _is_within(p: Path, parent: Path) -> bool:
    try:
        Path(p).expanduser().resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cmd_sessions(args, paths, cfg) -> int:
    from agentic_voice_controller.pipeline import list_sessions

    rows = list_sessions(paths)[: args.limit]
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("No sessions yet.")
    for r in rows:
        dur = f"{r['duration'] / 60:5.1f} min" if r["duration"] else "    -    "
        share = f"you {r['your_share']:.0%}" if r["your_share"] is not None else ""
        flags = " coached" if r["coaching"] else ""
        print(f"{r['id']:<45} {r['state']:<10} {dur} {share}{flags}")
    return 0


def cmd_watch(args, paths, cfg) -> int:
    from agentic_voice_controller.watch import watch

    n = watch(paths, cfg, interval=args.interval, once=args.once)
    if args.once:
        print(f"processed {n} file(s)")
    return 0


def cmd_bank_status(args, paths, cfg) -> int:
    from agentic_voice_controller import bank

    st = bank.status(paths)
    if args.json:
        _print_json(st)
        return 0
    print(f"Voice bank: {st['voicebank']}")
    if not any(v["clips"] for v in st["labels"].values()):
        print("  (empty)")
    for label, v in st["labels"].items():
        kind = "emotion" if v["emotion_label"] else "voice only"
        print(f"  {label:<16} {v['clips']:>3} clips  {v['seconds'] / 60:5.1f} min  ({kind})")
    print(f"voice profile: {'yes' if st['voice_profile'] else 'no'}, "
          f"personal emotion model: {'yes' if st['emotion_classifier'] else 'no'}")
    m = (st.get("metrics") or {}).get("emotion_classifier")
    if isinstance(m, dict):
        print(f"last training: balanced accuracy {m['balanced_accuracy']:.0%} (chance {m['chance']:.0%})")
    return 0


def cmd_bank_add(args, paths, cfg) -> int:
    from agentic_voice_controller import bank

    for f in bank.add(paths, args.label, [Path(f) for f in args.files]):
        print(f)
    return 0


def cmd_bank_record(args, paths, cfg) -> int:
    from agentic_voice_controller import bank

    r = bank.record(paths, cfg, args.label, args.seconds, args.device)
    print(f"saved {r['file']}")
    return 0


def cmd_bank_train(args, paths, cfg) -> int:
    from agentic_voice_controller import bank

    r = bank.train(paths, cfg)
    if args.json:
        _print_json(r)
        return 0
    vp = r["voice_profile"]
    print(f"Voice profile: {vp['speech_seconds'] / 60:.1f} min of speech. {vp['note']}".rstrip())
    ec = r["emotion_classifier"]
    if isinstance(ec, dict):
        print(f"Emotion model: labels {', '.join(ec['labels'])}; held-out balanced accuracy "
              f"{ec['balanced_accuracy']:.0%} (chance {ec['chance']:.0%}) over {ec['folds']} folds")
        print("Confusion matrix (rows = true, columns = predicted):")
        w = max(len(x) for x in ec["labels"])
        print(" " * (w + 2) + " ".join(f"{x[:6]:>6}" for x in ec["labels"]))
        for lab, rowv in zip(ec["labels"], ec["confusion_matrix"]):
            print(f"  {lab:<{w}} " + " ".join(f"{v:>6}" for v in rowv))
    else:
        print(f"Emotion model: {ec}")
    return 0


# --- parser --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vc", description="Agentic Voice Controller: control agents by voice; record, transcribe and analyze how you speak.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create the data folder and default settings")
    s.set_defaults(func=cmd_init)
    s = sub.add_parser("doctor", help="check packages, microphone and models")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("devices", help="list microphones")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_devices)

    m = sub.add_parser("models", help="model management").add_subparsers(dest="models_cmd", required=True)
    s = m.add_parser("download", help="download all models now (otherwise on first use)")
    s.set_defaults(func=cmd_models_download)

    r = sub.add_parser("record", help="record the microphone (and the incoming call audio) in the background")
    rs = r.add_subparsers(dest="record_cmd", required=True)
    s = rs.add_parser("start")
    s.add_argument("--name", help="short label for the recording, e.g. client-call")
    s.add_argument("--device", help="input device name substring or index")
    s.add_argument("--system-device", dest="system_device",
                   help="loopback input with the incoming call audio (e.g. 'BlackHole 2ch'): records both sides "
                        "as two channels. 'off' = microphone only")
    s.add_argument("--max-minutes", type=float)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_record_start)
    s = rs.add_parser("stop")
    s.add_argument("--analyze", action="store_true", help="analyze the recording right away")
    s.add_argument("--split", action="store_true",
                   help="two-sided recording: also write one file per side (<name>.tracks/me.wav, others.wav)")
    s.add_argument("--inbox", action="store_true", help="hand the recording to the inbox watcher (Docker)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_record_stop)
    s = rs.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_record_status)
    s = rs.add_parser("split", help="split a two-sided recording into one file per side (me.wav, others.wav)")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_record_split)
    s = rs.add_parser("_run")
    s.add_argument("--out", required=True)
    s.add_argument("--device")
    s.add_argument("--system-device", dest="system_device")
    s.add_argument("--max-minutes", type=float, default=180)
    s.set_defaults(func=cmd_record_run)

    t = sub.add_parser("talk", help="conversation mode: talk out loud with the agent (voice-discuss skill)")
    ts = t.add_subparsers(dest="talk_cmd", required=True)
    s = ts.add_parser("start", help="open the microphone and wait for turns")
    s.add_argument("--name", help="topic, used for the folder name")
    s.add_argument("--barge-in", dest="barge_in", action="store_true", default=None,
                   help="let the user interrupt the agent (headphones only)")
    s.add_argument("--no-barge-in", dest="barge_in", action="store_false")
    s.add_argument("--language", help="fix the language, e.g. cs or en (default: settings / detect)")
    s.add_argument("--device", help="input device name substring or index")
    s.add_argument("--input-file", help=argparse.SUPPRESS)
    s.add_argument("--tts", help=argparse.SUPPRESS)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_start)
    s = ts.add_parser("turn", help="speak the agent's reply, then wait for the user's next turn")
    s.add_argument("--say", help="the reply to speak; - reads it from stdin")
    s.add_argument("--lang", help="language of the reply, e.g. cs (default: the user's last language)")
    s.add_argument("--wait", type=float, default=240.0, help="seconds to wait for the user to start talking")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_turn)
    s = ts.add_parser("say", help="speak without waiting for an answer (also works without a conversation)")
    s.add_argument("text", help="the text; - reads it from stdin")
    s.add_argument("--lang")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_say)
    s = ts.add_parser("stop", help="end the conversation (optionally saying goodbye first)")
    s.add_argument("--say", help="last words; - reads them from stdin")
    s.add_argument("--lang")
    s.add_argument("--analyze", action="store_true", help="also analyze your side like a recording")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_stop)
    s = ts.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_status)
    s = ts.add_parser("voices", help="list text-to-speech voices and the one picked per language")
    s.add_argument("--lang")
    s.add_argument("--all", action="store_true", help="include novelty voices")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_talk_voices)
    s = ts.add_parser("_run")
    s.add_argument("--dir", required=True)
    s.add_argument("--name")
    s.add_argument("--barge-in", choices=["0", "1"])
    s.add_argument("--language")
    s.add_argument("--device")
    s.add_argument("--input-file")
    s.add_argument("--tts")
    s.set_defaults(func=cmd_talk_run)

    s = sub.add_parser("import", help="copy audio files (e.g. from your phone) into the inbox")
    s.add_argument("files", nargs="+")
    s.add_argument("--name", help="rename the file, e.g. 2026-11-13_143000_client-call")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("analyze", help="analyze recordings into session reports")
    s.add_argument("targets", nargs="*", help="audio files, or a session folder to re-analyze")
    s.add_argument("--latest", action="store_true", help="the newest file in data/recordings")
    s.add_argument("--inbox", action="store_true", help="everything in data/inbox")
    s.add_argument("--name", help="label for the session folder")
    s.add_argument("--speakers", type=int, help="number of speakers (default from settings)")
    s.add_argument("--language", help="e.g. en, cs (default: settings / auto-detect)")
    s.add_argument("--me", help="which numbered speaker is you, e.g. SPEAKER_2 (when no voice profile)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("sessions", help="list analyzed sessions")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sessions)

    s = sub.add_parser("watch", help="keep analyzing new files in data/inbox (the service)")
    s.add_argument("--interval", type=float, default=10.0)
    s.add_argument("--once", action="store_true", help="process the inbox once and exit")
    s.set_defaults(func=cmd_watch)

    b = sub.add_parser("bank", help="your voice bank of labelled clips")
    bs = b.add_subparsers(dest="bank_cmd", required=True)
    s = bs.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_bank_status)
    s = bs.add_parser("add", help="copy clips into a label folder")
    s.add_argument("label")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_bank_add)
    s = bs.add_parser("record", help="record one clip from the microphone into a label")
    s.add_argument("label")
    s.add_argument("--seconds", type=float, default=15)
    s.add_argument("--device")
    s.set_defaults(func=cmd_bank_record)
    s = bs.add_parser("train", help="build your voice profile and personal emotion model")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_bank_train)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    paths = get_paths().ensure()
    setup_env(paths)
    try:
        cfg = load_config(paths)
        if args.func is not cmd_models_download:
            go_offline_if_cached(paths, cfg)
        return args.func(args, paths, cfg) or 0
    except VoiceControllerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
