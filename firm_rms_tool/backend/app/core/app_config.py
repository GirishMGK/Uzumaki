"""Read editable rule thresholds from the `app_config` table (§3.15, §4).

Falls back to the Settings default if the DB row is missing (e.g. a test
DB that never ran `app/jobs/startup_seed.py`), so callers never have to
special-case "not seeded yet."
"""
from sqlmodel import Session, select

from app.models.reference import AppConfig


def get_config_value(db: Session, key: str, default: float) -> float:
    row = db.exec(select(AppConfig).where(AppConfig.key == key)).first()
    if row is None or not isinstance(row.value, dict) or "value" not in row.value:
        return default
    return row.value["value"]
