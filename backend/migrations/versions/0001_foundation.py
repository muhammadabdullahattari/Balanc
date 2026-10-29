"""Identity, tenant boundaries and immutable accounting foundation."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    directory = Path(__file__).parent
    connection = op.get_bind()
    role = op.get_context().config.attributes["runtime_role"]
    quoted = connection.dialect.identifier_preparer.quote(role)
    connection.exec_driver_sql((directory / "0001_tables.sql").read_text("utf-8"))
    sql = (directory / "0001_guards.sql").read_text("utf-8").replace("__RUNTIME__", quoted)
    connection.execute(text(sql))
    connection.execute(text("REVOKE ALL ON app.alembic_version FROM PUBLIC"))
    connection.exec_driver_sql(f"REVOKE ALL ON app.alembic_version FROM {quoted}")


def downgrade() -> None:
    # Posted financial history must never be silently erased by downgrade.
    raise RuntimeError(
        "Destructive downgrade is disabled; restore or use a reviewed forward repair"
    )
