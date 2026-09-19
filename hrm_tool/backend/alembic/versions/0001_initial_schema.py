"""initial schema

Bootstraps every table from app.db.base.metadata in one shot rather than
hand-writing ~20 op.create_table() calls. This is deliberate for the
*initial* migration only — every migration after this one should be a
normal incremental op.* script (or `alembic revision --autogenerate` once
a real Postgres instance is available to diff against).

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""
from alembic import op

from app.db.base import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind=bind)
