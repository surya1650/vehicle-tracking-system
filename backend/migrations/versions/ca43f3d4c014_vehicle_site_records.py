"""Add site vehicle records, camera areas, and visit parking confirmations."""

import sqlalchemy as sa
from alembic import op

revision = "ca43f3d4c014"
down_revision = "26b07745e378"
branch_labels = None
depends_on = None

PROFILE_COLUMNS = (
    ("manufacturer", sa.String(80)),
    ("model", sa.String(80)),
    ("color", sa.String(40)),
    ("fuel_type", sa.String(40)),
    ("registration_valid_until", sa.Date()),
    ("insurance_valid_until", sa.Date()),
    ("fitness_valid_until", sa.Date()),
    ("puc_certificate_number", sa.String(80)),
    ("puc_valid_until", sa.Date()),
    ("profile_updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column("cameras", sa.Column("area", sa.String(120)))
    for name, type_ in PROFILE_COLUMNS:
        op.add_column("vehicles", sa.Column(name, type_))
    op.create_table(
        "parking_confirmations",
        sa.Column("entry_event_id", sa.Uuid(), sa.ForeignKey("vehicle_events.event_uuid"), primary_key=True),
        sa.Column("registration_number", sa.String(24), sa.ForeignKey("vehicles.registration_number"), nullable=False),
        sa.Column("zone", sa.String(80), nullable=False),
        sa.Column("bay", sa.String(40)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("parking_confirmations")
    for name, _ in reversed(PROFILE_COLUMNS):
        op.drop_column("vehicles", name)
    op.drop_column("cameras", "area")
