"""Durable local evidence queue with bounded retries and leased completion."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    source = (Path(__file__).parent / "0009_evidence_jobs.sql").read_text("utf-8")
    connection.execute(text(source.replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Use a forward repair preserving evidence jobs and leases")
