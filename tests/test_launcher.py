"""Tests for updater.py's update-check logic (used by launcher.py, the
Uzumaki.exe entry point, and by the in-app "Check for Updates" control).

Can't build/run an actual .exe here — these test the pure-Python decision
logic in isolation with the network mocked out, since a failed/slow update
check must never block the app from launching.
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import launcher
import updater


@pytest.fixture(autouse=True)
def _clean_frozen_state():
    had_frozen = hasattr(sys, "frozen")
    had_meipass = hasattr(sys, "_MEIPASS")
    yield
    if not had_frozen and hasattr(sys, "frozen"):
        del sys.frozen
    if not had_meipass and hasattr(sys, "_MEIPASS"):
        del sys._MEIPASS


def test_launcher_delegates_to_updater():
    """launcher.py must not re-implement this logic -- it should be the
    exact same function object updater.py exposes, so there's one source of
    truth for both the at-launch check and the in-app control."""
    assert launcher.check_for_update is updater.check_for_update
    assert launcher.base_dir is updater.base_dir


def test_update_check_is_noop_when_not_frozen():
    sys.frozen = False
    with mock.patch("urllib.request.urlopen") as m:
        updater.check_for_update()
    m.assert_not_called()


def test_update_check_never_blocks_when_offline():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")), \
         mock.patch.object(updater, "self_update_and_relaunch") as upd:
        updater.check_for_update()  # must not raise
    upd.assert_not_called()


def test_update_triggers_when_remote_version_differs():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(updater, "remote_version", return_value="deadbeef"), \
         mock.patch.object(updater, "local_version", return_value="0.0.0-dev"), \
         mock.patch.object(updater, "self_update_and_relaunch") as upd:
        updater.check_for_update()
    upd.assert_called_once()


def test_update_skipped_when_versions_match():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(updater, "remote_version", return_value="abc123"), \
         mock.patch.object(updater, "local_version", return_value="abc123"), \
         mock.patch.object(updater, "self_update_and_relaunch") as upd:
        updater.check_for_update()
    upd.assert_not_called()


def test_check_update_status_reports_no_side_effects():
    """The in-app control's status check must never trigger a download/restart
    on its own -- only perform_update_and_restart(), called from a button
    click, should do that."""
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(updater, "remote_version", return_value="v2"), \
         mock.patch.object(updater, "local_version", return_value="v1"), \
         mock.patch.object(updater, "self_update_and_relaunch") as upd:
        status = updater.check_update_status()
    upd.assert_not_called()
    assert status == {"local": "v1", "remote": "v2", "update_available": True, "checked": True}


def test_check_update_status_when_not_frozen():
    sys.frozen = False
    status = updater.check_update_status()
    assert status["checked"] is False
    assert status["update_available"] is False


def test_check_update_status_when_offline():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(updater, "remote_version", return_value=None):
        status = updater.check_update_status()
    assert status["checked"] is False
    assert status["update_available"] is False


def test_perform_update_and_restart_reports_failure_without_exiting():
    """If the download fails, this must return an error tuple, not hard-exit
    the process -- os._exit() is reserved for the success path only."""
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(updater, "_download", return_value=False):
        ok, message = updater.perform_update_and_restart()
    assert ok is False
    assert "Download failed" in message


def test_perform_update_and_restart_noop_when_not_frozen():
    sys.frozen = False
    ok, message = updater.perform_update_and_restart()
    assert ok is False


def test_run_app_launches_home_py_headless_on_given_port():
    """run_app() is what the headless server CHILD process actually runs
    (see launcher.py's module docstring for why Streamlit moved from
    in-process to a child process: pywebview's window now owns the main
    thread instead). It must stay headless (no browser auto-open --
    pywebview is the UI) and bind the exact port the parent chose."""
    with mock.patch("streamlit.web.cli.main", return_value=0) as m:
        with pytest.raises(SystemExit):
            launcher.run_app(port=54321)
    m.assert_called_once()
    assert sys.argv[0] == "streamlit"
    assert sys.argv[1] == "run"
    assert sys.argv[2].endswith("Home.py")
    assert os.path.exists(sys.argv[2])
    assert "--server.headless=true" in sys.argv
    assert "--server.port=54321" in sys.argv


def test_find_free_port_returns_a_bindable_port():
    port = launcher.find_free_port()
    assert 1 <= port <= 65535
    # Must actually be free right after — bind it ourselves to prove it.
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_server_healthy_false_when_nothing_listening():
    # Nothing is listening on this port in the test environment.
    assert launcher._server_healthy(1) is False


def test_wait_for_server_returns_false_if_child_dies_first():
    class _DeadProc:
        def poll(self):
            return 1  # already exited

    ok = launcher._wait_for_server(12345, _DeadProc(), timeout=1)
    assert ok is False


def test_wait_for_server_returns_true_once_healthy():
    class _AliveProc:
        def poll(self):
            return None

    calls = {"n": 0}

    def _fake_healthy(port):
        calls["n"] += 1
        return calls["n"] >= 2  # unhealthy once, then healthy

    with mock.patch.object(launcher, "_server_healthy", side_effect=_fake_healthy):
        ok = launcher._wait_for_server(12345, _AliveProc(), timeout=5)
    assert ok is True
    assert calls["n"] >= 2


def test_spawn_server_child_sets_sentinel_env_and_port():
    captured = {}

    def _fake_popen(args, env=None, **kwargs):
        captured["args"] = args
        captured["env"] = env
        return mock.Mock()

    with mock.patch("subprocess.Popen", side_effect=_fake_popen):
        launcher._spawn_server_child(9999)

    assert captured["env"]["UZUMAKI_SERVER_ONLY"] == "1"
    assert captured["env"]["UZUMAKI_PORT"] == "9999"


def test_main_entrypoint_dispatches_on_sentinel_env(monkeypatch):
    """The bottom-of-file `if __name__ == "__main__":` dispatch (server-only
    child vs. normal parent) is exercised indirectly here by checking the
    sentinel env var name/value this module actually looks for, since
    running the real __main__ block would try to open a real window."""
    monkeypatch.setenv("UZUMAKI_SERVER_ONLY", "1")
    assert os.environ.get(launcher._SERVER_ONLY_ENV) == "1"


def test_fatal_startup_error_writes_a_real_log_file(tmp_path):
    """console=False (Uzumaki.spec) means a startup failure before the
    window opens would otherwise be completely silent to the user -- no
    console, no window. This is the fallback: verified end-to-end with a
    real exception and a real temp file, not mocked, since the whole point
    is that the file's actual content must be readable/useful."""
    fake_exe = tmp_path / "Uzumaki.exe"
    fake_exe.write_bytes(b"")  # only its path is used, content irrelevant
    with mock.patch.object(sys, "executable", str(fake_exe)):
        launcher._report_fatal_startup_error(RuntimeError("simulated startup failure"))

    log_path = tmp_path / "Uzumaki_error.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "simulated startup failure" in content
    assert "RuntimeError" in content


def test_fatal_error_path_reraises_system_exit_untouched():
    """Normal exit paths (stcli.main()'s sys.exit(0) inside run_app(), etc.)
    must propagate as-is, not get caught and rewritten as a 'fatal error' --
    only genuinely unhandled exceptions should trigger the error dialog/log."""
    src = open(os.path.join(REPO_ROOT, "launcher.py"), encoding="utf-8").read()
    main_block = src.split('if __name__ == "__main__":')[1]
    assert "except SystemExit:" in main_block
    assert "raise  # normal exit paths" in main_block


def test_download_streams_and_reports_progress_with_content_length():
    """New feature: a real progress bar for 'Download & Restart' instead of
    a bare spinner. Verified against a real local HTTP server (not mocked)
    with a payload that spans multiple chunks, so this actually exercises
    the streaming read loop and confirms on_progress() fires with correct,
    monotonically increasing byte counts and the right total each time."""
    import http.server
    import threading

    payload = b"X" * (600 * 1024)  # > one _DOWNLOAD_CHUNK_SIZE (256 KiB)

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    calls = []
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "out.bin")
            ok = updater._download(
                f"http://127.0.0.1:{port}/", dest, on_progress=lambda d, t: calls.append((d, t))
            )
            assert ok
            assert os.path.getsize(dest) == len(payload)
    finally:
        server.shutdown()

    assert len(calls) >= 2  # payload spans multiple chunks at this size
    assert calls[-1] == (len(payload), len(payload))
    assert all(total == len(payload) for _, total in calls)
    # monotonically increasing
    assert all(a[0] < b[0] for a, b in zip(calls, calls[1:]))


def test_download_progress_falls_back_gracefully_without_content_length():
    """Some responses won't carry Content-Length (e.g. chunked transfer) --
    on_progress() must still fire with a usable (bytes-so-far, None) shape
    rather than crashing or silently not reporting progress at all."""
    import http.server
    import threading

    payload = b"Y" * (300 * 1024)

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # no keep-alive -- simplest way to omit Content-Length

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    calls = []
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "out.bin")
            ok = updater._download(
                f"http://127.0.0.1:{port}/", dest, on_progress=lambda d, t: calls.append((d, t))
            )
            assert ok
            assert os.path.getsize(dest) == len(payload)
    finally:
        server.shutdown()

    assert calls  # progress still reported
    assert all(total is None for _, total in calls)



def test_perform_update_and_restart_passes_progress_callback_through(monkeypatch):
    """perform_update_and_restart() must forward on_progress to _download()
    unchanged, not silently drop it."""
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT

    captured = {}

    def _fake_download(url, dest, on_progress=None):
        captured["on_progress"] = on_progress
        if on_progress:
            on_progress(50, 100)
        return False  # fail fast, don't actually reach os._exit()

    with mock.patch.object(updater, "_download", side_effect=_fake_download):
        marker = object()
        seen = []
        ok, message = updater.perform_update_and_restart(on_progress=lambda d, t: seen.append((d, t)))

    assert ok is False
    assert captured["on_progress"] is not None
    assert seen == [(50, 100)]
