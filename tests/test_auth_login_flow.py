"""AppTest coverage of the actual login screen/nav-filtering wired into
Home.py -- exercises the real Streamlit widgets (not just auth.py's
functions directly), since the actual bug risk for a login gate is in
the wiring (does submitting the form really authenticate? does an
unauthenticated session really never see the tool catalogue? does a
restricted user really only see their role's tools?), not in the auth
logic in isolation (already covered by tests/test_auth.py).
"""
from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest
from streamlit.testing.v1 import AppTest

import auth

ADMIN = "admin"
ADMIN_PW = "Adm1n-Test-Pass"


@pytest.fixture()
def fresh_home_env(tmp_path, monkeypatch):
    """A brand-new install: no accounts yet."""
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def isolated_home_env(fresh_home_env):
    """First-run setup already done, with a known admin account."""
    auth.init_db()
    auth.create_initial_admin(ADMIN, ADMIN_PW, "Administrator")
    yield fresh_home_env


def _card_titles(at: AppTest) -> set[str]:
    titles = set()
    for m in at.markdown:
        titles.update(re.findall(r'sa-card-title">([^<]+)<', m.value))
    return titles


def test_unauthenticated_session_sees_login_not_tools(isolated_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    assert not at.exception
    assert len(at.text_input) >= 2  # username + password fields
    assert not _card_titles(at)  # no tool catalogue visible pre-login


def test_correct_credentials_log_in_and_show_full_catalogue_for_admin(isolated_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value(ADMIN)
    at.text_input[1].set_value(ADMIN_PW)
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert _card_titles(at) == set(auth.TOOL_KEYS)


def test_wrong_password_shows_error_and_stays_logged_out(isolated_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value(ADMIN)
    at.text_input[1].set_value("totally wrong")
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert any("Incorrect username or password" in e.value for e in at.error)
    assert not _card_titles(at)


def test_restricted_role_only_sees_its_own_tools(isolated_home_env):
    auth.create_role("Junior Auditor", ["JE Audit Analytics", "Document Redaction"])
    auth.create_user("jdoe", "Passw0rd!", "Jane Doe", "Junior Auditor", is_admin=False)

    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value("jdoe")
    at.text_input[1].set_value("Passw0rd!")
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert _card_titles(at) == {"JE Audit Analytics", "Document Redaction"}


def test_logout_button_returns_to_login_screen(isolated_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value(ADMIN)
    at.text_input[1].set_value(ADMIN_PW)
    at.button[0].click().run(timeout=30)
    assert _card_titles(at)  # logged in, tools visible

    logout_btn = next(b for b in at.button if b.label == "Log out")
    logout_btn.click().run(timeout=30)
    assert not at.exception
    assert not _card_titles(at)
    assert len(at.text_input) >= 2  # back at the login form


def test_fresh_install_shows_setup_not_a_default_login(fresh_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    assert not at.exception
    assert not _card_titles(at)
    assert any(b.label == "Create account" for b in at.button)
    assert not any("ChangeMe" in m.value for m in [*at.markdown, *at.caption])


def test_setup_form_creates_admin_and_logs_in(fresh_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value("owner")
    at.text_input[2].set_value("Own3r-Pass!")
    at.text_input[3].set_value("Own3r-Pass!")
    next(b for b in at.button if b.label == "Create account").click().run(timeout=30)
    assert not at.exception
    assert _card_titles(at) == set(auth.TOOL_KEYS)
    assert auth.authenticate("owner", "Own3r-Pass!")["is_admin"] is True


def test_setup_form_rejects_mismatched_passwords(fresh_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value("owner")
    at.text_input[2].set_value("Own3r-Pass!")
    at.text_input[3].set_value("different-pass")
    next(b for b in at.button if b.label == "Create account").click().run(timeout=30)
    assert any("don't match" in e.value for e in at.error)
    assert auth.needs_setup()


def test_flagged_account_must_change_password_before_seeing_tools(isolated_home_env):
    auth.create_user("temp", "Temp-Pass-1", "Temp", auth.ADMIN_ROLE_NAME, is_admin=True)
    import sqlite3

    with sqlite3.connect(isolated_home_env / "auth.db") as conn:
        conn.execute("UPDATE users SET must_change_password = 1 WHERE username = 'temp'")

    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value("temp")
    at.text_input[1].set_value("Temp-Pass-1")
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert not _card_titles(at)  # blocked until the password is changed

    at.text_input[0].set_value("Temp-Pass-1")
    at.text_input[1].set_value("Brand-New-Pass")
    at.text_input[2].set_value("Brand-New-Pass")
    next(b for b in at.button if b.label == "Change password").click().run(timeout=30)
    assert not at.exception
    assert _card_titles(at) == set(auth.TOOL_KEYS)
