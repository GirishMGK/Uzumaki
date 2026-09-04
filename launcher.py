"""
Uzumaki.exe entry point.

Two jobs, in order:
  1. Self-update check — compare the version baked into this build against the
     latest published on GitHub; if newer, download the new .exe, hand off to
     a tiny helper script that replaces this file once the process exits, and
     relaunch. Skipped entirely when not running as a frozen exe (i.e. during
     `python launcher.py` from source) or when offline — a failed update
     check must never block launching the app. The actual logic lives in
     updater.py (a plain module, not another entry script) so the same code
     can also be called from within the running app for the in-app "Check
     for Updates" control (Home.py's sidebar) — see updater.py's docstring
     for why that split matters for a frozen build specifically.
  2. Launch the Streamlit hub (Home.py) in-process and open the browser.

This file is the sole PyInstaller entry point (see Uzumaki.spec) — everything
else (Home.py, _pages/, tools/, redaction_tool/, je_audit_tool/,
form26as_tool/, and the root-level extractor modules) ships as bundled data
alongside it, preserving the exact relative layout the source repo already
uses, so none of that code needs to know it's running frozen.
"""

from __future__ import annotations

import os
import sys

from updater import base_dir, check_for_update  # noqa: F401 -- base_dir kept for tests


def _accept_streamlit_credentials() -> None:
    """
    Pre-seed ~/.streamlit/credentials.toml so Streamlit skips its first-run
    "Enter your email" prompt. That prompt reads from stdin — fine for a
    terminal, but a double-clicked .exe has no stdin to read from, so
    without this it hangs forever on first launch instead of opening the
    browser. --browser.gatherUsageStats=false does not skip this prompt by
    itself; only a pre-existing credentials file does.
    """
    from streamlit.file_util import get_streamlit_file_path

    conf_file = get_streamlit_file_path("credentials.toml")
    if os.path.exists(conf_file):
        return
    os.makedirs(os.path.dirname(conf_file), exist_ok=True)
    with open(conf_file, "w", encoding="utf-8") as f:
        f.write('[general]\nemail = ""\n')


def run_app() -> None:
    _accept_streamlit_credentials()
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
