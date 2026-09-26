"""Analytics hub — the "pick a dataset, then pick what to run against it"
landing page. EAD Files is the only report type with real analytics built
so far (fcmr_core/rules/ead_rules.py, ead_reports.py, ead_cross_dataset.py);
Customer Master keeps its existing per-upload KYC/duplicate-detection flow
(fcmr_core/rules/registry.py) unchanged rather than being migrated into the
newer consolidated-dataset pattern. Every other registered report type is
listed too, so the picker reflects what's actually been uploaded even
before analytics exist for it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store
from fcmr_core.schemas.loader import available_report_types, label_for_report_type

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# report_type -> analytics screen route. A type with no entry here has no
# analytics built yet, and the hub says so instead of linking anywhere.
_ANALYTICS_ROUTES = {
    "ead_files": "/dashboard/analytics/ead",
    # Customer Master's analytics are per-upload, not per-dataset -- there's
    # no single "run against everything" page to link to, so it points at
    # the uploads list instead, same as before this hub existed.
    "customer_master": "/dashboard",
}


@router.get("/dashboard/analytics", response_class=HTMLResponse)
async def analytics_hub(request: Request):
    engagement_id = request.session.get("engagement_id")
    uploads = store.list_uploads(engagement_id=engagement_id)
    ready_counts: dict[str, int] = {}
    for u in uploads:
        if u["status"] == "ready":
            ready_counts[u["report_type"]] = ready_counts.get(u["report_type"], 0) + 1

    datasets = []
    for report_type in available_report_types():
        datasets.append(
            {
                "report_type": report_type,
                "label": label_for_report_type(report_type),
                "ready_count": ready_counts.get(report_type, 0),
                "analytics_url": _ANALYTICS_ROUTES.get(report_type),
                # Consolidate & Download is generic -- every report type
                # gets one, unlike analytics_url above which only exists
                # for types with real checks built.
                "consolidate_url": f"/dashboard/consolidate/{report_type}",
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="analytics_hub.html",
        context={"datasets": datasets},
    )
