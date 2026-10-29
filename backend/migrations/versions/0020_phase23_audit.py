"""Guard reversal chronology and bind sessions to password generations."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(text(Path(__file__).with_suffix(".sql").read_text("utf-8")))


def downgrade() -> None:
    raise RuntimeError("Preserve authentication and accounting guards; use forward repair")
