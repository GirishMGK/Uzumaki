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
    had_env = launcher._JUST_UPDATED_ENV in os.environ
    yield
    if not had_frozen and hasattr(sys, "frozen"):
        del sys.frozen
    if not had_meipass and hasattr(sys, "_MEIPASS"):
        del sys._MEIPASS
    if not had_env:
        os.environ.pop(launcher._JUST_UPDATED_ENV, None)


def test_update_check_is_noop_when_not_frozen():
    sys.frozen = False
    with mock.patch("urllib.request.urlopen") as m:
        status = launcher.check_for_update()
    m.assert_not_called()
    assert status == launcher.STATUS_CURRENT


def test_update_check_never_blocks_when_offline():
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")), \
         mock.patch.object(launcher, "_self_update_and_relaunch") as upd:
        status = launcher.check_for_update()  # must not raise
    upd.assert_not_called()
    assert status == launcher.STATUS_OFFLINE


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
        status = launcher.check_for_update()
    upd.assert_not_called()
    assert status == launcher.STATUS_CURRENT


def test_status_reports_updated_after_relaunch_without_hitting_network():
    """The freshly relaunched process (env var set by _self_update_and_relaunch)
    must report STATUS_UPDATED immediately, without re-checking GitHub."""
    sys.frozen = True
    sys._MEIPASS = REPO_ROOT
    os.environ[launcher._JUST_UPDATED_ENV] = "1"
    with mock.patch("urllib.request.urlopen") as m:
        status = launcher.check_for_update()
    m.assert_not_called()
    assert status == launcher.STATUS_UPDATED


def test_self_update_relaunch_passes_just_updated_env_on_non_windows():
    """Non-Windows fallback path: the relaunched child process must inherit
    UZUMAKI_JUST_UPDATED=1 so it reports STATUS_UPDATED, not STATUS_CURRENT."""
    with mock.patch.object(launcher.sys, "platform", "linux"), \
         mock.patch.object(launcher, "_download", return_value=True), \
         mock.patch("os.replace"), mock.patch("os.chmod"), \
         mock.patch("subprocess.Popen") as popen:
        with pytest.raises(SystemExit):
            launcher._self_update_and_relaunch()
    _, kwargs = popen.call_args
    assert kwargs["env"][launcher._JUST_UPDATED_ENV] == "1"


def test_self_update_relaunch_sets_just_updated_env_in_windows_helper(tmp_path):
    """Windows path: the helper .bat must `set` the env var before `start`ing
    the relaunched exe, since that's the only way to hand it to a new process."""
    # subprocess.DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP only exist on
    # Windows — stub them so this branch is exercisable from CI's ubuntu runner.
    with mock.patch.object(launcher.sys, "platform", "win32"), \
         mock.patch.object(launcher, "_download", return_value=True), \
         mock.patch.object(launcher.tempfile, "gettempdir", return_value=str(tmp_path)), \
         mock.patch.object(launcher.subprocess, "DETACHED_PROCESS", 0x00000008, create=True), \
         mock.patch.object(launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True), \
         mock.patch("subprocess.Popen"):
        with pytest.raises(SystemExit):
            launcher._self_update_and_relaunch()
    helper_content = (tmp_path / "uzumaki_update.bat").read_text(encoding="utf-8")
    assert f"set \"{launcher._JUST_UPDATED_ENV}=1\"" in helper_content
    assert helper_content.index(f"set \"{launcher._JUST_UPDATED_ENV}=1\"") < helper_content.index("start ")


def test_main_exposes_update_status_and_version_via_env_for_home_py():
    """Home.py's on-open toast reads these two env vars — main() must set
    them (post-check) before handing off to run_app()."""
    sys.frozen = False  # keeps check_for_update() a network-free STATUS_CURRENT
    os.environ.pop("UZUMAKI_UPDATE_STATUS", None)
    os.environ.pop("UZUMAKI_VERSION", None)
    with mock.patch.object(launcher, "run_app") as run_app, \
         mock.patch.object(launcher, "_local_version", return_value="1.2.3"):
        launcher.main()
    run_app.assert_called_once()
    assert os.environ["UZUMAKI_UPDATE_STATUS"] == launcher.STATUS_CURRENT
    assert os.environ["UZUMAKI_VERSION"] == "1.2.3"
    del os.environ["UZUMAKI_UPDATE_STATUS"]
    del os.environ["UZUMAKI_VERSION"]


def test_run_app_launches_home_py_with_expected_flags():
    with mock.patch("streamlit.web.cli.main", return_value=0) as m:
        with pytest.raises(SystemExit):
            launcher.run_app()
    m.assert_called_once()
    assert sys.argv[0] == "streamlit"
    assert sys.argv[1] == "run"
    assert sys.argv[2].endswith("Home.py")
    assert os.path.exists(sys.argv[2])
