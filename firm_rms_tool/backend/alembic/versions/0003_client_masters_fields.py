"""Client masters fields for the firm's actual staff/client screens.

Adds is_listed, priority, is_mnc, practicing_firm, primary_service_type to
clients, and widens entity_class to fit the new "Nature" values added to
EntityClass (SECTION_8, COOPERATIVE_SOCIETY, SOLE_PROPRIETORSHIP,
PARTNERSHIP_FIRM, OTHERS — see app.models.enums). relationship_status's
new INACTIVE value fits the existing VARCHAR(8) width (same length as
PROSPECT) so it needs no column change.

These enum-backed columns are plain VARCHAR with no native DB enum type
and no CHECK constraint (see app.models.client.Client — SQLModel maps a
`str, Enum` field to AutoString, not sqlalchemy.Enum), so on SQLite this
migration's only real effect is the new columns; the width guard below is
for Postgres only, which does enforce VARCHAR(n).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("is_listed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("clients", sa.Column("priority", sa.String(10), nullable=False, server_default="MEDIUM"))
    op.add_column("clients", sa.Column("is_mnc", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("clients", sa.Column("practicing_firm", sa.String(10), nullable=True))
    op.add_column("clients", sa.Column("primary_service_type", sa.String(20), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("clients", "entity_class", type_=sa.String(30))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("clients", "entity_class", type_=sa.String(15))

    op.drop_column("clients", "primary_service_type")
    op.drop_column("clients", "practicing_firm")
    op.drop_column("clients", "is_mnc")
    op.drop_column("clients", "priority")
    op.drop_column("clients", "is_listed")
