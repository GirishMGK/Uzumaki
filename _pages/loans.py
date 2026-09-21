"""Hub page: Loan Analytics — NBFC loan-portfolio audit analytics.

Runs the vendored Loan Analytics FastAPI backend (originally "SanGir
Automations" / FCMR) in-process (a background thread, started once per app
launch) and embeds its server-rendered UI via an iframe on the same
origin/port -- same pattern as hrm.py's HRM integration.

One deliberate difference from HRM: this tool's own login is skipped
entirely (LOANS_TRUST_HOST_AUTH=1, read by loans_tool/backend/loan_app/main.py's
LoginRequiredMiddleware) -- Uzumaki's own per-user login + role-based tool
access already gate whether this page is reachable at all, so its original
separate admin/admin123 login would just be a redundant second prompt, not
additional security.

Source: vendored from `girishmgk/FCMR` (loan_app/ + fcmr_core/, no Electron/
PyInstaller/Vercel-specific files -- Uzumaki has its own packaging/launcher
already).
"""
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "loans_tool", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from _pages.theme import footer

HOST = "127.0.0.1"
PORT = 8767

# Same reasoning as hrm.py: a compact header instead of the full sa-hero
# banner, so the embedded app's own UI is visible immediately, not pushed
# below the fold by our own page chrome on top of it.
st.subheader("📒 Loan Analytics — NBFC Loan-Portfolio Audit Analytics")
st.caption(
    "KYC & data-quality checks, duplicate/UCID detection, PIN/address "
    "validation, ICAI-sampled Excel audit workpapers, EAD/ECL file "
    "consolidation. Deterministic — no AI/LLM."
)


def _app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = str(Path.home())
    data_dir = Path(base) / "UzumakiLoanAnalytics"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _configure_environment() -> None:
    # Trust Uzumaki's own login instead of asking again -- see this file's
    # module docstring and LoginRequiredMiddleware's own docstring in
    # loans_tool/backend/loan_app/main.py for the full reasoning.
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"

    # fcmr_core/config.py already resolves a per-user appdata directory on
    # its own when sys.frozen is set (which it is, process-wide, whenever
    # this is running as Uzumaki.exe) -- same auto-detection HRM's own
    # backend does. The only thing genuinely worth persisting ourselves is
    # the Aadhaar-hashing salt: config.py's own default is a literal
    # "change-me" placeholder, fine for a quick dev run but not something
    # to actually ship, so generate + persist a real one exactly once,
    # mirroring hrm.py's JWT-secret-file pattern.
    data_dir = _app_data_dir()
    salt_file = data_dir / "aadhaar_salt.key"
    if not salt_file.exists():
        import secrets
        salt_file.write_text(secrets.token_hex(32), encoding="utf-8")
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", salt_file.read_text(encoding="utf-8").strip())
    os.environ.setdefault("FCMR_BACKEND_PORT", str(PORT))


def _health_ok() -> bool:
    # No dedicated /health route -- /login is always public regardless of
    # session/auth state (see LoginRequiredMiddleware), so it's a reliable
    # readiness probe even with LOANS_TRUST_HOST_AUTH active.
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/login", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@st.cache_resource(show_spinner=False)
def _start_backend() -> str:
    """Starts the Loan Analytics backend exactly once per app run (cached
    across reruns/sessions via st.cache_resource) and blocks until it's
    serving."""
    _configure_environment()

    from loan_app.main import app as fastapi_app
    import uvicorn

    def _serve():
        uvicorn.run(fastapi_app, host=HOST, port=PORT, log_level="warning")

    threading.Thread(target=_serve, daemon=True, name="loans-backend").start()

    for _ in range(60):
        if _health_ok():
            break
        time.sleep(0.5)
    return f"http://{HOST}:{PORT}/"


with st.spinner("Starting Loan Analytics (first launch creates the local database)…"):
    try:
        url = _start_backend()
    except Exception as e:
        st.error(f"Loan Analytics failed to start: {e}")
        st.stop()

if not _health_ok():
    st.error("Loan Analytics started but isn't responding yet — try reopening this page.")
else:
    st.components.v1.iframe(url, height=900, scrolling=True)
    st.caption(f"Data stored locally at `{_app_data_dir()}`. Signed in as you, via Uzumaki.")

with st.expander("What this does"):
    st.markdown(
        """
Deterministic (no AI/LLM anywhere — a hard auditability requirement) audit
analytics for NBFC loan portfolios:

- **KYC / data-quality analytics** — 24 rules across 4 categories: KYC &
  document format (PAN/Aadhaar-Verhoeff/Voter/Passport/DL/Mobile/Email/DOB),
  Address & PIN (validated against the bundled India Post PIN master),
  Duplicate detection (PAN/Aadhaar/Mobile/Bank/Voter/Name+DOB/Address), and
  Identity grouping (UCID — union-find across matching identifiers, flags
  KYC inconsistencies).
- **EAD/ECL file consolidation** — merges multiple L&T-Finance-style loan
  exports (39 canonical columns) into one file, tolerant of differing
  columns across files.
- **ICAI-sampled Excel workpaper** — 4-sheet audit deliverable (Lead Sheet,
  Detailed Exceptions, TOC/TOD with a seeded stratified sample, Methodology)
  — sign-off ready.

An engagement scopes each audit job; upload a CSV export, map its columns
to canonical fields (auto-suggested, remembered per header signature), run
the rules, download exception reports or the workpaper.

Like HRM, this keeps **persistent, multi-session data** in a local
database rather than processing an upload and discarding it — its own
server runs in the background (same process as this app), but reuses your
Uzumaki login rather than asking you to sign in a second time.

Source: vendored from the `FCMR` repo (`loan_app/` + `fcmr_core/`), originally
"SanGir Automations."
"""
    )

footer()
