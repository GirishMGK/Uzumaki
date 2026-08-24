"""Postgres EXCLUDE constraint: hard-stop 100% double-booking (§3.8).

Partial-overlap / partial-percentage combinations (e.g. two 50% bookings)
are validated in the service layer (app.services.conflict_engine, R1) since
they require summing across an arbitrary number of overlapping rows, which
an EXCLUDE constraint cannot express. This migration only guards the exact
case the constraint *can* express: two 100% CONFIRMED/IN_PROGRESS bookings
for the same staff member on overlapping dates.

SQLite has no EXCLUDE/gist support, so this is a no-op there — dev/test
integrity relies entirely on the service-layer check in that case.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-19
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE allocations
        ADD CONSTRAINT allocations_no_full_overlap
        EXCLUDE USING gist (
            staff_id WITH =,
            daterange(date_from::date, date_to::date, '[]') WITH &&
        )
        WHERE (
            status IN ('CONFIRMED', 'IN_PROGRESS')
            AND allocation_pct = 100
            AND is_active
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE allocations DROP CONSTRAINT IF EXISTS allocations_no_full_overlap")
