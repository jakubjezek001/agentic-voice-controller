"""Watch data/inbox and analyze every audio file that lands there (the Docker service runs this)."""

from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path

from voicecoach.config import AUDIO_EXTENSIONS, Paths, log


def _candidates(paths: Paths) -> list[Path]:
    return sorted(
        f for f in paths.inbox.iterdir()
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def watch(paths: Paths, cfg: dict, interval: float = 10.0, once: bool = False) -> int:
    from voicecoach.pipeline import analyze

    log(f"watching {paths.inbox} (every {interval:.0f} s)")
    sizes: dict[Path, int] = {}
    processed = 0
    while True:
        for f in _candidates(paths):
            try:
                size = f.stat().st_size
            except FileNotFoundError:
                continue
            if not once and sizes.get(f) != size:  # wait until the file stops growing
                sizes[f] = size
                continue
            sizes.pop(f, None)
            log(f"new recording: {f.name}")
            try:
                analyze(f, paths, cfg, move=True)
                processed += 1
            except FileNotFoundError:
                continue  # another worker took it
            except Exception:
                log("analysis failed:\n" + traceback.format_exc())
                if f.exists():
                    shutil.move(str(f), str(paths.inbox / "failed" / f.name))
        if once:
            return processed
        time.sleep(interval)
