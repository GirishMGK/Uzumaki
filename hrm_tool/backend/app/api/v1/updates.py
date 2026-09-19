""""Check for updates" for the Windows desktop build (§ Manpower Allocation
tab's sibling feature — a small, self-contained one). The app has no
silent auto-update: this just tells the user whether a newer release
exists and hands them the download link, exactly like every other
installer-distributed desktop tool. Nothing here runs unless a signed-in
user clicks the button — no background polling, no telemetry.

Reads `Settings.update_check_repo`'s GitHub Releases API (public,
unauthenticated, rate-limited per source IP by GitHub itself) and compares
the release's tag against this build's own version (app.core.version).
Network failures (offline desktop, corporate proxy, GitHub down) are
caught and reported as `checked_ok: false` rather than a 5xx — "couldn't
check right now" is a normal, expected outcome here, not a server error.
"""
import httpx
from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.version import get_app_version
from app.models.user import User

router = APIRouter()


def _parse_version(v: str) -> tuple[int, int, int]:
    """'v1.2.3' / '1.2.3' / '1.2' -> (1, 2, 3); anything unparsable -> (0, 0, 0)
    so a malformed tag never crashes the comparison (worst case: it just
    won't look newer than what's running)."""
    v = v.strip().lstrip("vV")
    parts = (v.split("+")[0].split("-")[0].split(".") + ["0", "0", "0"])[:3]
    out = []
    for p in parts:
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return (out[0], out[1], out[2])


def _pick_download_url(release: dict) -> str | None:
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".exe"):
            return asset.get("browser_download_url")
    # No .exe asset found (e.g. a source-only release) — the release page
    # itself is still a useful fallback link.
    return release.get("html_url")


@router.get("/check")
def check_for_updates(user: User = Depends(get_current_user)) -> dict:
    current_version = get_app_version()
    repo = get_settings().update_check_repo
    base = {
        "current_version": current_version,
        "latest_version": None,
        "update_available": False,
        "download_url": None,
        "release_notes_url": None,
        "checked_ok": False,
        "message": None,
    }
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=5.0,
        )
        if resp.status_code == 404:
            # A real, reachable answer — the repo just has no GitHub Release
            # published yet — not a connectivity problem, so say so rather
            # than blaming the user's internet.
            base["message"] = "No releases have been published yet."
            return base
        resp.raise_for_status()
        release = resp.json()
    except Exception:
        base["message"] = "Could not reach GitHub to check for updates — check your internet connection and try again."
        return base

    latest_tag = release.get("tag_name", "")
    latest_version = latest_tag.lstrip("vV") or None
    update_available = latest_version is not None and _parse_version(latest_tag) > _parse_version(current_version)
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "download_url": _pick_download_url(release) if update_available else None,
        "release_notes_url": release.get("html_url"),
        "checked_ok": True,
        "message": None,
    }
