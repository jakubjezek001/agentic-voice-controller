"""Text-to-speech with the voices the operating system already has, and audio playback.

- macOS: `say`. Better voices: System Settings > Accessibility > Spoken Content >
  System Voice > Manage Voices. "Premium" and "Enhanced" voices sound far more natural.
- Windows: SAPI voices through PowerShell. More voices: Settings > Time & language >
  Speech (or Language & region > add a language with speech).
- Linux: espeak-ng (`sudo apt install espeak-ng`).

Every engine renders to a WAV file. The `Player` plays it, so interrupting, measuring
what was heard and muting the microphone while the agent talks work the same on all
platforms.
"""

from __future__ import annotations

import base64
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

from agentic_voice_controller.config import VoiceControllerError, load_dotenv

TTS_RATE = 22050
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes on Windows

# macOS novelty voices, never picked automatically
_NOVELTY = {
    "albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "good news", "jester",
    "organ", "superstar", "trinoids", "whisper", "wobble", "zarvox", "fred", "junior", "kathy", "ralph",
}
# macOS "Eloquence" voices: understandable but robotic, picked only when nothing else exists
_ELOQUENCE = ("eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley")
_PREFERRED = [
    "ava", "zoe", "evan", "allison", "nathan", "joelle", "noelle", "tom", "susan", "samantha",
    "daniel", "serena", "karen", "moira", "tessa", "zuzana", "anna", "petra", "markus", "thomas",
    "amélie", "monica", "alice", "zosia", "milena", "alex",
]
_EN_LOCALES = ["en_US", "en_GB", "en_AU", "en_IE", "en_CA", "en_ZA", "en_IN"]


class TTSError(VoiceControllerError):
    pass


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW, **kw)


def _powershell(script: str, env: dict | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise TTSError("PowerShell was not found, so Windows voices cannot be used.")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return _run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                env=dict(os.environ, **(env or {})), timeout=timeout)


# --- text preparation ------------------------------------------------------------

def clean_for_speech(text: str) -> str:
    """Drop markdown and other things that sound bad when read aloud."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"^\s{0,3}(#+|[-*+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", t)
    t = t.replace("->", " to ").replace("=>", " to ").replace("&", " and ")
    return re.sub(r"\s+", " ", t).strip()


def split_for_speech(text: str) -> list[str]:
    """First sentence alone (so speech starts quickly), the rest in chunks of a few sentences."""
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s]
    if len(sentences) <= 1:
        return sentences
    chunks, cur = [sentences[0]], ""
    for s in sentences[1:]:
        cur = f"{cur} {s}".strip()
        if len(cur) >= 160:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


_STOPWORDS = {
    "en": "the and is are you to of that it what this for with would be have not we your can do how about or so if i "
          "it's that's don't there they which then could should",
    "cs": "a je to se na že v ve jsem jsi co jak ale tak by bych bychom pro s z mi ti už nebo když protože které který "
          "jste si tohle máš můžeš myslím jsou být bude také jako",
    "sk": "a je to sa na že v vo som si čo ako ale tak by pre s z mi ti už alebo keď pretože ktoré ktorý ste toto "
          "môžeš myslím sú byť bude tiež",
    "de": "der die das und ist nicht ich du wir sie zu mit auf für ein eine es auch wie was oder aber wenn dass",
    "es": "el la los las y es no yo tú que de en un una por para con como pero si",
    "fr": "le la les et est ne pas je tu nous vous que de en un une pour avec comme mais si",
    "pl": "i jest to się na że w nie co jak ale tak by dla z mi ci już lub bo które który są być",
}
_STOPWORDS = {k: set(v.split()) for k, v in _STOPWORDS.items()}
_CS_ONLY = set("ěřů")


def guess_language(text: str, candidates: list[str], default: str) -> str:
    """Which of the candidate languages a reply is written in (stop words), so it gets
    a voice that can pronounce it. Falls back to `default` when unsure."""
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text.lower())
    scores = {lang: sum(w in _STOPWORDS[lang] for w in words) for lang in candidates if lang in _STOPWORDS}
    if "cs" in scores and any(ch in _CS_ONLY for ch in text.lower()):
        scores["cs"] += 3
    if not scores or max(scores.values()) < 2:
        return default
    best = max(scores, key=lambda k: scores[k])
    if default in scores and scores[best] <= scores[default] + 1:
        return default
    return best


# --- voices ------------------------------------------------------------------------

_SAY_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}_[A-Za-z0-9]+)\s+#")


def _quality(name: str) -> str:
    low = name.lower()
    return "premium" if "(premium)" in low else "enhanced" if "(enhanced)" in low else ""


@lru_cache(maxsize=1)
def list_voices() -> tuple[dict, ...]:
    voices: list[dict] = []
    if sys.platform == "darwin":
        out = _run(["say", "-v", "?"]).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            m = _SAY_LINE.match(line)
            if m:
                name, locale = m["name"].strip(), m["locale"]
                voices.append({"name": name, "locale": locale, "lang": locale.split("_")[0], "quality": _quality(name)})
    elif os.name == "nt":
        script = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            "foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled) {"
            " [Console]::Out.WriteLine($v.VoiceInfo.Name + \"`t\" + $v.VoiceInfo.Culture.Name) } }\n"
        )
        out = _powershell(script, timeout=60).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            if "\t" in line:
                name, culture = line.rstrip("\r").split("\t", 1)
                locale = culture.replace("-", "_")
                voices.append({"name": name, "locale": locale, "lang": locale.split("_")[0].lower(),
                               "quality": "natural" if "natural" in name.lower() else ""})
    else:
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if exe:
            out = _run([exe, "--voices"]).stdout.decode("utf-8", "replace")
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    code = parts[1]
                    voices.append({"name": code, "locale": code, "lang": code.split("-")[0], "quality": ""})
    return tuple(voices)


def is_novelty(name: str) -> bool:
    return name.lower().split(" (")[0] in _NOVELTY


def _rank(v: dict, lang: str) -> tuple:
    low = v["name"].lower()
    base = low.split(" (")[0]
    q = {"premium": 0, "natural": 0, "enhanced": 1}.get(v["quality"], 2)
    eloquence = 1 if base.startswith(_ELOQUENCE) else 0
    pref = _PREFERRED.index(base) if base in _PREFERRED else len(_PREFERRED)
    if lang == "en":
        loc = _EN_LOCALES.index(v["locale"]) if v["locale"] in _EN_LOCALES else len(_EN_LOCALES)
    else:
        loc = 0 if v["locale"].lower().endswith("_" + lang) else 1
    return (eloquence, q, loc, pref, low)


def pick_voice(lang: str, configured: dict | None = None) -> str | None:
    """The voice for a language: the configured one, else the best installed one.
    None = the engine's default voice."""
    voices = list_voices()
    want = str((configured or {}).get(lang) or "").strip()
    if want:
        for v in voices:
            if v["name"].lower() == want.lower():
                return v["name"]
        for v in voices:
            if want.lower() in v["name"].lower():
                return v["name"]
    cands = [v for v in voices if v["lang"] == lang and not is_novelty(v["name"])]
    if not cands:
        return None
    return sorted(cands, key=lambda v: _rank(v, lang))[0]["name"]


# --- synthesis -------------------------------------------------------------------

def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise TTSError(f"unexpected sample width {width} from the speech engine")
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


class SystemTTS:
    def __init__(self, cfg: dict):
        c = cfg.get("conversation", {})
        self.rate = float(c.get("speech_rate", 1.0) or 1.0)
        self.voices = dict(c.get("voices") or {})
        self._picked: dict[str, str | None] = {}

    def voice_for(self, lang: str) -> str | None:
        if lang not in self._picked:
            self._picked[lang] = pick_voice(lang, self.voices)
        return self._picked[lang]

    def check(self) -> None:
        if sys.platform == "darwin":
            if not shutil.which("say"):
                raise TTSError("macOS `say` was not found.")
        elif os.name == "nt":
            if not (shutil.which("powershell") or shutil.which("pwsh")):
                raise TTSError("PowerShell was not found.")
        elif not (shutil.which("espeak-ng") or shutil.which("espeak")):
            raise TTSError("No speech engine: install espeak-ng (sudo apt install espeak-ng).")

    def synth(self, text: str, lang: str) -> tuple[np.ndarray, int]:
        voice = self.voice_for(lang)
        with tempfile.TemporaryDirectory(prefix="vc-tts-") as td:
            txt, out = Path(td) / "text.txt", Path(td) / "speech.wav"
            txt.write_text(text, encoding="utf-8")
            if sys.platform == "darwin":
                args = ["say", "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{TTS_RATE}", "-f", str(txt)]
                if voice:
                    args += ["-v", voice]
                if abs(self.rate - 1.0) > 0.01:
                    args += ["-r", str(int(185 * self.rate))]
                r = _run(args, timeout=120)
            elif os.name == "nt":
                sapi_rate = max(-10, min(10, round(10 * math.log(self.rate) / math.log(3))))
                script = (
                    "Add-Type -AssemblyName System.Speech\n"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                    "if ($env:VC_VOICE) { $s.SelectVoice($env:VC_VOICE) }\n"
                    "$s.Rate = [int]$env:VC_RATE\n"
                    "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
                    f"{TTS_RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
                    " [System.Speech.AudioFormat.AudioChannel]::Mono)\n"
                    "$s.SetOutputToWaveFile($env:VC_OUT, $fmt)\n"
                    "$s.Speak([IO.File]::ReadAllText($env:VC_TEXT, [Text.Encoding]::UTF8))\n"
                    "$s.Dispose()\n"
                )
                r = _powershell(script, env={"VC_TEXT": str(txt), "VC_OUT": str(out),
                                             "VC_VOICE": voice or "", "VC_RATE": str(sapi_rate)})
            else:
                exe = shutil.which("espeak-ng") or shutil.which("espeak")
                if not exe:
                    raise TTSError("No speech engine: install espeak-ng (sudo apt install espeak-ng).")
                args = [exe, "-w", str(out), "-f", str(txt), "-s", str(int(175 * self.rate))]
                if voice or lang:
                    args += ["-v", voice or lang]
                r = _run(args, timeout=120)
            if r.returncode != 0 or not out.exists():
                err = (r.stderr or b"").decode("utf-8", "replace").strip()
                raise TTSError(f"speech synthesis failed: {err or r.returncode}")
            return _read_wav(out)


class ElevenLabsTTS:
    """ElevenLabs voices over their HTTP API (only the text of the replies leaves this machine).

    The API key comes from the ELEVENLABS_API_KEY environment variable, never from a file.
    If the key or voice id is missing, the network fails or the credits run out, speech
    falls back to the system voice, so a conversation never goes silent.
    """

    API = "https://api.elevenlabs.io/v1/text-to-speech"
    RATE = 22050

    def __init__(self, cfg: dict):
        c = cfg.get("conversation", {})
        self.voice_id = str(c.get("elevenlabs_voice_id") or "").strip()
        self.model = str(c.get("elevenlabs_model") or "eleven_flash_v2_5").strip()
        load_dotenv()
        self.key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        self.fallback = SystemTTS(cfg)
        self.chars_sent = 0
        self.disabled = ""  # reason, once ElevenLabs is no longer used in this session
        if not self.key:
            self.disabled = "ELEVENLABS_API_KEY is not set"
        elif not self.voice_id:
            self.disabled = "conversation.elevenlabs_voice_id is empty in data/config.toml"

    def voice_for(self, lang: str) -> str | None:
        if self.disabled:
            return self.fallback.voice_for(lang)
        return f"elevenlabs:{self.voice_id}"

    def check(self) -> None:
        if self.disabled:
            print(f"note: ElevenLabs is off ({self.disabled}); using the system voice.", file=sys.stderr)
        self.fallback.check()

    def _request(self, text: str, lang: str) -> np.ndarray:
        import json
        import urllib.error
        import urllib.request

        body = {"text": text, "model_id": self.model}
        req = urllib.request.Request(
            f"{self.API}/{self.voice_id}?output_format=pcm_{self.RATE}",
            data=json.dumps(body).encode("utf-8"),
            headers={"xi-api-key": self.key, "Content-Type": "application/json", "Accept": "audio/pcm"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code in (401, 403):
                self.disabled = "the API key was rejected or lacks the text-to-speech permission"
            elif e.code in (402, 429) and ("quota" in detail.lower() or "credit" in detail.lower()):
                self.disabled = "the ElevenLabs credits for this period are used up"
            raise TTSError(f"ElevenLabs returned HTTP {e.code}: {detail}") from e
        if len(raw) < 2:
            raise TTSError("ElevenLabs returned no audio")
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0

    def synth(self, text: str, lang: str) -> tuple[np.ndarray, int]:
        if not self.disabled:
            try:
                audio = self._request(text, lang)
                self.chars_sent += len(text)
                return audio, self.RATE
            except (TTSError, OSError) as e:
                print(f"note: ElevenLabs failed ({e}); using the system voice for this part.", file=sys.stderr)
        return self.fallback.synth(text, lang)


class SilentTTS:
    """For tests: 'speaks' silence of a plausible length."""

    def __init__(self, cfg: dict | None = None):
        pass

    def voice_for(self, lang: str) -> str | None:
        return None

    def check(self) -> None:
        pass

    def synth(self, text: str, lang: str) -> tuple[np.ndarray, int]:
        seconds = max(0.5, len(text.split()) / 2.8)
        return np.zeros(int(seconds * TTS_RATE), dtype=np.float32), TTS_RATE


def make_tts(cfg: dict, engine: str | None = None):
    engine = (engine or cfg.get("conversation", {}).get("tts") or "system").lower()
    if engine == "silent":
        return SilentTTS(cfg)
    if engine == "system":
        return SystemTTS(cfg)
    if engine == "elevenlabs":
        return ElevenLabsTTS(cfg)
    raise TTSError(f"Unknown text-to-speech engine {engine!r} (use \"system\" or \"elevenlabs\").")


# --- playback --------------------------------------------------------------------

def _resample(a: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return a.astype(np.float32)
    from scipy.signal import resample_poly

    g = math.gcd(sr, target)
    return resample_poly(a, target // g, sr // g).astype(np.float32)


def resolve_output_device(spec) -> int | None:
    if spec is None or str(spec).strip() == "":
        return None
    if str(spec).strip().isdigit():
        return int(spec)
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and str(spec).lower() in d["name"].lower():
            return i
    raise TTSError(f"No output device matches {spec!r}.")


class Player:
    """Plays speech chunk by chunk while later chunks are still being synthesized.
    `stop()` (from any thread) cuts it off; the result says what was heard."""

    def __init__(self, device: int | None = None, silent: bool = False):
        self.device = device
        self.silent = silent
        self._stop = threading.Event()
        self.lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def output_rate(self) -> int:
        if self.silent:
            return TTS_RATE
        import sounddevice as sd

        return int(sd.query_devices(self.device, "output")["default_samplerate"])

    def play_chunks(
        self,
        chunks: list[str],
        synth: Callable[[str], tuple[np.ndarray, int]],
        on_audible: Callable[[], None] | None = None,
    ) -> dict:
        """Blocking. Returns {interrupted, started: number of chunks begun, seconds}."""
        self._stop.clear()
        out_sr = self.output_rate()
        pending: deque[tuple[int, np.ndarray]] = deque()
        produced = threading.Event()
        errors: list[Exception] = []

        def producer() -> None:
            try:
                for i, text in enumerate(chunks):
                    if self._stop.is_set():
                        break
                    a, sr = synth(text)
                    pending.append((i, _resample(a, sr, out_sr)))
            except Exception as e:  # reported after playback
                errors.append(e)
            finally:
                produced.set()

        threading.Thread(target=producer, daemon=True).start()
        state = {"cur": None, "pos": 0, "started": 0, "played": 0, "audible": False}
        finished = threading.Event()

        def fill(out: np.ndarray) -> bool:
            """Fill `out`; return True when everything has been played."""
            n = 0
            while n < len(out) and not self._stop.is_set():
                if state["cur"] is None:
                    if not pending:
                        break
                    i, a = pending.popleft()
                    state.update(cur=a, pos=0, started=i + 1)
                    if not state["audible"]:
                        state["audible"] = True
                        if on_audible:
                            on_audible()
                cur, pos = state["cur"], state["pos"]
                take = min(len(out) - n, len(cur) - pos)
                out[n:n + take] = cur[pos:pos + take]
                n += take
                state["pos"] = pos + take
                state["played"] += take
                if state["pos"] >= len(cur):
                    state["cur"] = None
            out[n:] = 0
            return self._stop.is_set() or (produced.is_set() and state["cur"] is None and not pending)

        if self.silent:
            import time

            block = np.zeros(int(out_sr * 0.05), dtype=np.float32)
            while not fill(block):
                time.sleep(0.05)
        else:
            import sounddevice as sd

            def callback(outdata, frames, time_info, status):
                if fill(outdata[:, 0]):
                    raise sd.CallbackStop

            with sd.OutputStream(samplerate=out_sr, channels=1, dtype="float32", device=self.device,
                                 callback=callback, finished_callback=finished.set):
                while not finished.wait(0.1):
                    pass
        if errors and not state["started"]:
            raise errors[0]
        return {"interrupted": self._stop.is_set(), "started": state["started"],
                "seconds": round(state["played"] / out_sr, 2)}

    def tone(self, freqs: tuple[float, ...] = (660.0, 880.0), seconds: float = 0.07, volume: float = 0.12) -> None:
        if self.silent or not self.lock.acquire(blocking=False):
            return
        try:
            sr = self.output_rate()
            t = np.arange(int(sr * seconds)) / sr
            fade = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.01)
            a = np.concatenate([np.sin(2 * np.pi * f * t) * fade * volume for f in freqs]).astype(np.float32)
            import sounddevice as sd

            sd.play(a, sr, device=self.device, blocking=True)
        except Exception:
            pass
        finally:
            self.lock.release()
