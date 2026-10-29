"""Expose only migration revision metadata to the readiness probe."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    connection.exec_driver_sql(f"GRANT SELECT ON app.alembic_version TO {role}")


def downgrade() -> None:
    connection = op.get_bind()
    role = connection.dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    connection.exec_driver_sql(f"REVOKE SELECT ON app.alembic_version FROM {role}")
