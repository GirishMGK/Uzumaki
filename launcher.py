"""
Uzumaki.exe entry point.

Two jobs, in order:
  1. Self-update check — compare the version baked into this build against the
     latest published on GitHub; if newer, download the new .exe, hand off to
     a tiny helper script that replaces this file once the process exits, and
     relaunch. Skipped entirely when not running as a frozen exe (i.e. during
     `python launcher.py` from source) or when offline — a failed update
     check must never block launching the app.
  2. Launch the Streamlit hub (Home.py) in-process and open the browser.

This file is the sole PyInstaller entry point (see Uzumaki.spec) — everything
else (Home.py, _pages/, tools/, redaction_tool/, je_audit_tool/,
form26as_tool/, and the root-level extractor modules) ships as bundled data
alongside it, preserving the exact relative layout the source repo already
uses, so none of that code needs to know it's running frozen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

OWNER = "GirishMGK"
REPO = "Uzumaki"
RELEASE_TAG = "latest"
_GH_RELEASE_BASE = f"https://github.com/{OWNER}/{REPO}/releases/download/{RELEASE_TAG}"
_VERSION_URL = f"{_GH_RELEASE_BASE}/version.txt"
_EXE_URL = f"{_GH_RELEASE_BASE}/Uzumaki.exe"
_TIMEOUT = 6  # seconds — an update check must never meaningfully delay launch


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> str:
    """Directory containing the bundled app resources (Home.py, _pages/, ...)."""
    if _is_frozen():
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _local_version() -> str:
    try:
        with open(os.path.join(base_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0-dev"


def _remote_version() -> str | None:
    # Broad except by design: a failed/garbled update check (bad encoding,
    # malformed response, proxy captive portal, ...) must never block launch.
    try:
        with urllib.request.urlopen(_VERSION_URL, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


def _download(url: str, dest: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
            out.write(resp.read())
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        return False
    return _looks_like_valid_exe(dest)


def _looks_like_valid_exe(path: str) -> bool:
    """Minimal integrity check before the swap-in helper deletes the running
    exe — a truncated/corrupt download must never brick the install."""
    try:
        if os.path.getsize(path) < 1_000_000:  # a real build is tens of MB
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"  # Windows PE header magic
    except OSError:
        return False


def _self_update_and_relaunch() -> None:
    """Download the new exe, swap it in via a detached helper, then exit."""
    exe_path = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe_path)
    new_path = os.path.join(exe_dir, "Uzumaki_new.exe")

    print("Update available — downloading…")
    if not _download(_EXE_URL, new_path):
        print("Update download failed — continuing with the current version.")
        return

    if sys.platform.startswith("win"):
        helper = os.path.join(tempfile.gettempdir(), "uzumaki_update.bat")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(
                "@echo off\r\n"
                "setlocal enabledelayedexpansion\r\n"
                "set tries=0\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f':retry\r\n'
                f'del /f /q "{exe_path}" 2>nul\r\n'
                f'if exist "{exe_path}" (\r\n'
                "  set /a tries+=1\r\n"
                "  if !tries! geq 15 goto giveup\r\n"
                "  timeout /t 1 /nobreak >nul\r\n"
                "  goto retry\r\n"
                ")\r\n"
                f'move /y "{new_path}" "{exe_path}" >nul\r\n'
                f'start "" "{exe_path}"\r\n'
                'del /f /q "%~f0"\r\n'
                "goto :eof\r\n"
                ":giveup\r\n"
                # exe stayed locked (e.g. AV scan) — abandon the swap and
                # relaunch the still-working current build instead of hanging.
                f'del /f /q "{new_path}" 2>nul\r\n'
                f'start "" "{exe_path}"\r\n'
                'del /f /q "%~f0"\r\n'
            )
        subprocess.Popen(
            ["cmd", "/c", helper],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        # Non-Windows dev/test fallback: simple replace-and-relaunch, no
        # helper script needed since the OS allows overwriting a running file.
        os.replace(new_path, exe_path)
        os.chmod(exe_path, 0o755)
        subprocess.Popen([exe_path])

    print("Restarting with the new version…")
    sys.exit(0)


def check_for_update() -> None:
    if not _is_frozen():
        return  # nothing to self-replace when running from source
    try:
        remote = _remote_version()
        if remote is None:
            return  # offline or GitHub unreachable — just launch what we have
        if remote != _local_version():
            _self_update_and_relaunch()
    except SystemExit:
        raise  # the successful-update path calls sys.exit(0) — let it through
    except Exception as e:
        # The self-update path must never prevent the app from launching.
        print(f"Update check failed ({e}) — continuing with the current version.")


def run_app() -> None:
    home = os.path.join(base_dir(), "Home.py")
    sys.argv = [
        "streamlit", "run", home,
        "--server.headless=false",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]
    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


if __name__ == "__main__":
    check_for_update()
    run_app()
