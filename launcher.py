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

The outcome of step 1 (updated / already current / couldn't reach GitHub /
update found but the download failed) is handed to Home.py via environment
variables (UZUMAKI_UPDATE_STATUS, UZUMAKI_VERSION) so it can show a one-time
toast notification every time the app is opened — see Home.py's
`_update_notice()`.

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

# Update-status outcomes, handed to Home.py via the UZUMAKI_UPDATE_STATUS env
# var so it knows what to say in the on-open toast notification.
STATUS_UPDATED = "updated"            # just self-updated and relaunched
STATUS_CURRENT = "current"            # already on the latest version
STATUS_OFFLINE = "offline"            # couldn't reach GitHub to check
STATUS_UPDATE_FAILED = "update_failed"  # newer build exists but download failed

# Set (with value "1") on the relaunched process's environment by
# _self_update_and_relaunch so it can report STATUS_UPDATED without hitting
# the network a second time.
_JUST_UPDATED_ENV = "UZUMAKI_JUST_UPDATED"


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
    try:
        with urllib.request.urlopen(_VERSION_URL, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _download(url: str, dest: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
            out.write(resp.read())
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _self_update_and_relaunch() -> None:
    """Download the new exe, swap it in via a detached helper, then exit.

    Marks the relaunched process's environment with _JUST_UPDATED_ENV so it
    reports STATUS_UPDATED (and Home.py shows the "updated" toast) instead of
    silently re-checking and reporting STATUS_CURRENT.
    """
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
                "timeout /t 2 /nobreak >nul\r\n"
                f':retry\r\n'
                f'del /f /q "{exe_path}" 2>nul\r\n'
                f'if exist "{exe_path}" (\r\n'
                "  timeout /t 1 /nobreak >nul\r\n"
                "  goto retry\r\n"
                ")\r\n"
                f'move /y "{new_path}" "{exe_path}" >nul\r\n'
                f'set "{_JUST_UPDATED_ENV}=1"\r\n'
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
        subprocess.Popen([exe_path], env={**os.environ, _JUST_UPDATED_ENV: "1"})

    print("Restarting with the new version…")
    sys.exit(0)


def check_for_update() -> str:
    """Check GitHub for a newer build and self-update if one exists.

    Returns the STATUS_* outcome for *this* process to launch with — the
    caller stashes it in UZUMAKI_UPDATE_STATUS for Home.py's on-open toast.
    A successful self-update never returns (the process exits and a new one
    takes over); that new process short-circuits here via _JUST_UPDATED_ENV.
    """
    if os.environ.get(_JUST_UPDATED_ENV) == "1":
        return STATUS_UPDATED
    if not _is_frozen():
        return STATUS_CURRENT  # nothing to self-replace when running from source
    remote = _remote_version()
    if remote is None:
        return STATUS_OFFLINE  # offline or GitHub unreachable — just launch what we have
    if remote != _local_version():
        _self_update_and_relaunch()  # exits on success; falls through only if the download failed
        return STATUS_UPDATE_FAILED
    return STATUS_CURRENT


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


def main() -> None:
    status = check_for_update()
    os.environ["UZUMAKI_UPDATE_STATUS"] = status
    os.environ["UZUMAKI_VERSION"] = _local_version()
    run_app()


if __name__ == "__main__":
    main()
