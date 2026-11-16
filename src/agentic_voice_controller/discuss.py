"""Conversation mode: talk out loud with the agent of a Claude Code / T3 Code thread.

The agent drives the conversation (see the voice-discuss skill). `vc discuss start` launches a
background process that keeps the microphone open, cuts your speech into turns (Silero
VAD), transcribes them (faster-whisper) and speaks the agent's replies (system voices,
see tts.py). Each agent turn is one `vc discuss turn --say -` call: speak the reply, wait
for your next turn, print it.

Because the microphone stays open between calls, nothing you say while the agent is
thinking gets lost: it is handed over with the next turn. The commands reach the
process over HTTP on 127.0.0.1 with a random token kept in data/state/discuss.json.
Nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import wave
from collections import deque
from datetime import datetime
from pathlib import Path

from agentic_voice_controller.config import Paths, VoiceControllerError, read_json, write_json

STATE_FILE = "discuss.json"
LOG_FILE = "discuss.log"
HEARTBEAT_TIMEOUT = 6.0
SR = 16000
FRAME = 512  # samples per Silero VAD frame (32 ms)
SPEECH_ON, SPEECH_OFF = 0.5, 0.35  # VAD probability to start / to keep a turn going
MAX_UTTERANCE = 300.0  # seconds; longer monologues are cut into several turns
ECHO_TAIL = 0.35  # seconds the mic stays muted after the agent stops talking
DEFAULT_WAIT = 240.0  # seconds a turn waits for you to start talking

# Whisper sometimes "hears" subtitle credits in noise
_HALLUCINATIONS = ("amara.org", "titulky vytvořil", "johnyx", "subtitles by", "untertitel", "sous-titres")


class DiscussError(VoiceControllerError):
    pass


def slug(text: str) -> str:
    from agentic_voice_controller.recorder import slug as _slug

    return _slug(text)


def fmt_clock(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# =================================================================================
# Client side (cheap imports only: these run once per conversation turn)
# =================================================================================

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy


def status(paths: Paths) -> dict:
    st = read_json(paths.state / STATE_FILE)
    if not st:
        return {"running": False}
    st["running"] = time.time() - float(st.get("heartbeat", 0)) < HEARTBEAT_TIMEOUT
    if not st["running"]:
        st["stale"] = True
    return st


def _log_tail(paths: Paths, lines: int = 20) -> str:
    try:
        text = (paths.state / LOG_FILE).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _running(paths: Paths) -> dict:
    st = status(paths)
    if st.get("running"):
        return st
    if st.get("stale"):
        (paths.state / STATE_FILE).unlink(missing_ok=True)
        raise DiscussError("The conversation process stopped unexpectedly. Start a new one with `vc discuss start`.\n"
                        + _log_tail(paths))
    raise DiscussError("No conversation is running. Start one with `vc discuss start`.")


def request(st: dict, path: str, body: dict | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{st['port']}{path}",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Discuss-Token": st["token"]},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            msg = str(e)
        raise DiscussError(msg or str(e)) from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise DiscussError(f"Cannot reach the conversation process ({e}). Check `vc discuss status`.") from e


def start(paths: Paths, cfg: dict, *, name: str | None = None, barge_in: bool | None = None,
          language: str | None = None, device: str | None = None,
          input_file: str | None = None, tts: str | None = None, keep_audio: bool | None = None) -> dict:
    from agentic_voice_controller.recorder import spawn_background

    st = status(paths)
    if st.get("running"):
        return dict(st, already_running=True)
    (paths.state / STATE_FILE).unlink(missing_ok=True)
    stem = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if name and slug(name):
        stem += f"_{slug(name)}"
    out_dir = paths.conversations / stem
    args = [sys.executable, "-m", "agentic_voice_controller", "discuss", "_run", "--dir", str(out_dir)]
    if barge_in is not None:
        args += ["--barge-in", "1" if barge_in else "0"]
    if keep_audio is not None:
        args += ["--keep-audio", "1" if keep_audio else "0"]
    for flag, value in (("--language", language), ("--device", device), ("--input-file", input_file),
                        ("--tts", tts), ("--name", name)):
        if value:
            args += [flag, str(value)]
    proc = spawn_background(paths, args, paths.state / LOG_FILE)
    deadline = time.time() + 240  # the first start may load the speech model from disk
    while time.time() < deadline:
        st = status(paths)
        if st.get("running") and st.get("ready"):
            return st
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    raise DiscussError("The conversation process did not start.\n" + _log_tail(paths))


def turn(paths: Paths, say: str | None, lang: str | None, wait: float) -> dict:
    st = _running(paths)
    return request(st, "/turn", {"say": say, "lang": lang, "wait": wait}, timeout=wait + MAX_UTTERANCE + 120)


def say(paths: Paths, text: str, lang: str | None) -> dict:
    st = _running(paths)
    return request(st, "/say", {"say": text, "lang": lang}, timeout=600)


def stop(paths: Paths, text: str | None = None, lang: str | None = None) -> dict:
    st = status(paths)
    if not st.get("running"):
        if st.get("stale"):
            (paths.state / STATE_FILE).unlink(missing_ok=True)
            return {"stopped": True, "dir": st.get("dir"), "note": "the process had already stopped"}
        raise DiscussError("No conversation is running.")
    res = request(st, "/stop", {"say": text, "lang": lang}, timeout=600)
    deadline = time.time() + 15
    while (paths.state / STATE_FILE).exists() and time.time() < deadline:
        time.sleep(0.2)
    return res


def speak_once(cfg: dict, text: str, lang: str) -> dict:
    """Speak without a running conversation (to try a voice)."""
    from agentic_voice_controller.tts import Player, clean_for_speech, make_tts, resolve_output_device, split_for_speech

    tts = make_tts(cfg)
    tts.check()
    player = Player(resolve_output_device(cfg["conversation"].get("output_device")))
    chunks = split_for_speech(clean_for_speech(text))
    res = player.play_chunks(chunks, lambda t: tts.synth(t, lang))
    return dict(res, voice=tts.voice_for(lang), lang=lang)


# =================================================================================
# The background process
# =================================================================================

class StreamingVAD:
    """Silero VAD (the model bundled with faster-whisper) with its state carried
    across calls, so it can follow a live stream frame by frame."""

    def __init__(self):
        import numpy as np
        from faster_whisper.vad import get_vad_model

        self.np = np
        self.session = get_vad_model().session
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(64, dtype=np.float32)

    def __call__(self, frames):
        """frames: (n, 512) float32 -> n speech probabilities."""
        np = self.np
        ctx = np.vstack([self.ctx[None, :], frames[:-1, -64:]])
        self.ctx = frames[-1, -64:].copy()
        out, self.h, self.c = self.session.run(
            None, {"input": np.concatenate([ctx, frames], axis=1), "h": self.h, "c": self.c})
        return out.reshape(-1)


class Conversation:
    def __init__(self, paths: Paths, cfg: dict, out_dir: Path, *, name: str | None = None,
                 barge_in: bool | None = None, language: str | None = None, device: str | None = None,
                 input_file: str | None = None, tts: str | None = None, keep_audio: bool | None = None):
        import numpy as np

        from agentic_voice_controller.tts import Player, make_tts, resolve_output_device

        self.np = np
        self.paths, self.cfg, self.dir, self.name = paths, cfg, out_dir, name
        c = cfg["conversation"]
        self.c = c
        self.barge_in = bool(c.get("barge_in")) if barge_in is None else barge_in
        self.keep_audio = bool(c.get("keep_audio")) if keep_audio is None else keep_audio
        self.language = (language or cfg["general"].get("language") or "").strip()
        self.languages = [str(x) for x in (c.get("languages") or []) if str(x).strip()]
        self.last_lang = self.language or (self.languages[0] if self.languages else "")
        self.end_silence_frames = max(3, int(float(c["end_silence_seconds"]) * SR / FRAME))
        self.min_speech = float(c["min_speech_seconds"])
        self.start_frames = 3  # ~0.1 s of speech starts a turn
        self.start_frames_over_agent = 10  # ~0.3 s while the agent talks (barge-in)
        self.device_spec = device if device is not None else cfg["recording"].get("device")
        self.input_file = input_file
        self.tts = make_tts(cfg, tts)
        self.tts.check()
        self.player = Player(resolve_output_device(c.get("output_device")), silent=(tts == "silent"))
        self.token = secrets.token_urlsafe(24)

        self.cond = threading.Condition()
        self.ready: list[dict] = []  # your turns, transcribed, not yet handed to the agent
        self.transcribing = 0
        self.in_speech = False
        self.turns: list[dict] = []  # the whole conversation, for the transcript
        self.turn_gen = 0
        self.active_requests = 0
        self.last_activity = time.monotonic()
        self.stop_event = threading.Event()
        self.server = None

        self.speaking = False  # the agent's speech is being prepared or played
        self.audible = False  # ... and is actually coming out of the speakers
        self.mute: deque[list[float]] = deque(maxlen=8)  # monotonic intervals to ignore (echo guard)
        self.audio_q: queue.Queue = queue.Queue()
        self.utt_q: queue.Queue = queue.Queue()
        self.frames_seen = 0
        self.peak_window: deque[float] = deque(maxlen=40)
        self.max_peak = 0.0
        self.blocks = 0
        self.warnings: list[str] = []
        self.started_at = datetime.now()
        self.wav = None
        self.stream = None

        # listener state
        self.state = "idle"
        self.preroll: deque = deque(maxlen=self.start_frames_over_agent + 10)
        self.cur: list = []
        self.speech_run = self.silence_run = self.voiced = 0
        self.utt_start = 0.0

    # --- setup / teardown -------------------------------------------------------------

    def log(self, msg: str) -> None:
        print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)

    def stt_model(self):
        from agentic_voice_controller.transcribe import _model, whisper_device

        name = self.c.get("stt_model") or self.cfg["transcription"]["model"]
        return _model(name, whisper_device(self.cfg), str(self.cfg["transcription"]["compute_type"]))

    def open(self) -> None:
        np = self.np
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log(f"loading models (stt: {self.c.get('stt_model') or self.cfg['transcription']['model']})")
        self.vad = StreamingVAD()
        model = self.stt_model()
        list(model.transcribe(np.zeros(SR, dtype=np.float32), language="en", beam_size=1)[0])  # warm up
        if self.keep_audio:
            self.wav = wave.open(str(self.dir / "mic.wav"), "wb")
            self.wav.setnchannels(1)
            self.wav.setsampwidth(2)
            self.wav.setframerate(SR)
        self._open_source()
        self.threads = [threading.Thread(target=self._listen_loop, name="listen", daemon=True),
                        threading.Thread(target=self._transcribe_loop, name="stt", daemon=True)]
        for t in self.threads:
            t.start()
        # microphone check: real microphones are never perfectly silent
        t_end = time.time() + 2.5
        while time.time() < t_end and self.blocks < 20:
            time.sleep(0.1)
        time.sleep(0.5)
        if self.blocks == 0:
            raise DiscussError("No audio arrives from the microphone. Check `vc devices`.")
        if self.max_peak < 2 / 32768 and not self.input_file:
            self.warnings.append(
                "The microphone delivers pure silence: access is probably blocked. On macOS allow the app "
                "that runs Claude Code (T3 Code, Terminal, ...) in System Settings > Privacy & Security > "
                "Microphone, then restart that app.")
        self._save()

    def _open_source(self) -> None:
        np = self.np
        if self.input_file:  # tests: play a file into the listener in real time, then silence
            from agentic_voice_controller.audio import load_audio

            audio = load_audio(Path(self.input_file))
            self.input_name = f"file {Path(self.input_file).name}"

            def feed() -> None:
                block = FRAME * 2
                i, t0 = 0, time.monotonic()
                while not self.stop_event.is_set():
                    chunk = audio[i:i + block] if i < len(audio) else np.zeros(block, dtype=np.float32)
                    if len(chunk) < block:
                        chunk = np.concatenate([chunk, np.zeros(block - len(chunk), dtype=np.float32)])
                    i += block
                    self.audio_q.put((time.monotonic(), chunk))
                    wait = t0 + i / SR - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)

            threading.Thread(target=feed, daemon=True).start()
            return

        from agentic_voice_controller.recorder import _sd, device_name, resolve_device

        sd = _sd()
        dev = resolve_device(self.device_spec)
        self.input_name = device_name(dev)
        try:
            sd.check_input_settings(device=dev, samplerate=SR, channels=1, dtype="float32")
            rate = SR
        except Exception:
            rate = int(sd.query_devices(dev, "input")["default_samplerate"])
        self.in_rate = rate

        def callback(indata, frames, time_info, status):
            self.audio_q.put((time.monotonic(), indata[:, 0].copy()))

        self.stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=dev,
                                     blocksize=int(rate * 0.064), callback=callback)
        self.stream.start()

    def close(self) -> None:
        self.stop_event.set()
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        for t in getattr(self, "threads", []):
            t.join(timeout=5)
        if self.wav is not None:
            try:
                self.wav.close()
            except Exception:
                pass
        self._save(final=True)

    # --- listening --------------------------------------------------------------------

    def _is_muted(self, t: float) -> bool:
        return any(a <= t <= b for a, b in tuple(self.mute))

    def _listen_loop(self) -> None:
        np = self.np
        buf = np.zeros(0, dtype=np.float32)
        while not self.stop_event.is_set():
            try:
                t_cap, x = self.audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if getattr(self, "in_rate", SR) != SR:
                    from agentic_voice_controller.tts import _resample

                    x = _resample(x, self.in_rate, SR)
                self.blocks += 1
                peak = float(np.abs(x).max()) if len(x) else 0.0
                self.peak_window.append(peak)
                self.max_peak = max(self.max_peak, peak)
                buf = np.concatenate([buf, x])
                n = len(buf) // FRAME
                if n == 0:
                    continue
                frames = buf[: n * FRAME].reshape(n, FRAME)
                buf = buf[n * FRAME:]
                probs = self.vad(frames)
                muted = not self.barge_in and self._is_muted(t_cap)
                for f, p in zip(frames, probs):
                    self._on_frame(f, float(p), muted)
            except Exception:
                self.log("listener error:\n" + traceback.format_exc())

    def _on_frame(self, f, p: float, muted: bool) -> None:
        np = self.np
        pos = self.frames_seen * FRAME / SR
        self.frames_seen += 1
        if self.wav is not None:
            pcm = np.zeros(FRAME, dtype=np.int16) if muted else (np.clip(f, -1, 1) * 32767).astype(np.int16)
            self.wav.writeframes(pcm.tobytes())
        if muted:  # the agent is talking through the speakers: do not listen to it
            if self.state == "idle":
                self.preroll.clear()
                self.speech_run = 0
                return
            p = 0.0  # a turn in progress hears silence instead
        if self.state == "idle":
            self.preroll.append(f)
            self.speech_run = self.speech_run + 1 if p >= SPEECH_ON else 0
            need = self.start_frames_over_agent if self.audible else self.start_frames
            if self.speech_run >= need:
                self.state = "speech"
                self.cur = list(self.preroll)
                self.utt_start = pos - (len(self.preroll) - 1) * FRAME / SR
                self.voiced, self.silence_run = self.speech_run, 0
                with self.cond:
                    self.in_speech = True
                    self.cond.notify_all()
                if self.speaking and (self.barge_in or not self.audible):
                    self.player.stop()  # you started talking: the agent yields
            return
        self.cur.append(f)
        if p >= SPEECH_OFF:
            self.silence_run = 0
            if p >= SPEECH_ON:
                self.voiced += 1
        else:
            self.silence_run += 1
        if self.silence_run >= self.end_silence_frames or len(self.cur) * FRAME / SR >= MAX_UTTERANCE:
            keep = len(self.cur) - max(0, self.silence_run - int(0.3 * SR / FRAME))
            audio = np.concatenate(self.cur[:keep])
            voiced_s = self.voiced * FRAME / SR
            self.state, self.cur, self.speech_run = "idle", [], 0
            self.preroll.clear()
            with self.cond:
                self.in_speech = False
                if voiced_s >= self.min_speech:
                    self.transcribing += 1
                    self.utt_q.put({"audio": audio, "start": round(self.utt_start, 2),
                                    "end": round(self.utt_start + len(audio) / SR, 2)})
                self.cond.notify_all()
            if voiced_s >= self.min_speech and self.c.get("earcon", True) and not self.speaking:
                threading.Thread(target=self._earcon, daemon=True).start()

    def _earcon(self) -> None:
        start = time.monotonic()
        self.mute.append([start, start + 0.35])
        self.player.tone()

    # --- speech to text ---------------------------------------------------------------

    def _detect_language(self, model, audio) -> str:
        if self.language:
            return self.language
        try:
            _, _, probs = model.detect_language(audio)
        except Exception:
            return self.last_lang
        if self.languages:
            allowed = [(lang, p) for lang, p in probs if lang in self.languages]
            if allowed:
                return max(allowed, key=lambda x: x[1])[0]
        lang, p = probs[0]
        if p < 0.5 and self.last_lang:
            return self.last_lang
        return lang

    def _stt(self, audio) -> tuple[str, str]:
        model = self.stt_model()
        lang = self._detect_language(model, audio)
        segments, info = model.transcribe(
            audio, language=lang or None, beam_size=int(self.c.get("beam_size", 3)), vad_filter=False,
            condition_on_previous_text=False, without_timestamps=len(audio) < 28 * SR,
        )
        parts = [s.text.strip() for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(p for p in parts if p).strip()
        if any(h in text.lower() for h in _HALLUCINATIONS):
            text = ""
        return text, (lang or info.language)

    def _transcribe_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                u = self.utt_q.get(timeout=0.3)
            except queue.Empty:
                continue
            text, lang = "", self.last_lang
            try:
                t = time.monotonic()
                text, lang = self._stt(u["audio"])
                self.log(f"you ({lang}, {u['end'] - u['start']:.1f} s, stt {time.monotonic() - t:.1f} s): {text}")
            except Exception:
                self.log("transcription error:\n" + traceback.format_exc())
            with self.cond:
                self.transcribing -= 1
                if text:
                    self.last_lang = lang or self.last_lang
                    entry = {"who": "you", "start": u["start"], "end": u["end"], "text": text, "lang": lang}
                    self.ready.append(entry)
                    self.turns.append(entry)
                self.cond.notify_all()
            if text:
                self._save()

    # --- speaking ---------------------------------------------------------------------

    def _speak(self, text: str, lang: str | None) -> dict:
        from agentic_voice_controller.tts import clean_for_speech, guess_language, split_for_speech

        chunks = split_for_speech(clean_for_speech(text))
        if not chunks:
            return {"status": "empty"}
        if not lang:  # the agent may answer in another language than the user's last turn
            default = self.last_lang or "en"
            candidates = list(dict.fromkeys([default, "en", *self.languages, *(t["lang"] for t in self.turns if t.get("lang"))]))
            lang = default if self.language else guess_language(text, candidates, default)
        lang = lang.lower()
        start_pos = self.frames_seen * FRAME / SR
        interval: list[float] = []

        def on_audible() -> None:
            self.audible = True
            interval[:] = [time.monotonic(), float("inf")]
            self.mute.append(interval)

        with self.player.lock:
            self.speaking = True
            try:
                res = self.player.play_chunks(chunks, lambda t: self.tts.synth(t, lang), on_audible=on_audible)
            finally:
                if interval:
                    interval[1] = time.monotonic() + ECHO_TAIL
                self.speaking = self.audible = False
        if not res["interrupted"]:
            status = "spoken"
        elif res["started"] == 0:
            status = "not spoken"
        else:
            status = "interrupted"
        heard = " ".join(chunks[: res["started"]])
        entry = {"who": "agent", "start": round(start_pos, 2), "text": text, "lang": lang, "status": status}
        if status == "interrupted":
            entry["heard_up_to"] = heard
        with self.cond:
            self.turns.append(entry)
        self._save()
        self.log(f"agent ({lang}, {status}, {res['seconds']} s): {text}")
        return {"status": status, "heard": heard if status == "interrupted" else None,
                "voice": self.tts.voice_for(lang), "seconds": res["seconds"]}

    # --- requests ---------------------------------------------------------------------

    def _busy(self) -> bool:
        return bool(self.ready) or self.transcribing > 0 or self.in_speech

    def turn(self, say_text: str | None, lang: str | None, wait: float, alive) -> dict:
        with self.cond:
            self.turn_gen += 1
            gen = self.turn_gen
            busy = self._busy()
        reply = None
        if say_text and say_text.strip():
            if busy:
                reply = {"status": "not spoken", "reason": "you were talking"}
                with self.cond:
                    self.turns.append({"who": "agent", "start": round(self.frames_seen * FRAME / SR, 2),
                                       "text": say_text, "status": "not spoken"})
            else:
                reply = self._speak(say_text, lang)
        res = self._wait_for_you(gen, wait, alive)
        res["reply"] = reply
        return res

    def _wait_for_you(self, gen: int, wait: float, alive) -> dict:
        deadline = time.monotonic() + wait
        hard_deadline = deadline + MAX_UTTERANCE + 60
        with self.cond:
            while True:
                if gen != self.turn_gen:
                    return {"superseded": True, "text": "", "utterances": []}
                if not alive():
                    return {"client_gone": True, "text": "", "utterances": []}
                settled = not self.in_speech and self.transcribing == 0
                now = time.monotonic()
                if settled and (self.ready or now >= deadline):
                    break
                if now >= hard_deadline or self.stop_event.is_set():
                    break
                self.cond.wait(0.25)
            got, self.ready = self.ready, []
        text = " ".join(u["text"] for u in got).strip()
        return {"text": text, "utterances": got, "language": got[-1]["lang"] if got else self.last_lang,
                "silence": not got, "waited": round(wait, 0), "warnings": list(self.warnings)}

    def say_only(self, text: str, lang: str | None) -> dict:
        return {"reply": self._speak(text, lang)} if text and text.strip() else {"reply": None}

    def snapshot(self) -> dict:
        np = self.np
        peak = max(self.peak_window) if self.peak_window else 0.0
        return {
            "pid": os.getpid(), "port": self.server.server_address[1] if self.server else None,
            "token": self.token, "dir": str(self.dir), "name": self.name,
            "started_at": self.started_at.isoformat(timespec="seconds"), "heartbeat": time.time(),
            "ready": self.server is not None, "input": getattr(self, "input_name", None),
            "level_dbfs": round(20 * float(np.log10(max(peak, 1e-6))), 1),
            "in_speech": self.in_speech, "turns": len(self.turns), "barge_in": self.barge_in,
            "language": self.language or (",".join(self.languages) if self.languages else "auto"),
            "voices": {lang: self.tts.voice_for(lang) for lang in self._voice_langs()},
            "end_silence_seconds": self.c["end_silence_seconds"], "warnings": list(self.warnings),
        }

    def _voice_langs(self) -> list[str]:
        langs = [self.language] if self.language else list(self.languages)
        if self.last_lang and self.last_lang not in langs:
            langs.append(self.last_lang)
        return langs or ["en"]

    # --- files ------------------------------------------------------------------------

    def _save(self, final: bool = False) -> None:
        with self.cond:
            turns = list(self.turns)
        meta = {
            "name": self.name, "started_at": self.started_at.isoformat(timespec="seconds"),
            "seconds": round(self.frames_seen * FRAME / SR, 1), "input": getattr(self, "input_name", None),
            "barge_in": self.barge_in, "finished": final, "audio": "mic.wav" if self.wav is not None else None,
            "turns": turns,
        }
        try:
            write_json(self.dir / "conversation.json", meta)
            title = self.name or "conversation"
            lines = [f"# {title} ({self.started_at:%Y-%m-%d %H:%M})", ""]
            for t in turns:
                who = "You" if t["who"] == "you" else "Agent"
                flag = "" if t.get("status") in (None, "spoken") else f", {t['status']}"
                lines += [f"**{who}** ({fmt_clock(t['start'])}{flag}): {t['text']}", ""]
            (self.dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
        except OSError as e:
            self.log(f"saving failed: {e}")


def _make_handler(conv: Conversation):
    import select
    import socket
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        server_version = "vc-discuss"

        def log_message(self, *args) -> None:
            pass

        def _send(self, code: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def _alive(self) -> bool:
            """False once the client hung up (e.g. the agent's command timed out)."""
            try:
                r, _, _ = select.select([self.connection], [], [], 0)
                return not r or bool(self.connection.recv(1, socket.MSG_PEEK))
            except OSError:
                return False

        def do_POST(self) -> None:
            if not secrets.compare_digest(self.headers.get("X-Discuss-Token", ""), conv.token):
                return self._send(403, {"error": "bad token"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError):
                return self._send(400, {"error": "bad request"})
            with conv.cond:
                conv.active_requests += 1
            try:
                if self.path == "/turn":
                    res = conv.turn(body.get("say"), body.get("lang"), float(body.get("wait") or DEFAULT_WAIT),
                                    self._alive)
                    if res.get("client_gone") or res.get("superseded"):
                        conv.log("turn abandoned by the client" if res.get("client_gone") else "turn superseded")
                elif self.path == "/say":
                    res = conv.say_only(body.get("say") or "", body.get("lang"))
                elif self.path == "/status":
                    res = conv.snapshot()
                elif self.path == "/stop":
                    reply = conv.say_only(body.get("say") or "", body.get("lang"))["reply"]
                    res = {"stopped": True, "dir": str(conv.dir), "reply": reply,
                           "transcript": str(conv.dir / "transcript.md"),
                           "audio": str(conv.dir / "mic.wav") if conv.wav is not None else None,
                           "seconds": round(conv.frames_seen * FRAME / SR, 1), "turns": len(conv.turns)}
                else:
                    return self._send(404, {"error": f"unknown path {self.path}"})
                self._send(200, res)
                if self.path == "/stop":
                    threading.Thread(target=conv_shutdown, args=(conv,), daemon=True).start()
            except Exception as e:
                conv.log("request error:\n" + traceback.format_exc())
                self._send(500, {"error": str(e)})
            finally:
                with conv.cond:
                    conv.active_requests -= 1
                    conv.last_activity = time.monotonic()

    return Handler


def conv_shutdown(conv: Conversation, reason: str = "stop requested") -> None:
    if conv.stop_event.is_set():
        return
    conv.log(f"stopping: {reason}")
    conv.stop_event.set()
    with conv.cond:
        conv.cond.notify_all()
    if conv.server is not None:
        conv.server.shutdown()


def run_daemon(paths: Paths, cfg: dict, out_dir: Path, **kw) -> None:
    from http.server import ThreadingHTTPServer

    state_file = paths.state / STATE_FILE
    conv = Conversation(paths, cfg, out_dir, **kw)
    conv.log(f"conversation in {out_dir}")
    conv.open()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(conv))
    server.daemon_threads = True
    conv.server = server

    def on_signal(*_):
        threading.Thread(target=conv_shutdown, args=(conv, "signal"), daemon=True).start()

    for sig in ("SIGTERM", "SIGINT", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), on_signal)
            except (OSError, ValueError):
                pass

    idle_limit = float(conv.c.get("idle_minutes", 5)) * 60
    max_seconds = float(conv.c.get("max_minutes", 180)) * 60
    t_start = time.monotonic()

    def heartbeat() -> None:
        while not conv.stop_event.is_set():
            try:
                write_json(state_file, conv.snapshot())
            except OSError as e:
                conv.log(f"state write failed: {e}")
            now = time.monotonic()
            with conv.cond:
                idle = conv.active_requests == 0 and now - conv.last_activity > idle_limit
            if idle:
                conv_shutdown(conv, f"no turn requested for {idle_limit / 60:g} min")
            elif now - t_start > max_seconds:
                conv_shutdown(conv, "maximum length reached")
            conv.stop_event.wait(1.0)

    write_json(state_file, conv.snapshot())
    threading.Thread(target=heartbeat, name="heartbeat", daemon=True).start()
    conv.log(f"listening on {conv.input_name}; voices {conv.snapshot()['voices']}")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        conv.close()
        server.server_close()
        state_file.unlink(missing_ok=True)
        conv.log("stopped")
