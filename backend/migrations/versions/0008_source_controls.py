"""Scoped membership administration and source/close guards."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    role = quote(op.get_context().config.attributes["runtime_role"])
    owner = connection.execute(text("SELECT current_user")).scalar_one()
    # Role name is emitted as a SQL string literal only after escaping apostrophes.
    source = (Path(__file__).parent / "0008_source_controls.sql").read_text("utf-8")
    connection.execute(text(source.replace("__RUNTIME__", role)
        .replace("__OWNER_LITERAL__", "'" + owner.replace("'", "''") + "'")))


def downgrade() -> None:
    raise RuntimeError("Use a reviewed forward repair preserving authorization history")
