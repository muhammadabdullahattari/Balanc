"""Bounded progressive throttling without locking or suspending user accounts."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.auth_rate_buckets ADD COLUMN blocked_until timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE app.auth_rate_buckets DROP COLUMN blocked_until")
