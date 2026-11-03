"""Bind unverified password ownership to the requesting browser."""

from alembic import op
from sqlalchemy import text

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text("ALTER TABLE app.users ADD COLUMN verification_csrf_hash varchar(64)")
    )


def downgrade() -> None:
    raise RuntimeError("Preserve identity protections; use forward repair")
