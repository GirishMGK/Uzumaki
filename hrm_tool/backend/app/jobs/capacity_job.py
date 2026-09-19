"""Nightly capacity_daily rebuild (§5) via APScheduler, in-process (§1 — no
Celery/Redis for v1, single-box deployment).

Rolling window: 30 days back (so recent actuals/corrections stay current)
to 180 days forward (covers the scheduler's planning horizon well beyond
the 8-week default view). Registered from `app/main.py`'s startup event.
"""
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.db.session import engine
from app.services.capacity_materializer import recompute_range

ROLLING_BACK_DAYS = 30
ROLLING_FORWARD_DAYS = 180

_scheduler: BackgroundScheduler | None = None


def run_nightly_recompute() -> int:
    today = date.today()
    with Session(engine) as db:
        return recompute_range(db, today - timedelta(days=ROLLING_BACK_DAYS), today + timedelta(days=ROLLING_FORWARD_DAYS))


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(run_nightly_recompute, "cron", hour=2, minute=0, id="capacity_nightly_recompute")
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
