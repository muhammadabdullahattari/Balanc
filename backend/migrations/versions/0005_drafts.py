"""Durable journal drafts and explicit immutable posting provenance."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    source = (Path(__file__).parent / "0005_drafts.sql").read_text("utf-8")
    connection.execute(text(source.replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Use a reviewed forward repair; do not erase posting provenance")
