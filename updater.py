"""
Uzumaki self-update logic — shared by launcher.py (checked automatically at
every launch) and the in-app "Check for Updates" control in Home.py's
sidebar (checked/triggered on demand while the app is already running).

Deliberately a plain module, NOT the PyInstaller entry script: launcher.py
is compiled into the frozen exe as "__main__", not as an importable
"launcher" module, so code elsewhere in the app can't reliably `import
launcher` inside the frozen build even though that works fine when running
from source. Splitting the actual update logic out here avoids that trap.

Two call shapes:
  - check_for_update() -- launcher.py's existing at-launch behavior:
    check, and if newer, update-and-relaunch immediately, all before
    Streamlit even starts. Unchanged from before this file existed.
  - check_update_status() / perform_update_and_restart() -- for the in-app
    control: check without side effects (so the UI can show "up to date"
    vs "update available, click to install"), then only actually download
    and restart when the user clicks a button.

perform_update_and_restart(), unlike the at-launch path, runs while
Streamlit's server is already serving the page that called it. sys.exit()
there would only stop that one script rerun (Streamlit's script runner
catches SystemExit to end the rerun, not the whole process) -- the swap
helper script needs the whole exe's process to actually be gone before it
can delete/replace the file. So this path uses os._exit() instead: an
immediate, unconditional process termination with no cleanup, which is
exactly what's needed here (the helper script already retries the delete
for a few seconds specifically to tolerate this).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

OWNER = "GirishMGK"
REPO = "Uzumaki"
RELEASE_TAG = "latest"
_GH_RELEASE_BASE = f"https://github.com/{OWNER}/{REPO}/releases/download/{RELEASE_TAG}"
_VERSION_URL = f"{_GH_RELEASE_BASE}/version.txt"
_EXE_URL = f"{_GH_RELEASE_BASE}/Uzumaki.exe"
_TIMEOUT = 6  # seconds — a background check must never meaningfully delay anything


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> str:
    """Directory containing the bundled app resources (Home.py, _pages/, ...)."""
    if is_frozen():
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def local_version() -> str:
    try:
        with open(os.path.join(base_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0-dev"


def remote_version() -> str | None:
    try:
        with urllib.request.urlopen(_VERSION_URL, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


_DOWNLOAD_CHUNK_SIZE = 256 * 1024  # 256 KiB


def _download(url: str, dest: str, on_progress=None) -> bool:
    """Streams the download in chunks instead of one resp.read() call, so
    `on_progress(bytes_downloaded, total_bytes)` -- when given -- can be
    called as it goes, for a real progress bar rather than a spinner with
    no feedback. `total_bytes` is None if the server didn't send a
    Content-Length header (progress then has to show bytes-so-far only,
    no percentage)."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
            total = resp.headers.get("Content-Length")
            total_bytes = int(total) if total is not None and total.isdigit() else None
            downloaded = 0
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total_bytes)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def check_update_status() -> dict:
    """No side effects -- just reports what's available, for the UI to render.

    Returns a dict:
        {"local": str, "remote": str | None, "update_available": bool,
         "checked": bool}
    "checked" is False when running from source (nothing to self-replace)
    or when the remote check itself failed (offline/GitHub unreachable) --
    in both cases "update_available" is always False too.
    """
    local = local_version()
    if not is_frozen():
        return {"local": local, "remote": None, "update_available": False, "checked": False}
    remote = remote_version()
    if remote is None:
        return {"local": local, "remote": None, "update_available": False, "checked": False}
    return {"local": local, "remote": remote, "update_available": remote != local, "checked": True}


def _write_and_launch_helper(exe_path: str, new_path: str) -> bool:
    """Spawns the detached swap-and-relaunch helper. Returns True if launched."""
    if sys.platform.startswith("win"):
        helper = os.path.join(tempfile.gettempdir(), "uzumaki_update.bat")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(
                "@echo off\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                ":retry\r\n"
                f'del /f /q "{exe_path}" 2>nul\r\n'
                f'if exist "{exe_path}" (\r\n'
                "  timeout /t 1 /nobreak >nul\r\n"
                "  goto retry\r\n"
                ")\r\n"
                f'move /y "{new_path}" "{exe_path}" >nul\r\n'
                f'start "" "{exe_path}"\r\n'
                'del /f /q "%~f0"\r\n'
            )
        subprocess.Popen(
            ["cmd", "/c", helper],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    else:
        # Non-Windows dev/test fallback: simple replace-and-relaunch, no
        # helper script needed since the OS allows overwriting a running file.
        os.replace(new_path, exe_path)
        os.chmod(exe_path, 0o755)
        subprocess.Popen([exe_path])
        return True


def self_update_and_relaunch() -> None:
    """At-launch path (launcher.py, before Streamlit starts): download,
    hand off to the swap helper, then exit cleanly with sys.exit() -- safe
    here since nothing else is running yet."""
    exe_path = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe_path)
    new_path = os.path.join(exe_dir, "Uzumaki_new.exe")

    print("Update available — downloading…")
    if not _download(_EXE_URL, new_path):
        print("Update download failed — continuing with the current version.")
        return

    _write_and_launch_helper(exe_path, new_path)
    print("Restarting with the new version…")
    sys.exit(0)


def check_for_update() -> None:
    """launcher.py's at-launch entry point — unchanged behavior."""
    if not is_frozen():
        return  # nothing to self-replace when running from source
    remote = remote_version()
    if remote is None:
        return  # offline or GitHub unreachable — just launch what we have
    if remote != local_version():
        self_update_and_relaunch()


def perform_update_and_restart(on_progress=None) -> tuple[bool, str]:
    """In-app path (called from a Streamlit button click, mid-session):
    download the new exe, hand off to the swap helper, then hard-exit the
    whole process immediately via os._exit() -- sys.exit() here would only
    end the current Streamlit script rerun, not the process the helper
    script needs to see disappear before it can replace the exe.

    `on_progress(bytes_downloaded, total_bytes)`, if given, is called as the
    download streams in (see _download()) -- lets the caller show a real
    progress bar instead of an indefinite spinner. `total_bytes` is None if
    the server didn't send a Content-Length header.

    Returns (started, message) -- only returns at all if something failed
    before the point of no return (e.g. download failure); on success this
    function does not return, the process exits.
    """
    if not is_frozen():
        return False, "Not running as a packaged .exe — nothing to self-update here."

    exe_path = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe_path)
    new_path = os.path.join(exe_dir, "Uzumaki_new.exe")

    if not _download(_EXE_URL, new_path, on_progress=on_progress):
        return False, "Download failed — check your internet connection and try again."

    _write_and_launch_helper(exe_path, new_path)
    os._exit(0)  # noqa: SLF001 -- deliberate hard exit, see module docstring
