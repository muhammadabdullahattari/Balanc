"""Permit locking business rows without granting updates to accounting settings."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    # PostgreSQL SELECT FOR UPDATE requires UPDATE on at least one column.
    # Currency, policy and other business settings remain outside runtime UPDATE grants.
    connection.exec_driver_sql(f"GRANT UPDATE(version) ON app.businesses TO {role}")


def downgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    connection.exec_driver_sql(f"REVOKE UPDATE(version) ON app.businesses FROM {role}")
