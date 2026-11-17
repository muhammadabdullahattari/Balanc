"""Add crash-safe disposal intents table and reconciliation status."""

from alembic import op
from sqlalchemy import text

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    runtime_attr = op.get_context().config.attributes.get("runtime_role")
    runtime = quote(runtime_attr) if runtime_attr else "CURRENT_USER"

    op.execute(
        text(f"""
        ALTER TABLE app.cleanup_requests DROP CONSTRAINT IF EXISTS cleanup_requests_status_check;
        ALTER TABLE app.cleanup_requests ADD CONSTRAINT cleanup_requests_status_check
            CHECK (status IN ('preview', 'approved', 'disposing', 'failed', 'awaiting_confirmation', 'verified', 'confirmed'));

        CREATE TABLE IF NOT EXISTS app.disposal_intents (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES app.businesses(id) ON DELETE CASCADE,
            cleanup_request_id uuid NOT NULL REFERENCES app.cleanup_requests(id) ON DELETE CASCADE,
            resource_id uuid NOT NULL,
            location text NOT NULL,
            object_key text NOT NULL,
            state text NOT NULL DEFAULT 'intent_recorded'
                CHECK (state IN ('intent_recorded', 'unlinked', 'completed', 'rolled_back')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (business_id, cleanup_request_id) REFERENCES app.cleanup_requests(business_id, id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS disposal_intents_business ON app.disposal_intents (business_id, state);
        CREATE INDEX IF NOT EXISTS disposal_intents_pending ON app.disposal_intents (state)
            WHERE state IN ('intent_recorded', 'unlinked');

        ALTER TABLE app.disposal_intents ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.disposal_intents FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS disposal_intents_tenant ON app.disposal_intents;
        CREATE POLICY disposal_intents_tenant ON app.disposal_intents
            USING (business_id = app.scope() AND app.has_access(business_id))
            WITH CHECK (business_id = app.scope() AND app.has_access(business_id));

        GRANT SELECT, INSERT, UPDATE, DELETE ON app.disposal_intents TO {runtime};
    """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DROP TABLE IF EXISTS app.disposal_intents;
        ALTER TABLE app.cleanup_requests DROP CONSTRAINT IF EXISTS cleanup_requests_status_check;
        ALTER TABLE app.cleanup_requests ADD CONSTRAINT cleanup_requests_status_check
            CHECK (status IN ('preview', 'approved', 'failed', 'awaiting_confirmation', 'verified', 'confirmed'));
    """)
    )
