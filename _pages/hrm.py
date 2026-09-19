"""Hub page: HRM — Manpower / Resource Tracking.

Runs the vendored HRM FastAPI backend in-process (a background thread,
started once per app launch) and embeds its already-built frontend via an
iframe on the same origin/port -- so from here it behaves like any other
Uzumaki tool: one app, one click, no separate installer or server to run
yourself.

Adapted from hrm_tool/backend's own desktop/launcher.py (from the
Manpower-Tracker repo this was vendored from), which already solved:
per-user writable data dir (so it works from a read-only install folder),
a persisted JWT secret, and single-process static-file serving. The only
real difference here is uvicorn runs on a background thread rather than
blocking the process, since this process is also running Streamlit.
"""
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "hrm_tool", "backend")
_FRONTEND_DIST = os.path.join(_REPO_ROOT, "hrm_tool", "frontend_dist")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from _pages.theme import footer

HOST = "127.0.0.1"
PORT = 8766

# Deliberately NOT the full sa-hero page_header() banner every other hub
# page uses (icon + title + subtitle + badges -- ~150-250px tall): real
# feedback was that its login form is "down the scroll" on first open. The
# login form lives INSIDE the embedded iframe below, already at the top of
# HRM's own UI -- the only thing making it require a scroll at all was our
# own host page's header pushing the whole iframe further down before the
# user ever sees it. A single compact line here instead of the hero banner
# gets the iframe (and the login form inside it) into view immediately.
st.subheader("🧑‍💼 HRM — Manpower & Resource Tracking")
st.caption(
    "Scheduler board, capacity dashboards, timesheets, forecasting. "
    "Own local database — login required, first run creates it."
)


def _app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = str(Path.home())
    data_dir = Path(base) / "HRM"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _configure_environment() -> None:
    data_dir = _app_data_dir()
    db_path = data_dir / "hrm.db"
    os.environ.setdefault("RMS_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    secret_file = data_dir / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
    os.environ.setdefault("RMS_JWT_SECRET_KEY", secret_file.read_text(encoding="utf-8").strip())

    os.environ.setdefault("RMS_ENVIRONMENT", "desktop")
    os.environ.setdefault("RMS_CORS_ORIGINS", f'["http://{HOST}:{PORT}"]')
    if os.path.isdir(_FRONTEND_DIST):
        os.environ.setdefault("RMS_STATIC_DIR", _FRONTEND_DIST)


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@st.cache_resource(show_spinner=False)
def _start_backend() -> str:
    """Starts the HRM backend exactly once per app run (cached across
    reruns/sessions via st.cache_resource) and blocks until it's serving."""
    _configure_environment()

    from app.jobs import startup_seed
    startup_seed.run()

    from app.main import app as fastapi_app
    import uvicorn

    def _serve():
        uvicorn.run(fastapi_app, host=HOST, port=PORT, log_level="warning")

    threading.Thread(target=_serve, daemon=True, name="hrm-backend").start()

    for _ in range(60):
        if _health_ok():
            break
        time.sleep(0.5)
    return f"http://{HOST}:{PORT}/"


with st.spinner("Starting HRM (first launch creates the local database)…"):
    try:
        url = _start_backend()
    except Exception as e:
        st.error(f"HRM failed to start: {e}")
        st.stop()

if not _health_ok():
    st.error("HRM started but isn't responding yet — try reopening this page.")
else:
    st.components.v1.iframe(url, height=900, scrolling=True)
    st.caption(
        f"Data stored locally at `{_app_data_dir()}`. "
        "Default login on first run: **admin@firm.local** / **ChangeMe!2026** "
        "(you'll be asked to change it)."
    )

with st.expander("What this does"):
    st.markdown(
        """
HRM is a full resource-management system (scheduler board, capacity
dashboards, report library, timesheets/actuals, forecasting, RBAC) — unlike
the other tools in this hub, it keeps **persistent, multi-session data** in
a local SQLite database rather than processing an uploaded file and
discarding it. It runs here as its own server (in the background, same
process as this app) with its own login, so treat it as its own app inside
the hub rather than a stateless one-shot tool.

Source: vendored from `hrm_tool/` (the `Manpower-Tracker` repo's
`backend/` + a static build of `frontend/`).
"""
    )

footer()
