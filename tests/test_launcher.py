"""Tests for updater.py's update-check logic (used by launcher.py, the
Uzumaki.exe entry point, and by the in-app "Check for Updates" control).

Can't build/run an actual .exe here — these test the pure-Python decision
logic in isolation with the network mocked out, since a failed/slow update
check must never block the app from launching.
"""
from __future__ import annotations

import os
import sys
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


def test_run_app_launches_home_py_with_expected_flags():
    with mock.patch("streamlit.web.cli.main", return_value=0) as m:
        with pytest.raises(SystemExit):
            launcher.run_app()
    m.assert_called_once()
    assert sys.argv[0] == "streamlit"
    assert sys.argv[1] == "run"
    assert sys.argv[2].endswith("Home.py")
    assert os.path.exists(sys.argv[2])
