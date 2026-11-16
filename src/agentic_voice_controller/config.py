"""Settings, the data-folder layout and small shared helpers."""

from __future__ import annotations

import copy
import functools
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

EXAMPLE_CONFIG_NAME = "config.example.toml"


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
            self.config_file.write_text(default_config_toml(), encoding="utf-8")
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


@functools.cache
def default_config_toml() -> str:
    """The defaults, with a comment on every key: data/config.example.toml, tracked in git.
    It is looked up in the project's data folder, then in the active data folder (Docker
    mounts ./data as /data, so the example comes along)."""
    pr = project_root()
    candidates = [pr / "data" / EXAMPLE_CONFIG_NAME if pr else None, get_paths().root / EXAMPLE_CONFIG_NAME]
    for f in candidates:
        if f and f.is_file():
            return f.read_text(encoding="utf-8")
    looked = ", ".join(str(f) for f in candidates if f)
    raise VoiceControllerError(f"{EXAMPLE_CONFIG_NAME} not found (looked in {looked}).")


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
    cfg = tomllib.loads(default_config_toml())
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
