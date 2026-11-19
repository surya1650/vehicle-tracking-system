"""Record field-level sources for vehicle profile data."""

from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

revision = "91d2bc407b12"
down_revision = "ca43f3d4c014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicles", Column("profile_sources", JSONB()))


def downgrade() -> None:
    op.drop_column("vehicles", "profile_sources")
