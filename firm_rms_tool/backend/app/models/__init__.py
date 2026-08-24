"""SQLModel entity definitions.

Import order matters for SQLAlchemy relationship resolution — `app.db.base`
imports every model module so `SQLModel.metadata` is fully populated before
`create_all` / Alembic autogenerate run.
"""
