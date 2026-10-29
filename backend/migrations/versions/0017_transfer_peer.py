"""Each imported bank side can be claimed as a transfer peer only once."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX bank_peer_once ON app.bank_rows(business_id,peer_id) "
        "WHERE peer_id IS NOT NULL"
    )


def downgrade() -> None:
    raise RuntimeError("Preserve transfer-side uniqueness; use a forward repair")
