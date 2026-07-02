"""Tests for launcher.py's update-check logic (the Uzumaki.exe entry point).

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


@pytest.fixture(autouse=True)
def _clean_frozen_state():
    had_frozen = hasattr(sys, "frozen")
    had_meipass = hasattr(sys, "_MEIPASS")
    yield
    if not had_frozen and hasattr(sys, "frozen"):
        del sys.frozen
    if not had_meipass and hasattr(sys, "_MEIPASS"):
        del sys._MEIPASS


def test_update_check_is_noop_when_not_frozen():
    sys.frozen = False
    with mock.patch("urllib.request.urlopen") as m:
        launcher.check_for_update()
    m.assert_not_called()


def test_update_check_never_blocks_when_offline():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")), \
         mock.patch.object(launcher, "_self_update_and_relaunch") as upd:
        launcher.check_for_update()  # must not raise
    upd.assert_not_called()


def test_update_triggers_when_remote_version_differs():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(launcher, "_remote_version", return_value="deadbeef"), \
         mock.patch.object(launcher, "_local_version", return_value="0.0.0-dev"), \
         mock.patch.object(launcher, "_self_update_and_relaunch") as upd:
        launcher.check_for_update()
    upd.assert_called_once()


def test_update_skipped_when_versions_match():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch.object(launcher, "_remote_version", return_value="abc123"), \
         mock.patch.object(launcher, "_local_version", return_value="abc123"), \
         mock.patch.object(launcher, "_self_update_and_relaunch") as upd:
        launcher.check_for_update()
    upd.assert_not_called()


def test_run_app_launches_home_py_with_expected_flags():
    with mock.patch("streamlit.web.cli.main", return_value=0) as m:
        with pytest.raises(SystemExit):
            launcher.run_app()
    m.assert_called_once()
    assert sys.argv[0] == "streamlit"
    assert sys.argv[1] == "run"
    assert sys.argv[2].endswith("Home.py")
    assert os.path.exists(sys.argv[2])
