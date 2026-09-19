"""Resolves the app's own version string from the single `VERSION` file at
the repo root — the same file `desktop/installer.iss` reads to stamp the
Windows installer, so a release is "bump VERSION, commit, tag `vX.Y.Z`
matching it" with exactly one number to keep in sync, not two.

Frozen (PyInstaller) build: `desktop/firm_rms.spec` bundles VERSION as a
data file at the bundle root, found via `sys._MEIPASS`. Unfrozen (normal
dev run, docker-compose, tests): walks up from this file to find the repo
root's VERSION. Never raises — an app that can't find its own version
number should still start; it just reports "0.0.0-dev".
"""
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache
def get_app_version() -> str:
    candidates: list[Path] = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "VERSION")  # type: ignore[attr-defined]
    # backend/app/core/version.py -> backend/app/core -> backend/app -> backend -> repo root
    candidates.append(Path(__file__).resolve().parents[3] / "VERSION")

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0-dev"
