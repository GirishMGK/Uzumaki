"""Weekly booking digest email, in-process APScheduler (§1: no Celery/Redis
for v1) — same pattern as `capacity_job.py`'s nightly recompute, a separate
scheduler instance since this is an unrelated concern (email, not capacity).

Fires Monday mornings so the digest lands before the work week starts,
covering the week ahead (today through +6 days).
"""
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.db.session import engine
from app.services.digest import send_weekly_digests

_scheduler: BackgroundScheduler | None = None


def run_weekly_digest() -> int:
    today = date.today()
    with Session(engine) as db:
        return send_weekly_digests(db, week_from=today, week_to=today + timedelta(days=6))


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(run_weekly_digest, "cron", day_of_week="mon", hour=7, minute=0, id="weekly_booking_digest")
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
