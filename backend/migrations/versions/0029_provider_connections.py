"""Persist tenant provider connections and received Stripe events."""

from alembic import op
from sqlalchemy import text

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE app.provider_connections (
            business_id uuid NOT NULL REFERENCES app.businesses(id),
            provider text NOT NULL CHECK (provider IN ('stripe','quickbooks')),
            details jsonb NOT NULL,
            PRIMARY KEY (business_id, provider)
        );
        ALTER TABLE app.provider_connections ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.provider_connections FORCE ROW LEVEL SECURITY;
        CREATE POLICY provider_owner ON app.provider_connections
            USING (business_id=app.scope() AND app.has_role(business_id, ARRAY['owner']))
            WITH CHECK (business_id=app.scope() AND app.has_role(business_id, ARRAY['owner']));
        CREATE TABLE app.stripe_event_inbox (
            id text PRIMARY KEY,
            event_type text NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now()
        );
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON app.provider_connections TO {role}"))
    op.execute(text(f"GRANT INSERT ON app.stripe_event_inbox TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve provider connection history; use a forward repair")
