"""Authentication endpoints."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Default admin user created on first run
_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD = "admin123"  # Must be changed on first login


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash password with salt using pbkdf2. Returns (hash, salt)."""
    if salt is None:
        salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return hash_obj.hex(), salt


def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify password against hash and salt."""
    computed_hash, _ = _hash_password(password, salt)
    return computed_hash == password_hash


def _ensure_admin() -> None:
    """Create default admin user if it doesn't exist."""
    try:
        user = store.get_user(_ADMIN_USERNAME)
        if user is None:
            pwd_hash, salt = _hash_password(_ADMIN_PASSWORD)
            # Store salt:hash in password_hash field
            store.create_user(_ADMIN_USERNAME, f"{salt}:{pwd_hash}", "Admin User")
            print(
                f"\n🔐 Default admin user created: username='{_ADMIN_USERNAME}', password='{_ADMIN_PASSWORD}'"
            )
            print("   ⚠️  CHANGE THIS PASSWORD IMMEDIATELY ON FIRST LOGIN!\n")
    except Exception:
        pass  # User might already exist


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    """Login form."""
    return templates.TemplateResponse(request=request, name="login.html")


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Authenticate user and set session cookie."""
    user = store.get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Extract salt and hash
    pwd_parts = user["password_hash"].split(":")
    if len(pwd_parts) != 2:
        raise HTTPException(status_code=500, detail="Invalid password format")
    salt, stored_hash = pwd_parts

    if not _verify_password(password, stored_hash, salt):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Create response and set session cookie
    response = RedirectResponse(url="/", status_code=303)
    request.session["username"] = username
    request.session["display_name"] = user["display_name"]
    return response


@router.post("/logout")
async def logout(request: Request):
    """Logout user and clear session."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
