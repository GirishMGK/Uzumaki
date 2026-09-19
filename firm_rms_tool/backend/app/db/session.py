from collections.abc import Generator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)

# (table, column, DDL type, default SQL literal or None) for every column
# added to an already-shipped table outside of a docker-compose deploy —
# create_all() below only creates missing *tables*, it never adds a
# column to a table that already exists. docker-compose runs
# `alembic upgrade head` before this and so is unaffected either way
# (the column already exists by the time _add_missing_columns runs, so
# it's a no-op there); the desktop app and a bare local dev checkout have
# no such migration step, so an upgrade-in-place needs this or it hits
# "no such column" the first time the new field is touched. Append to
# this list (don't replace old entries) the next time a column is added
# to an existing table.
_COLUMN_PATCHES: list[tuple[str, str, str, str | None]] = [
    ("clients", "is_listed", "BOOLEAN", "FALSE"),
    ("clients", "priority", "VARCHAR(10)", "'MEDIUM'"),
    ("clients", "is_mnc", "BOOLEAN", "FALSE"),
    ("clients", "practicing_firm", "VARCHAR(10)", None),
    ("clients", "primary_service_type", "VARCHAR(20)", None),
]


def init_db() -> None:
    """Dev/test convenience bootstrap. Production uses Alembic migrations."""
    from app.db import base  # noqa: F401  ensures metadata is populated

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl_type, default in _COLUMN_PATCHES:
            if table not in tables:
                continue  # freshly created above by create_all(), already correct
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            clause = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'
            if default is not None:
                clause += f" DEFAULT {default}"
            conn.execute(text(clause))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
