"""Manual-trial initialization, reasoned job replay and held beneficiary proposals."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    source = (Path(__file__).parent / "0016_trial_and_replay.sql").read_text("utf-8")
    connection.execute(text(source.replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve entitlement, replay and beneficiary-review history")
