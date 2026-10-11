"""Login + per-role tool access control for the whole Uzumaki hub.

Real request: gate the whole app behind a login, with per-user accounts,
and let an admin restrict which tools a given user's role can open (e.g.
a role that shouldn't have Tally access gets stopped from opening it).

Storage: a local SQLite database (stdlib sqlite3, no new dependency) in
the same per-user writable data directory pattern _pages/hrm.py already
uses for its own database -- works from a read-only install folder, and
each user has entirely their own accounts/roles, matching this being a
locally-installed desktop app, not a shared server.

Password hashing reuses passlib/bcrypt -- already a real dependency here
for hrm_tool's backend (see requirements.txt's bcrypt==4.0.1 pin and its
own comment on why that exact version), so this adds no new dependency.

TOOL_KEYS is the canonical list of nav titles a role's access can be
restricted by -- Home.py's own _TOOLS catalogue titles must match this
list exactly (tests/test_auth.py checks that they do), since a title
this list doesn't know about could never be restricted or granted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from passlib.context import CryptContext

TOOL_KEYS: list[str] = [
    "Loan Analytics",
    "EAD Consolidator",
    "BRS Consolidator",
    "Statutory Extractor",
    "Form 26AS Extractor",
    "PDF Tools",
    "SOA · RPS · Reconcile",
    "Document Redaction",
    "JE Audit Analytics",
    "Tally",
    "HRM",
]

ADMIN_ROLE_NAME = "Administrator"
MIN_PASSWORD_LENGTH = 8

# Installs from before first-run setup existed were seeded with this
# well-known admin login. It's kept only so authenticate() can recognise a
# login that still uses it and force a password change -- nothing creates
# an account with it any more.
_LEGACY_DEFAULT_ADMIN_PASSWORD = "ChangeMe!2026"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


def validate_new_password(password: str) -> None:
    """Raises ValueError if `password` isn't acceptable for a new or
    changed password."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password == _LEGACY_DEFAULT_ADMIN_PASSWORD:
        raise ValueError("That's the old default password -- choose a different one.")


def _app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = str(Path.home())
    data_dir = Path(base) / "Uzumaki"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _db_path() -> Path:
    return _app_data_dir() / "auth.db"


@contextmanager
def _get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Creates the schema if missing. Deliberately does NOT seed any
    account: a fresh install has no users, and Home.py shows a first-run
    setup form (needs_setup() / create_initial_admin()) so the first admin
    picks their own password instead of every install sharing a known one."""
    with _get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS roles (
                name TEXT PRIMARY KEY,
                allowed_tools TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                role_name TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(role_name) REFERENCES roles(name)
            )"""
        )
        # Databases created before must_change_password existed.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            "INSERT OR IGNORE INTO roles (name, allowed_tools) VALUES (?, ?)",
            (ADMIN_ROLE_NAME, json.dumps(TOOL_KEYS)),
        )


def needs_setup() -> bool:
    """True on a fresh install with no accounts yet -- Home.py then shows
    the create-first-admin form instead of the login form."""
    with _get_conn() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def create_initial_admin(username: str, password: str, full_name: str = "") -> None:
    """Creates the first administrator. Refuses once any account exists, so
    the setup form can't be replayed to add an admin later."""
    if not username:
        raise ValueError("Username is required.")
    validate_new_password(password)
    with _get_conn() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ValueError("Setup has already been completed.")
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role_name, is_admin) "
            "VALUES (?, ?, ?, ?, 1)",
            (username, hash_password(password), full_name, ADMIN_ROLE_NAME),
        )


def _row_to_role(row: sqlite3.Row) -> dict:
    return {"name": row["name"], "allowed_tools": json.loads(row["allowed_tools"])}


def _row_to_user(row: sqlite3.Row) -> dict:
    return {
        "username": row["username"],
        "full_name": row["full_name"],
        "role_name": row["role_name"],
        "is_admin": bool(row["is_admin"]),
        "must_change_password": bool(row["must_change_password"]),
    }


def authenticate(username: str, password: str) -> dict | None:
    """Returns the authenticated user's record (including their resolved
    `allowed_tools` -- every tool for an admin, otherwise their role's
    list) or None if the username/password don't match."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        if password == _LEGACY_DEFAULT_ADMIN_PASSWORD and not row["must_change_password"]:
            # An install that still has the old seeded login -- make them
            # pick a real password before they get any further.
            conn.execute(
                "UPDATE users SET must_change_password = 1 WHERE username = ?", (username,)
            )
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        user = _row_to_user(row)
        if user["is_admin"]:
            user["allowed_tools"] = list(TOOL_KEYS)
        else:
            role_row = conn.execute(
                "SELECT * FROM roles WHERE name = ?", (user["role_name"],)
            ).fetchone()
            user["allowed_tools"] = _row_to_role(role_row)["allowed_tools"] if role_row else []
        return user


def list_users() -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_row_to_user(r) for r in rows]


def get_user(username: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _row_to_user(row) if row else None


def create_user(username: str, password: str, full_name: str, role_name: str, is_admin: bool = False) -> None:
    if not username or not password:
        raise ValueError("Username and password are required.")
    validate_new_password(password)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role_name, is_admin) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), full_name, role_name, int(is_admin)),
        )


def update_user(
    username: str, *, password: str | None = None, full_name: str | None = None,
    role_name: str | None = None, is_admin: bool | None = None,
) -> None:
    if password:
        validate_new_password(password)
    with _get_conn() as conn:
        if password:
            conn.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                         (hash_password(password), username))
        if full_name is not None:
            conn.execute("UPDATE users SET full_name = ? WHERE username = ?", (full_name, username))
        if role_name is not None:
            conn.execute("UPDATE users SET role_name = ? WHERE username = ?", (role_name, username))
        if is_admin is not None:
            conn.execute("UPDATE users SET is_admin = ? WHERE username = ?", (int(is_admin), username))


def change_own_password(username: str, current_password: str, new_password: str) -> None:
    """Self-service change (including the forced one after logging in with
    a must_change_password account): checks the current password, then
    clears the must-change flag."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        if new_password == current_password:
            raise ValueError("New password must be different from the current one.")
        validate_new_password(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE username = ?",
            (hash_password(new_password), username),
        )


def delete_user(username: str) -> None:
    with _get_conn() as conn:
        remaining_admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND username != ?", (username,)
        ).fetchone()[0]
        is_target_admin = conn.execute(
            "SELECT is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
        if is_target_admin and is_target_admin[0] and remaining_admins == 0:
            raise ValueError("Can't delete the last remaining admin account.")
        conn.execute("DELETE FROM users WHERE username = ?", (username,))


def list_roles() -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM roles ORDER BY name").fetchall()
        return [_row_to_role(r) for r in rows]


def create_role(name: str, allowed_tools: list[str]) -> None:
    if not name:
        raise ValueError("Role name is required.")
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO roles (name, allowed_tools) VALUES (?, ?)",
            (name, json.dumps(allowed_tools)),
        )


def update_role(name: str, allowed_tools: list[str]) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE roles SET allowed_tools = ? WHERE name = ?",
            (json.dumps(allowed_tools), name),
        )


def delete_role(name: str) -> None:
    with _get_conn() as conn:
        in_use = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role_name = ?", (name,)
        ).fetchone()[0]
        if in_use:
            raise ValueError(f"Can't delete role {name!r} -- {in_use} user(s) still assigned to it.")
        conn.execute("DELETE FROM roles WHERE name = ?", (name,))
