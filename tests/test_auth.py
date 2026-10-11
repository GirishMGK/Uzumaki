"""Tests for auth.py (per-user login + per-role tool access control) and
its wiring into Home.py -- real request: gate the whole app behind a
login, with per-user accounts, and let an admin restrict which tools a
given role can open.
"""
from __future__ import annotations

import importlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest

import auth

ADMIN = "admin"
ADMIN_PW = "Adm1n-Test-Pass"


@pytest.fixture()
def fresh_auth(tmp_path, monkeypatch):
    """A brand-new install: schema created, no accounts yet."""
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    importlib.reload(auth)
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    auth.init_db()
    yield auth


@pytest.fixture()
def isolated_auth(tmp_path, monkeypatch):
    """Points auth.py's DB at a throwaway directory per test (so tests
    don't share or pollute one real local auth.db) and completes first-run
    setup with a known admin account."""
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    importlib.reload(auth)
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    auth.init_db()
    auth.create_initial_admin(ADMIN, ADMIN_PW, "Administrator")
    yield auth


def test_initial_admin_gets_every_tool(isolated_auth):
    user = isolated_auth.authenticate(ADMIN, ADMIN_PW)
    assert user is not None
    assert user["is_admin"] is True
    assert set(user["allowed_tools"]) == set(isolated_auth.TOOL_KEYS)


def test_wrong_password_and_unknown_user_rejected(isolated_auth):
    assert isolated_auth.authenticate(ADMIN, "wrong") is None
    assert isolated_auth.authenticate("nobody", "whatever") is None


def test_role_restricts_allowed_tools(isolated_auth):
    isolated_auth.create_role("Junior Auditor", ["JE Audit Analytics", "Document Redaction"])
    isolated_auth.create_user("jdoe", "Passw0rd!", "Jane Doe", "Junior Auditor", is_admin=False)

    user = isolated_auth.authenticate("jdoe", "Passw0rd!")
    assert user is not None
    assert user["is_admin"] is False
    assert set(user["allowed_tools"]) == {"JE Audit Analytics", "Document Redaction"}
    assert "Tally" not in user["allowed_tools"]


def test_admin_always_has_every_tool_regardless_of_role(isolated_auth):
    """An admin's allowed_tools must be every tool, even if their
    assigned role (rarely meaningful for an admin) only grants a few --
    the is_admin flag overrides the role's list entirely."""
    isolated_auth.create_role("Junior Auditor", ["JE Audit Analytics"])
    isolated_auth.create_user("super", "Passw0rd!", "Super User", "Junior Auditor", is_admin=True)

    user = isolated_auth.authenticate("super", "Passw0rd!")
    assert set(user["allowed_tools"]) == set(isolated_auth.TOOL_KEYS)


def test_updating_role_permissions_takes_effect_on_next_login(isolated_auth):
    isolated_auth.create_role("Junior Auditor", ["JE Audit Analytics", "Document Redaction"])
    isolated_auth.create_user("jdoe", "Passw0rd!", "Jane Doe", "Junior Auditor")

    isolated_auth.update_role("Junior Auditor", ["JE Audit Analytics"])
    user = isolated_auth.authenticate("jdoe", "Passw0rd!")
    assert user["allowed_tools"] == ["JE Audit Analytics"]


def test_cannot_delete_last_remaining_admin(isolated_auth):
    with pytest.raises(ValueError):
        isolated_auth.delete_user(ADMIN)
    # still logs in fine afterwards -- the delete must not have partially applied
    assert isolated_auth.authenticate(
        ADMIN, ADMIN_PW
    ) is not None


def test_can_delete_admin_when_another_admin_remains(isolated_auth):
    isolated_auth.create_user("second_admin", "Passw0rd!", "Second Admin", "Administrator", is_admin=True)
    isolated_auth.delete_user(ADMIN)  # should not raise
    assert isolated_auth.authenticate(ADMIN, ADMIN_PW) is None
    assert isolated_auth.authenticate("second_admin", "Passw0rd!") is not None


def test_cannot_delete_role_still_assigned_to_a_user(isolated_auth):
    isolated_auth.create_role("Junior Auditor", ["JE Audit Analytics"])
    isolated_auth.create_user("jdoe", "Passw0rd!", "Jane Doe", "Junior Auditor")
    with pytest.raises(ValueError):
        isolated_auth.delete_role("Junior Auditor")


def test_password_hash_is_not_plaintext(isolated_auth):
    isolated_auth.create_user("plain", "SuperSecret123", "Plain", isolated_auth.ADMIN_ROLE_NAME, is_admin=True)
    users = isolated_auth.list_users()
    assert any(u["username"] == "plain" for u in users)
    # the public list_users() view never exposes the hash at all, but make
    # sure the hashing function itself never returns the raw password
    assert isolated_auth.hash_password("SuperSecret123") != "SuperSecret123"
    assert isolated_auth.verify_password("SuperSecret123", isolated_auth.hash_password("SuperSecret123"))
    assert not isolated_auth.verify_password("wrong", isolated_auth.hash_password("SuperSecret123"))


def test_home_py_tool_titles_match_auth_tool_keys():
    """Structural guard: every _TOOLS title in Home.py (what a role's
    permissions actually restrict by) must exist in auth.TOOL_KEYS,
    otherwise that tool could never be granted or restricted by role."""
    src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    for key in auth.TOOL_KEYS:
        assert f'"{key}"' in src, f"auth.TOOL_KEYS has {key!r} but Home.py's _TOOLS/nav doesn't reference it"


def test_home_py_gates_on_auth_user_before_building_nav():
    """Structural guard: the login gate must run before st.navigation() is
    built, not after -- otherwise an unauthenticated session could still
    see (or worse, reach) tool pages."""
    src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    gate_pos = src.index('if "auth_user" not in st.session_state:')
    nav_pos = src.index("nav = st.navigation(")
    assert gate_pos < nav_pos


# ── first-run setup / no shared default login ─────────────────────────────
def test_fresh_install_has_no_accounts_and_needs_setup(fresh_auth):
    assert fresh_auth.needs_setup() is True
    assert fresh_auth.list_users() == []
    assert fresh_auth.authenticate("admin", "ChangeMe!2026") is None


def test_initial_admin_can_only_be_created_once(fresh_auth):
    fresh_auth.create_initial_admin(ADMIN, ADMIN_PW)
    assert fresh_auth.needs_setup() is False
    with pytest.raises(ValueError):
        fresh_auth.create_initial_admin("intruder", "An0ther-Pass!")
    assert fresh_auth.get_user("intruder") is None


@pytest.mark.parametrize("bad", ["short", "ChangeMe!2026"])
def test_weak_or_legacy_passwords_rejected(fresh_auth, bad):
    with pytest.raises(ValueError):
        fresh_auth.create_initial_admin(ADMIN, bad)
    fresh_auth.create_initial_admin(ADMIN, ADMIN_PW)
    with pytest.raises(ValueError):
        fresh_auth.create_user("jdoe", bad, "Jane", fresh_auth.ADMIN_ROLE_NAME)
    with pytest.raises(ValueError):
        fresh_auth.update_user(ADMIN, password=bad)


def _legacy_install(tmp_path):
    """An auth.db exactly as the old init_db() left it: no
    must_change_password column, admin seeded with ChangeMe!2026."""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "auth.db")
    conn.executescript(
        """CREATE TABLE roles (name TEXT PRIMARY KEY, allowed_tools TEXT NOT NULL);
        CREATE TABLE users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL DEFAULT '', role_name TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(role_name) REFERENCES roles(name));"""
    )
    conn.execute("INSERT INTO roles VALUES ('Administrator', '[]')")
    conn.execute(
        "INSERT INTO users VALUES ('admin', ?, 'Administrator', 'Administrator', 1)",
        (auth.hash_password("ChangeMe!2026"),),
    )
    conn.commit()
    conn.close()


def test_legacy_default_login_is_migrated_and_forced_to_change(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_app_data_dir", lambda: tmp_path)
    _legacy_install(tmp_path)
    auth.init_db()  # adds the new column without losing the account

    user = auth.authenticate("admin", "ChangeMe!2026")
    assert user is not None
    assert user["must_change_password"] is True

    with pytest.raises(ValueError):
        auth.change_own_password("admin", "wrong-current", "N3w-Strong-Pass")
    with pytest.raises(ValueError):
        auth.change_own_password("admin", "ChangeMe!2026", "ChangeMe!2026")

    auth.change_own_password("admin", "ChangeMe!2026", "N3w-Strong-Pass")
    assert auth.authenticate("admin", "ChangeMe!2026") is None
    assert auth.authenticate("admin", "N3w-Strong-Pass")["must_change_password"] is False


def test_regular_login_is_not_flagged(isolated_auth):
    assert isolated_auth.authenticate(ADMIN, ADMIN_PW)["must_change_password"] is False
