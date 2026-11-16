"""Settings, the data-folder layout and small shared helpers."""

from __future__ import annotations

import copy
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
    ".webm", ".mp4", ".amr", ".3gp", ".caf", ".aiff", ".aif", ".wma",
}

DEFAULT_CONFIG_TOML = """\
# Agentic Voice Controller settings. Edit freely: missing keys fall back to built-in defaults.

[general]
# Language of your calls: "" = auto-detect, or a code like "en", "cs", "de".
language = ""
# How many people usually talk in a recording (1 = solo practice).
speakers = 2
# "auto" = NVIDIA GPU (cuda) if present, else Apple GPU (mps), else CPU.
# Force "cpu" if a GPU causes trouble.
device = "auto"

[transcription]
# faster-whisper model: tiny, base, small, medium, large-v3, large-v3-turbo.
# Bigger is more accurate and slower. "small" is a good start on a laptop.
model = "small"
# "auto" = int8 on CPU, float16 on a GPU.
compute_type = "auto"
beam_size = 5
# Ask Whisper to keep "um"/"uh" (it drops them by default). Used only when language = "en".
keep_fillers = true

[speakers]
model = "microsoft/wavlm-base-plus-sv"
window_seconds = 2.0
hop_seconds = 1.0
# Two speaker clusters this similar are treated as one person.
merge_similarity = 0.92

[emotion]
# Generic speech-emotion model (labels: neutral, happy, angry, sad).
model = "superb/wav2vec2-base-superb-er"
# Speech is scored in chunks of about this many seconds.
segment_seconds = 6.0
# Also score the other speakers with the generic model (your personal model only knows you).
analyze_others = true
# Hidden layers whose averaged output feeds your personal emotion classifier.
feature_layers = [6, 12]
# A voice-bank label needs at least this many clips to be trained.
min_clips_per_label = 3

[recording]
# Input device: "" = system default, or a name substring / index from `vc devices`.
device = ""
# Incoming call audio: a loopback input such as "BlackHole 2ch" (macOS) or "CABLE Output" /
# "Stereo Mix" (Windows). Set it to record BOTH sides as two channels of one file:
# channel 0 = your microphone, channel 1 = the incoming audio. "" = microphone only.
# `vc record start --system-device <name>` overrides it, `--system-device off` disables it.
system_device = ""
samplerate = 16000
channels = 1
max_minutes = 180

[conversation]
# Conversation mode (`vc talk`, the voice-discuss skill): talk out loud with the agent.
# Speech recognition model for your side; "" = the [transcription] model.
stt_model = ""
beam_size = 3
# Languages you use in conversations, e.g. ["cs", "en"]: detection then only picks
# among these. Empty = any language. Ignored when [general] language is set.
languages = []
# Seconds of silence that end your turn. Raise it if the agent cuts in while you think.
end_silence_seconds = 1.4
# Speech shorter than this (coughs, "hm") is ignored.
min_speech_seconds = 0.4
# Let you interrupt the agent by talking over it. Turn on only with headphones:
# through speakers the agent would hear itself.
barge_in = false
# A short sound when your turn has been captured.
earcon = true
# Text-to-speech: "system" = macOS `say`, Windows SAPI voices, Linux espeak-ng.
tts = "system"
# "elevenlabs" = natural voices from ElevenLabs (free tier available). Only the text of the
# agent's replies is sent, never your audio. The API key is read from the environment
# variable ELEVENLABS_API_KEY (never put it in this file). Falls back to the system voice.
elevenlabs_voice_id = ""
# "eleven_flash_v2_5" = fast and cheap; "eleven_multilingual_v2" = best quality.
elevenlabs_model = "eleven_flash_v2_5"
# 1.0 = the voice's normal speed.
speech_rate = 1.0
# Output device: "" = system default, or a name substring / index.
output_device = ""
# Stop listening when the agent has not asked for a turn for this long.
idle_minutes = 5
max_minutes = 180
# Keep your side of the conversation as mic.wav (needed for `vc talk stop --analyze`).
keep_audio = true

[conversation.voices]
# Voice per language, a name from `vc talk voices`. Missing = best installed voice.
# en = "Ava (Premium)"
# cs = "Zuzana (Premium)"

[analysis]
# Gaps inside your turn longer than this count as pauses.
pause_seconds = 0.6
long_pause_seconds = 2.0

[analysis.fillers]
en = ["um", "uh", "erm", "er", "hmm", "mm", "like", "basically", "actually", "literally", "you know", "i mean", "kind of", "sort of"]
cs = ["ehm", "hm", "eee", "jako", "prostě", "vlastně", "takže", "tak nějak", "jakoby", "no"]
"""


class VoiceControllerError(Exception):
    """An error with a message meant for the user."""


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def recordings(self) -> Path:
        return self.root / "recordings"

    @property
    def sessions(self) -> Path:
        return self.root / "sessions"

    @property
    def conversations(self) -> Path:
        return self.root / "conversations"

    @property
    def voicebank(self) -> Path:
        return self.root / "voicebank"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    def ensure(self) -> "Paths":
        for p in (
            self.inbox, self.inbox / "failed", self.recordings, self.sessions, self.conversations,
            self.voicebank / "_voice", self.models, self.cache, self.state,
        ):
            p.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.config_file.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return self


def project_root() -> Path | None:
    here = Path(__file__).resolve().parents[2]
    return here if (here / "pyproject.toml").exists() else None


def get_paths() -> Paths:
    env = os.environ.get("VC_DATA")
    if env:
        root = Path(env).expanduser()
    else:
        pr = project_root()
        root = pr / "data" if pr else Path.cwd() / "data"
    return Paths(root.resolve())


def load_dotenv() -> None:
    """Read KEY=value lines from `.env` in the project root (and in the data folder) into
    os.environ. Real environment variables win; empty values are ignored."""
    pr = project_root()
    for f in [pr / ".env" if pr else None, get_paths().root / ".env"]:
        if not f or not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.removeprefix("export ").strip(), value.strip().strip("\"'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value


def setup_env(paths: Paths) -> None:
    """Keep every downloaded model inside data/cache, and keep the libraries quiet."""
    os.environ.setdefault("HF_HOME", str(paths.cache / "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    import logging
    import warnings

    warnings.filterwarnings("ignore", category=UserWarning, module=r"torch\..*")
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)


def hf_repo_cached(paths: Paths, repo_id: str) -> bool:
    snap = paths.cache / "hf" / "hub" / f"models--{repo_id.replace('/', '--')}" / "snapshots"
    return snap.exists() and any(snap.iterdir())


def whisper_repo(name: str) -> str:
    if "/" in name:
        return name
    if name.startswith("large-v3-turbo") or name == "turbo":
        return "mobiuslabs/faster-whisper-large-v3-turbo"
    if name.startswith("distil-"):
        return f"Systran/faster-distil-whisper-{name[len('distil-'):]}"
    return f"Systran/faster-whisper-{name}"


def go_offline_if_cached(paths: Paths, cfg: dict) -> bool:
    """After setup, run without touching the network (no hub checks, fully private).
    Must run before huggingface_hub is imported."""
    repos = [whisper_repo(cfg["transcription"]["model"]), cfg["speakers"]["model"], cfg["emotion"]["model"]]
    if all(hf_repo_cached(paths, r) for r in repos):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        return True
    return False


def _deep_merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(paths: Paths) -> dict[str, Any]:
    cfg = tomllib.loads(DEFAULT_CONFIG_TOML)
    if paths.config_file.exists():
        try:
            user = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise VoiceControllerError(f"{paths.config_file} is not valid TOML: {e}") from e
        _deep_merge(cfg, user)
    return cfg


def with_overrides(cfg: dict, **general: Any) -> dict:
    out = copy.deepcopy(cfg)
    for k, v in general.items():
        if v is not None:
            out["general"][k] = v
    return out


def torch_device(cfg: dict) -> str:
    want = str(cfg["general"].get("device", "cpu")).lower()
    if want == "auto":
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    return want


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def _json_default(o: Any) -> Any:
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def write_json(path: Path, data: Any) -> None:
    """Atomic write. On Windows the replace fails while another process has the file
    open (e.g. `vc record status` reading the recorder state), so retry briefly."""
    import time

    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def read_json(path: Path, default: Any = None) -> Any:
    import time

    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (PermissionError, json.JSONDecodeError):
            time.sleep(0.05)
    return default
