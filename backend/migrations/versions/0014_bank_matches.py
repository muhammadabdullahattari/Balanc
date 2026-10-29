"""One bank movement may match a bounded bundle of existing source journals."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    source = (Path(__file__).parent / "0014_bank_matches.sql").read_text("utf-8")
    connection.execute(text(source.replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve immutable bank-match evidence through a forward repair")
