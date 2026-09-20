"""AppTest coverage of _pages/admin.py -- exercises the real widgets for
creating a role and a user (the actual UI an admin uses to restrict a
role's tool access), plus the admin-only guard.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest
from streamlit.testing.v1 import AppTest

import auth

_ADMIN_SESSION = {
    "username": "admin", "full_name": "Administrator", "role_name": "Administrator",
    "is_admin": True, "allowed_tools": auth.TOOL_KEYS,
}
_NON_ADMIN_SESSION = {
    "username": "jdoe", "full_name": "Jane Doe", "role_name": "Junior Auditor",
    "is_admin": False, "allowed_tools": [],
}


@pytest.fixture()
def isolated_admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    auth.init_db()
    yield tmp_path


def _admin_apptest() -> AppTest:
    at = AppTest.from_file(os.path.join(REPO_ROOT, "_pages", "admin.py"))
    at.session_state["auth_user"] = dict(_ADMIN_SESSION)
    return at


def test_non_admin_is_blocked(isolated_admin_env):
    at = AppTest.from_file(os.path.join(REPO_ROOT, "_pages", "admin.py"))
    at.session_state["auth_user"] = dict(_NON_ADMIN_SESSION)
    at.run(timeout=30)
    assert not at.exception
    assert any("don't have access" in e.value for e in at.error)


def test_admin_sees_the_page(isolated_admin_env):
    at = _admin_apptest()
    at.run(timeout=30)
    assert not at.exception


def test_create_role_via_real_ui_widgets(isolated_admin_env):
    at = _admin_apptest()
    at.run(timeout=30)

    tab_roles = at.tabs[1]
    tab_roles.text_input[0].set_value("Junior Auditor")
    tab_roles.multiselect[1].set_value(["JE Audit Analytics", "Document Redaction"])
    tab_roles.button[2].click().run(timeout=30)
    assert not at.exception

    roles = {r["name"]: r["allowed_tools"] for r in auth.list_roles()}
    assert set(roles["Junior Auditor"]) == {"JE Audit Analytics", "Document Redaction"}


def test_create_user_via_real_ui_widgets_and_login_works(isolated_admin_env):
    at = _admin_apptest()
    at.run(timeout=30)

    tab_roles = at.tabs[1]
    tab_roles.text_input[0].set_value("Junior Auditor")
    tab_roles.multiselect[1].set_value(["JE Audit Analytics"])
    tab_roles.button[2].click().run(timeout=30)

    tab_users = at.tabs[0]
    tab_users.text_input[0].set_value("jdoe")
    tab_users.text_input[1].set_value("Jane Doe")
    tab_users.text_input[2].set_value("Passw0rd!")
    tab_users.selectbox[0].set_value("Junior Auditor")
    tab_users.button[2].click().run(timeout=30)
    assert not at.exception

    user = auth.authenticate("jdoe", "Passw0rd!")
    assert user is not None
    assert user["allowed_tools"] == ["JE Audit Analytics"]
