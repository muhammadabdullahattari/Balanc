"""Reviewed opening subledgers retain original dates without replaying historic cash."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    source = Path(__file__).with_suffix(".sql").read_text("utf-8")
    op.get_bind().execute(text(source.replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve reviewed opening balances; use forward corrections")
