"""
Uzumaki.exe entry point.

Three jobs, in order:
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
  2. Start the Streamlit hub as a headless CHILD process (not in-process --
     see below) on a dynamically chosen free port.
  3. Open a native desktop window (pywebview) pointing at that local server,
     instead of a browser tab -- so Uzumaki looks and behaves like a real
     desktop app: its own window/taskbar entry, no address bar or browser
     chrome, and no separate console window either (this exe is now built
     with console=False in Uzumaki.spec).

WHY A CHILD PROCESS, NOT IN-PROCESS STREAMLIT
------------------------------------------------
The previous version of this file called `streamlit.web.cli.main()`
in-process, which runs Streamlit's own Tornado ioloop on the calling
thread and blocks there (that's how it worked when a browser tab, not a
native window, was the UI: the ioloop just ran on the main thread for the
life of the app). Making the actual UI a pywebview window instead means
*that* now needs the main thread (`webview.start()` blocks the same way),
so Streamlit's server has to run somewhere else. Running it in a background
Python thread is possible but fragile here specifically because
`streamlit.web.cli.main()` is a Click command that calls `sys.exit()`
internally when the server stops -- harmless in its own thread, but it
means reusing that exact function outside the main thread relies on
undocumented behavior. A child PROCESS avoids that entirely and mirrors a
mechanism this file already uses elsewhere (the self-update swap helper is
also a separate process) -- this process is relaunched with the sentinel
env var UZUMAKI_SERVER_ONLY=1 set, which makes it skip straight to
`run_app()` (this same file, just running the server, no update-check/
webview) once it reaches the bottom of this module.

This file is the sole PyInstaller entry point (see Uzumaki.spec) — everything
else (Home.py, _pages/, tools/, redaction_tool/, je_audit_tool/,
form26as_tool/, and the root-level extractor modules) ships as bundled data
alongside it, preserving the exact relative layout the source repo already
uses, so none of that code needs to know it's running frozen.

NOT YET VERIFIED ON A REAL WINDOWS MACHINE: this environment has no display
to actually open a pywebview window on, so the process-spawn/health-check/
window-lifecycle wiring below is verified as far as it can be here (the
server-only child path is identical to the previous working in-process
call, just gated behind the env var; the free-port/health-check helpers are
unit-testable in isolation) but the actual native window has not been seen
rendering for real. Expect to iterate once tried on Windows.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from updater import base_dir, check_for_update  # noqa: F401 -- base_dir kept for tests

_SERVER_ONLY_ENV = "UZUMAKI_SERVER_ONLY"
_PORT_ENV = "UZUMAKI_PORT"
_HEALTH_TIMEOUT = 30  # seconds to wait for the child Streamlit server to come up


def _accept_streamlit_credentials() -> None:
    """
    Pre-seed ~/.streamlit/credentials.toml so Streamlit skips its first-run
    "Enter your email" prompt. That prompt reads from stdin — fine for a
    terminal, but a double-clicked .exe has no stdin to read from, so
    without this it hangs forever on first launch instead of ever starting
    the server. --browser.gatherUsageStats=false does not skip this prompt
    by itself; only a pre-existing credentials file does.
    """
    from streamlit.file_util import get_streamlit_file_path

    conf_file = get_streamlit_file_path("credentials.toml")
    if os.path.exists(conf_file):
        return
    os.makedirs(os.path.dirname(conf_file), exist_ok=True)
    with open(conf_file, "w", encoding="utf-8") as f:
        f.write('[general]\nemail = ""\n')


def run_app(port: int | None = None) -> None:
    """Runs the Streamlit server itself (this is the CHILD process's whole
    job when UZUMAKI_SERVER_ONLY is set -- see module docstring). Headless:
    no browser auto-open, since the pywebview window in the parent process
    is the actual UI. `port` defaults to the UZUMAKI_PORT env var the parent
    set when spawning this child."""
    _accept_streamlit_credentials()
    if port is None:
        port = int(os.environ.get(_PORT_ENV, "0")) or find_free_port()
    home = os.path.join(base_dir(), "Home.py")
    sys.argv = [
        "streamlit", "run", home,
        "--server.headless=true",
        f"--server.port={port}",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]
    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


def find_free_port() -> int:
    """Binds to port 0 (OS picks any free ephemeral port), reads it back,
    then releases it. Small TOCTOU race between this and Streamlit actually
    binding it is possible in principle (something else could grab the same
    port in between) but is not worth guarding against for a local-only,
    single-user desktop app."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _spawn_server_child(port: int) -> subprocess.Popen:
    """Relaunches this same exe (or `python launcher.py` when running from
    source) as a child process with UZUMAKI_SERVER_ONLY set, so it skips
    straight to run_app() instead of repeating the update-check/webview
    steps -- same self-relaunch trick already used by the update-swap
    helper elsewhere in this file's flow."""
    from updater import is_frozen

    args = [sys.executable] if is_frozen() else [sys.executable, os.path.abspath(__file__)]
    env = dict(os.environ)
    env[_SERVER_ONLY_ENV] = "1"
    env[_PORT_ENV] = str(port)
    kwargs = {}
    if sys.platform.startswith("win"):
        # No console window for the child either.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, env=env, **kwargs)


def _wait_for_server(port: int, proc: subprocess.Popen, timeout: float = _HEALTH_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # child died before ever becoming healthy
        if _server_healthy(port):
            return True
        time.sleep(0.3)
    return False


def run_desktop_app() -> None:
    """Parent-process path: spawn the headless server child, wait for it,
    then open the native window pointing at it. Blocks in webview.start()
    for the life of the app; terminates the child once the window closes."""
    port = find_free_port()
    server = _spawn_server_child(port)

    if not _wait_for_server(port, server):
        server.terminate()
        raise RuntimeError(
            "Uzumaki's local server didn't start in time. "
            "Try relaunching — if this keeps happening, run from a terminal to see the error."
        )

    import webview

    webview.create_window(
        "Uzumaki",
        f"http://127.0.0.1:{port}",
        width=1400, height=900, min_size=(900, 600),
    )
    try:
        webview.start()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def _report_fatal_startup_error(exc: BaseException) -> None:
    """console=False (see Uzumaki.spec) means a startup failure before the
    window ever opens would otherwise be completely silent -- no console,
    no window, nothing; the double-click would just appear to do nothing.
    Write the real traceback to a log file next to the exe, then try a
    native message box (Windows only, best-effort -- if even that fails,
    at least the log file exists)."""
    import traceback

    log_path = os.path.join(
        os.path.dirname(os.path.abspath(sys.executable)), "Uzumaki_error.log"
    )
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("Uzumaki failed to start:\n\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        pass  # best-effort; still try the message box below

    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Uzumaki failed to start:\n\n{exc}\n\n"
                f"Details written to:\n{log_path}",
                "Uzumaki",
                0x10,  # MB_ICONERROR
            )
        except Exception:
            pass  # nothing more we can do without a console


if __name__ == "__main__":
    try:
        if os.environ.get(_SERVER_ONLY_ENV) == "1":
            run_app()
        else:
            check_for_update()
            run_desktop_app()
    except SystemExit:
        raise  # normal exit paths (stcli.main()'s sys.exit(0), etc.) — not a failure
    except BaseException as exc:  # noqa: BLE001 — last-resort net, see docstring above
        _report_fatal_startup_error(exc)
        sys.exit(1)
