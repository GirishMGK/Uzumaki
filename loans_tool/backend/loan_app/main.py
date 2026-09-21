"""Loan Analytics — FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from loan_app.api import (
    auth,
    blob_upload,
    downloads,
    ead_consolidate,
    engagements,
    runs,
    system,
    uploads,
)
from loan_app.api import settings as settings_api
from fcmr_core.catalog import store
from fcmr_core.catalog.store import init_catalog
from fcmr_core.config import settings
from fcmr_core.logging_setup import get_logger

logger = get_logger("loan_app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_catalog()
    auth._ensure_admin()
    store.init_settings()
    logger.info("Application startup: Loan Analytics ready")
    yield
    logger.info("Application shutdown")


app = FastAPI(
    title="Loan Analytics",
    description=(
        "Audit Analytics & Automated Solutions — "
        "Deterministic KYC and data-quality analytics for NBFC loan audits."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Ensure catalog + admin user exist — idempotent, safe to call on every cold start
_initialized = False


def _ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        settings.ensure_dirs()
        init_catalog()
        auth._ensure_admin()
        store.init_settings()
        _initialized = True


# Login requirement middleware
class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """Gates every path except /login, /static, /api/blob-noop behind a
    session.

    Trust-host-auth mode (LOANS_TRUST_HOST_AUTH=1, set by _pages/loans.py
    when this runs embedded inside Uzumaki): Uzumaki's own per-user login +
    role-based tool access already gate whether this page is reachable at
    all, so asking the user to log in a *second* time with this app's own
    separate admin/admin123 account would be redundant, not additional
    security -- the iframe is only ever loaded for someone Uzumaki has
    already authenticated. Auto-populates the same session shape a real
    POST /login would (see app/api/auth.py's login()) instead of redirecting,
    so every route downstream (which reads request.session["username"] for
    audit-log attribution, etc.) behaves identically either way. Standalone
    / Vercel / Electron-desktop deployments never set this flag and keep
    their own real login untouched.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        _ensure_initialized()
        if os.environ.get("LOANS_TRUST_HOST_AUTH") == "1" and "username" not in request.session:
            request.session["username"] = auth._ADMIN_USERNAME
            request.session["display_name"] = "Loan Analytics"
            return await call_next(request)
        public_paths = {"/login", "/static", "/api/blob-noop"}
        # Check if path starts with any public path
        is_public = any(request.url.path.startswith(p) for p in public_paths)
        if is_public:
            return await call_next(request)
        # Require login for all other paths (including /)
        if "username" not in request.session:
            return RedirectResponse(url="/login", status_code=303)
        return await call_next(request)


# Add middlewares in reverse order (last added = innermost = runs first)
app.add_middleware(LoginRequiredMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

# Static files
_static_dir = settings.base_dir / "loan_app" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Auth routes (login/logout) — public
app.include_router(auth.router, prefix="", tags=["auth"])

# Engagement routes (at /) — require login; "/" is the workspace selector
app.include_router(engagements.router, prefix="", tags=["engagements"])

# Upload/dashboard routes (at /dashboard) — require login; main analytics workspace
app.include_router(uploads.router, prefix="/dashboard", tags=["uploads"])

# Run/analytics routes (at /runs) — require login
app.include_router(runs.router, prefix="", tags=["runs"])

# Download routes — require login
app.include_router(downloads.router, prefix="", tags=["downloads"])

# Settings routes — require login
app.include_router(settings_api.router, prefix="", tags=["settings"])

# System info & monitoring routes — require login
app.include_router(system.router, prefix="/api", tags=["system"])

# Blob upload routes (token endpoint is public; register endpoint requires login)
app.include_router(blob_upload.router, prefix="", tags=["blob"])

# EAD consolidation routes — require login
app.include_router(ead_consolidate.router, prefix="", tags=["ead"])
