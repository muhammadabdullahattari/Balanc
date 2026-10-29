"""Reviewed suggestions, retained exports, privacy decisions and opening adjustments."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.get_bind().execute(text(Path(__file__).with_suffix(".sql").read_text("utf-8")
                              .replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve closure audit/source records; use forward repair")
