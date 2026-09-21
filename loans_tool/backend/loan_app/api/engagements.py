"""Engagements (workspace) endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@router.get("/", response_class=HTMLResponse)
async def engagements_list(request: Request):
    """List all engagements."""
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    engagements = store.list_engagements()
    return templates.TemplateResponse(
        request=request,
        name="engagements.html",
        context={"engagements": engagements},
    )


@router.post("/", response_class=HTMLResponse)
async def create_engagement(
    request: Request,
    name: str = Form(...),
    client_name: str = Form(""),
    period_from: str = Form(""),
    period_to: str = Form(""),
):
    """Create a new engagement."""
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    try:
        engagement_id = store.create_engagement(
            name=name,
            client_name=client_name or None,
            period_from=period_from or None,
            period_to=period_to or None,
            created_by=request.session["username"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create engagement: {e}")

    # Set as active engagement in session
    request.session["engagement_id"] = engagement_id
    request.session["engagement_name"] = name

    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/set-active/{engagement_id}")
async def set_active_engagement(request: Request, engagement_id: str):
    """Set the active engagement in the session."""
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    engagement = store.get_engagement(engagement_id)
    if not engagement:
        raise HTTPException(status_code=404, detail="Engagement not found")

    request.session["engagement_id"] = engagement_id
    request.session["engagement_name"] = engagement["name"]

    return RedirectResponse(url="/dashboard", status_code=303)
