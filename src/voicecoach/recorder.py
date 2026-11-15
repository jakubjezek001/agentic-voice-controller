"""Microphone recording.

`start()` launches a detached background process (`vc record _run`) that writes a WAV
file into data/recordings/. `stop()` asks it to finish by creating a stop file, which
works the same way on macOS, Linux and Windows. The recorder writes a heartbeat into
data/state/recorder.json every second, so `status()` can tell a live recorder from a
crashed one without sending signals to process ids.
"""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from voicecoach.config import Paths, VoiceCoachError, read_json, write_json

STATE_FILE = "recorder.json"
STOP_FILE = "recorder.stop"
LAST_FILE = "recorder-last.json"
LOG_FILE = "recorder.log"
HEARTBEAT_TIMEOUT = 6.0


class RecorderError(VoiceCoachError):
    pass


def slug(text: str) -> str:
    s = re.sub(r"[^\w-]+", "-", text.strip(), flags=re.UNICODE).strip("-").lower()
    return s[:60]


# --- devices -----------------------------------------------------------------

def _sd():
    try:
        import sounddevice as sd
    except OSError as e:  # PortAudio missing (Linux without libportaudio2)
        raise RecorderError(
            f"Audio input is not available: {e}. On Debian/Ubuntu run: sudo apt install libportaudio2"
        ) from e
    return sd


def list_input_devices() -> list[dict]:
    sd = _sd()
    default_in = sd.default.device[0] if sd.default.device else None
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({
                "index": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "samplerate": int(d["default_samplerate"]),
                "default": i == default_in,
            })
    return out


def resolve_device(spec: str | int | None) -> int | None:
    if spec is None or str(spec).strip() == "":
        return None
    if isinstance(spec, int) or str(spec).strip().isdigit():
        return int(spec)
    for d in list_input_devices():
        if str(spec).lower() in d["name"].lower():
            return d["index"]
    raise RecorderError(f"No input device matches {spec!r}. Run `vc devices` to see them.")


def device_name(device: int | None) -> str:
    sd = _sd()
    try:
        return sd.query_devices(device, "input")["name"]
    except Exception:
        return "default"


def _pick_samplerate(device: int | None, wanted: int, channels: int) -> int:
    sd = _sd()
    try:
        sd.check_input_settings(device=device, samplerate=wanted, channels=channels, dtype="int16")
        return int(wanted)
    except Exception:
        return int(sd.query_devices(device, "input")["default_samplerate"])


# --- the recording loop ------------------------------------------------------

def record_to_wav(
    out: Path,
    device: int | None,
    samplerate: int,
    channels: int,
    max_seconds: float,
    should_stop: Callable[[], bool],
    on_tick: Callable[[dict], None] | None = None,
) -> dict:
    """Record until should_stop() is true or max_seconds pass. The WAV header is
    rewritten on every write, so a crash still leaves a readable file."""
    sd = _sd()
    q: queue.Queue[bytes] = queue.Queue()
    overflows = 0

    def callback(indata, frames, time_info, status):  # runs on the audio thread
        nonlocal overflows
        if status:
            overflows += 1
        q.put(bytes(indata))

    sr = _pick_samplerate(device, samplerate, channels)
    frames_written = 0
    level_dbfs: float | None = None
    started = time.time()
    last_tick = 0.0

    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)

        def drain() -> None:
            nonlocal frames_written, level_dbfs
            peak = 0
            while True:
                try:
                    buf = q.get_nowait()
                except queue.Empty:
                    break
                wf.writeframes(buf)
                samples = np.frombuffer(buf, dtype=np.int16)
                frames_written += len(samples) // channels
                if len(samples):
                    peak = max(peak, int(np.abs(samples.astype(np.int32)).max()))
            if peak:
                level_dbfs = round(20 * np.log10(peak / 32768), 1)

        with sd.InputStream(samplerate=sr, channels=channels, dtype="int16", device=device, callback=callback):
            while True:
                drain()
                now = time.time()
                if on_tick and now - last_tick >= 1.0:
                    on_tick({"seconds": round(frames_written / sr, 1), "level_dbfs": level_dbfs, "samplerate": sr})
                    last_tick = now
                if should_stop() or now - started >= max_seconds:
                    break
                time.sleep(0.1)
        time.sleep(0.05)
        drain()

    return {
        "file": str(out),
        "seconds": round(frames_written / sr, 1),
        "samplerate": sr,
        "channels": channels,
        "overflows": overflows,
    }


# --- background recorder -----------------------------------------------------

def spawn_background(paths: Paths, args: list[str], log_file: Path) -> subprocess.Popen:
    """Start a process that outlives this one (and the shell or agent that ran it)."""
    env = dict(os.environ, VOICECOACH_DATA=str(paths.root))
    with open(log_file, "ab") as logf:
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, env=env, close_fds=True)
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            try:  # break away from the launcher's job object so the process outlives it
                return subprocess.Popen(args, creationflags=flags | 0x01000000, **kwargs)
            except OSError:
                return subprocess.Popen(args, creationflags=flags, **kwargs)
        return subprocess.Popen(args, start_new_session=True, **kwargs)


def status(paths: Paths) -> dict:
    st = read_json(paths.state / STATE_FILE)
    if not st:
        return {"running": False, "last": read_json(paths.state / LAST_FILE)}
    age = time.time() - float(st.get("heartbeat", 0))
    st["running"] = age < HEARTBEAT_TIMEOUT
    if not st["running"]:
        st["stale"] = True
    return st


def _log_tail(paths: Paths, lines: int = 15) -> str:
    try:
        text = (paths.state / LOG_FILE).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def start(paths: Paths, cfg: dict, name: str | None = None, device: str | None = None,
          max_minutes: float | None = None) -> dict:
    st = status(paths)
    if st.get("running"):
        raise RecorderError(f"Already recording to {st['file']} (since {st['started_at']}).")
    (paths.state / STATE_FILE).unlink(missing_ok=True)
    (paths.state / STOP_FILE).unlink(missing_ok=True)

    stem = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if name and slug(name):
        stem += f"_{slug(name)}"
    out = paths.recordings / f"{stem}.wav"
    args = [
        sys.executable, "-m", "voicecoach", "record", "_run",
        "--out", str(out),
        "--max-minutes", str(max_minutes or cfg["recording"]["max_minutes"]),
    ]
    if device is not None:
        args += ["--device", str(device)]

    proc = spawn_background(paths, args, paths.state / LOG_FILE)
    deadline = time.time() + 15
    while time.time() < deadline:
        st = status(paths)
        if st.get("running"):
            return st
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise RecorderError("The recorder did not start.\n" + _log_tail(paths))


def run_background(paths: Paths, cfg: dict, out: Path, device_spec: str | None, max_minutes: float) -> None:
    """Body of the detached recorder process."""
    state_file, stop_file = paths.state / STATE_FILE, paths.state / STOP_FILE
    stop_file.unlink(missing_ok=True)
    rc = cfg["recording"]
    device = resolve_device(device_spec if device_spec is not None else rc["device"])
    dev_name = device_name(device)
    started_at = datetime.now().isoformat(timespec="seconds")
    stop_flag = {"v": False}

    def on_signal(*_):
        stop_flag["v"] = True

    for sig in ("SIGTERM", "SIGINT", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), on_signal)
            except (OSError, ValueError):
                pass

    def tick(info: dict) -> None:
        try:  # never let a state-file hiccup stop the recording
            write_json(state_file, {
                "pid": os.getpid(), "file": str(out), "device": dev_name,
                "started_at": started_at, "heartbeat": time.time(), **info,
            })
        except OSError as e:
            print(f"state write failed: {e}", flush=True)

    print(f"{started_at} recording to {out} from {dev_name!r}", flush=True)
    try:
        result = record_to_wav(
            out, device, int(rc["samplerate"]), int(rc["channels"]), float(max_minutes) * 60,
            should_stop=lambda: stop_flag["v"] or stop_file.exists(), on_tick=tick,
        )
        result.update(started_at=started_at, stopped_at=datetime.now().isoformat(timespec="seconds"), device=dev_name)
        write_json(paths.state / LAST_FILE, result)
        print(f"stopped: {result['seconds']} s", flush=True)
    finally:
        state_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)


def stop(paths: Paths, timeout: float = 20.0) -> dict:
    st = status(paths)
    if not st.get("running"):
        if st.get("stale"):
            (paths.state / STATE_FILE).unlink(missing_ok=True)
            raise RecorderError(
                f"The recorder stopped unexpectedly. The partial file is kept at {st.get('file')}.\n" + _log_tail(paths)
            )
        raise RecorderError("Not recording.")
    (paths.state / STOP_FILE).touch()
    deadline = time.time() + timeout
    while (paths.state / STATE_FILE).exists() and time.time() < deadline:
        time.sleep(0.2)
    if (paths.state / STATE_FILE).exists():
        raise RecorderError("The recorder did not stop in time. See data/state/recorder.log.")
    last = read_json(paths.state / LAST_FILE) or {}
    if last.get("file") != st.get("file"):
        last = {"file": st.get("file"), "seconds": st.get("seconds")}
    return last
