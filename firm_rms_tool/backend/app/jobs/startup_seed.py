"""Idempotent boot-time bootstrap: default admin user + app_config rows.

Runs on container start (see Dockerfile CMD). This is NOT the demo/sample
dataset described in §14 (6 offices, 300 staff, 200 clients, ...) — that
lives in seed/seed_data.py and is run explicitly, never automatically,
since a firm's production deployment must never get demo data injected.
"""
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import engine, init_db
from app.models.enums import UserRole
from app.models.reference import AppConfig
from app.models.user import User

DEFAULT_APP_CONFIG: dict[str, dict] = {}


def _default_config() -> dict[str, dict]:
    s = get_settings()
    return {
        "max_article_ratio": {"value": s.default_max_article_ratio, "description": "R12 GRADE_MIX_BREACH threshold (articles:qualified)"},
        "burnout_weeks": {"value": s.default_burnout_weeks, "description": "R15 SUSTAINED_OVERLOAD consecutive-week threshold"},
        "bench_days": {"value": s.default_bench_days, "description": "Consecutive free working days to flag bench_flag"},
        "max_cost_ratio": {"value": s.default_max_cost_ratio, "description": "R18 BUDGET_OVERRUN cost/fee ratio ceiling"},
        "cooling_off_months": {"value": s.default_cooling_off_months, "description": "R24 COOLING_OFF months after leaving a client's employ"},
        "max_days_single_client": {"value": s.default_max_days_single_client, "description": "R20 NO_EXPOSURE_DIVERSITY threshold"},
        "article_secondment_cap": {"value": s.default_article_secondment_cap, "description": "R13 ICAI Reg. 43 secondment cap per principal"},
        "article_secondment_months_cap": {"value": s.default_article_secondment_months_cap, "description": "R13 aggregate secondment months cap"},
    }


def run() -> None:
    init_db()
    with Session(engine) as session:
        for key, payload in _default_config().items():
            existing = session.exec(select(AppConfig).where(AppConfig.key == key)).first()
            if existing is None:
                session.add(AppConfig(key=key, value=payload["value"] if isinstance(payload["value"], dict) else {"value": payload["value"]}, description=payload["description"]))

        admin_email = "admin@firm.local"
        existing_admin = session.exec(select(User).where(User.email == admin_email)).first()
        if existing_admin is None:
            session.add(
                User(
                    email=admin_email,
                    hashed_password=hash_password("ChangeMe!2026"),
                    role=UserRole.ADMIN,
                    full_name="System Administrator",
                    must_change_password=True,
                )
            )
        session.commit()


if __name__ == "__main__":
    run()
