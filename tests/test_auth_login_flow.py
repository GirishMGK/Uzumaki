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


@pytest.fixture()
def isolated_home_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path


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
    at.text_input[0].set_value(auth.DEFAULT_ADMIN_USERNAME)
    at.text_input[1].set_value(auth.DEFAULT_ADMIN_PASSWORD)
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert _card_titles(at) == set(auth.TOOL_KEYS)


def test_wrong_password_shows_error_and_stays_logged_out(isolated_home_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "Home.py"))
    at.run(timeout=30)
    at.text_input[0].set_value(auth.DEFAULT_ADMIN_USERNAME)
    at.text_input[1].set_value("totally wrong")
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert any("Incorrect username or password" in e.value for e in at.error)
    assert not _card_titles(at)


def test_restricted_role_only_sees_its_own_tools(isolated_home_env):
    auth.init_db()
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
    at.text_input[0].set_value(auth.DEFAULT_ADMIN_USERNAME)
    at.text_input[1].set_value(auth.DEFAULT_ADMIN_PASSWORD)
    at.button[0].click().run(timeout=30)
    assert _card_titles(at)  # logged in, tools visible

    logout_btn = next(b for b in at.button if b.label == "Log out")
    logout_btn.click().run(timeout=30)
    assert not at.exception
    assert not _card_titles(at)
    assert len(at.text_input) >= 2  # back at the login form
