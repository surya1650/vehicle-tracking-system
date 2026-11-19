"""Support tracking cameras and sighting events."""

import sqlalchemy as sa
from alembic import op

revision = "26b07745e378"
down_revision = "e22c5b188b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cameras", "direction", type_=sa.String(8))
    op.alter_column("vehicle_events", "direction", type_=sa.String(8))
    op.drop_constraint("event_direction", "vehicle_events", type_="check")
    op.create_check_constraint("event_direction", "vehicle_events", "direction IN ('IN', 'OUT', 'SIGHTING')")


def downgrade() -> None:
    op.drop_constraint("event_direction", "vehicle_events", type_="check")
    op.create_check_constraint("event_direction", "vehicle_events", "direction IN ('IN', 'OUT')")
    op.alter_column("vehicle_events", "direction", type_=sa.String(3))
    op.alter_column("cameras", "direction", type_=sa.String(5))
