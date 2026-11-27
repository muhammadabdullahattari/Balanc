"""Phase E to G schema: connectors, inbound email, public API keys, webhooks, fair-share ingestion queue, and AI cost ledger."""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    runtime_name = op.get_context().config.attributes.get("runtime_role")
    role = (
        op.get_bind().dialect.identifier_preparer.quote(runtime_name)
        if runtime_name
        else "CURRENT_USER"
    )
    op.execute(f"""
        -- E0..E6: Unified tenant connector accounts & sync runs
        CREATE TABLE IF NOT EXISTS app.connector_accounts (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          connector_kind text NOT NULL CHECK (
            connector_kind IN (
              'plaid',
              'stripe_fc',
              'quickbooks',
              'xero',
              'shopify',
              'square',
              'inbound_email',
              'google_drive',
              'dropbox'
            )
          ),
          display_name text NOT NULL,
          environment text NOT NULL DEFAULT 'sandbox' CHECK (environment IN ('sandbox', 'production')),
          status text NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'reauth_required', 'revoked')),
          encrypted_credentials text NOT NULL DEFAULT '',
          config_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          sync_cursor text NOT NULL DEFAULT '',
          last_synced_at timestamptz,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.connector_accounts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.connector_accounts FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.connector_accounts
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.connector_accounts TO {role};

        CREATE TABLE IF NOT EXISTS app.connector_sync_runs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          connector_account_id uuid,
          connector_kind text NOT NULL,
          direction text NOT NULL DEFAULT 'import' CHECK (direction IN ('import', 'export', 'webhook')),
          records_processed integer NOT NULL DEFAULT 0,
          records_created integer NOT NULL DEFAULT 0,
          records_deduplicated integer NOT NULL DEFAULT 0,
          status text NOT NULL DEFAULT 'completed',
          summary_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.connector_sync_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.connector_sync_runs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.connector_sync_runs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT ON app.connector_sync_runs TO {role};

        -- E5: Inbound invoice email intake & sender quarantine log
        CREATE TABLE IF NOT EXISTS app.inbound_invoice_emails (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          message_id text NOT NULL,
          sender_email text NOT NULL,
          recipient_address text NOT NULL,
          subject text NOT NULL DEFAULT '',
          spf_dkim_passed boolean NOT NULL DEFAULT true,
          status text NOT NULL CHECK (
            status IN ('ingested', 'quarantined_sender', 'deduplicated', 'rejected')
          ),
          fingerprint_id uuid,
          draft_id uuid,
          po_match_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, message_id)
        );
        ALTER TABLE app.inbound_invoice_emails ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.inbound_invoice_emails FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.inbound_invoice_emails
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.inbound_invoice_emails TO {role};

        -- E7: Public Developer API keys & HMAC outbound webhooks
        CREATE TABLE IF NOT EXISTS app.public_api_keys (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          name text NOT NULL,
          key_prefix text NOT NULL,
          key_sha256 text NOT NULL UNIQUE,
          scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
          rate_limit_per_minute integer NOT NULL DEFAULT 120 CHECK (rate_limit_per_minute BETWEEN 1 AND 10000),
          expires_at timestamptz,
          revoked_at timestamptz,
          last_used_at timestamptz,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.public_api_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.public_api_keys FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.public_api_keys
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.public_api_keys TO {role};

        -- Controlled hash-exact lookup for Bearer lp_pat_... authentication prior to tenant scope
        CREATE OR REPLACE FUNCTION app.lookup_public_api_key(p_key_sha256 text)
        RETURNS TABLE (
          key_id uuid,
          business_id uuid,
          created_by uuid,
          scopes jsonb,
          rate_limit_per_minute integer,
          expires_at timestamptz,
          revoked_at timestamptz
        )
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          SELECT k.id, k.business_id, k.created_by, k.scopes,
                 k.rate_limit_per_minute, k.expires_at, k.revoked_at
          FROM app.public_api_keys k
          WHERE k.key_sha256 = p_key_sha256
          LIMIT 1;
        $$;
        REVOKE ALL ON FUNCTION app.lookup_public_api_key(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.lookup_public_api_key(text) TO {role};

        -- Inbound email address -> business_id lookup for signed webhook routing
        CREATE OR REPLACE FUNCTION app.resolve_inbound_email_business(p_slug text)
        RETURNS uuid
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          SELECT b.id
          FROM app.businesses b
          WHERE lower(b.id::text) LIKE lower(p_slug) || '%'
             OR regexp_replace(lower(b.name), '[^a-z0-9]+', '-', 'g') = lower(p_slug)
          ORDER BY b.created_at ASC
          LIMIT 1;
        $$;
        REVOKE ALL ON FUNCTION app.resolve_inbound_email_business(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.resolve_inbound_email_business(text) TO {role};

        CREATE TABLE IF NOT EXISTS app.webhook_endpoints (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          target_url text NOT NULL,
          description text NOT NULL DEFAULT '',
          signing_secret_encrypted text NOT NULL,
          events jsonb NOT NULL DEFAULT '[]'::jsonb,
          active boolean NOT NULL DEFAULT true,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.webhook_endpoints ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.webhook_endpoints FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.webhook_endpoints
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.webhook_endpoints TO {role};

        CREATE TABLE IF NOT EXISTS app.webhook_deliveries (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          endpoint_id uuid NOT NULL REFERENCES app.webhook_endpoints(id) ON DELETE CASCADE,
          event_type text NOT NULL,
          payload_json jsonb NOT NULL,
          signature_header text NOT NULL,
          status text NOT NULL CHECK (status IN ('delivered', 'queued_retry', 'failed', 'simulated_local')),
          http_status integer,
          attempts integer NOT NULL DEFAULT 1,
          next_retry_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.webhook_deliveries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.webhook_deliveries FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.webhook_deliveries
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.webhook_deliveries TO {role};

        -- F3 & H2: Real AI per-page cost & caching ledger
        CREATE TABLE IF NOT EXISTS app.ai_page_cost_ledger (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          period_month text NOT NULL,
          content_sha256 text NOT NULL DEFAULT '',
          operation_kind text NOT NULL,
          pages integer NOT NULL DEFAULT 1 CHECK (pages >= 1),
          tier_used text NOT NULL,
          cache_hit boolean NOT NULL DEFAULT false,
          cost_usd numeric(14,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
          accuracy_score numeric(8,4) NOT NULL DEFAULT 0.9800,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ai_page_cost_ledger_month_idx
          ON app.ai_page_cost_ledger (business_id, period_month, created_at DESC);
        ALTER TABLE app.ai_page_cost_ledger ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_page_cost_ledger FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.ai_page_cost_ledger
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT ON app.ai_page_cost_ledger TO {role};

        -- G1: Fair-share live vs. bulk document ingestion queue
        CREATE TABLE IF NOT EXISTS app.ingestion_queue_jobs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          batch_id uuid,
          lane text NOT NULL CHECK (lane IN ('live', 'bulk')),
          fair_share_weight integer NOT NULL DEFAULT 100 CHECK (fair_share_weight BETWEEN 10 AND 1000),
          filename text NOT NULL,
          page_count integer NOT NULL DEFAULT 1 CHECK (page_count >= 1),
          state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'leased', 'completed', 'failed')),
          worker_id text,
          lease_until timestamptz,
          processing_ms numeric(12,2),
          error_message text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz
        );
        CREATE INDEX IF NOT EXISTS ingestion_queue_lane_state_idx
          ON app.ingestion_queue_jobs (business_id, lane, state, created_at ASC);
        ALTER TABLE app.ingestion_queue_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ingestion_queue_jobs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.ingestion_queue_jobs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.ingestion_queue_jobs TO {role};
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS app.ingestion_queue_jobs;
        DROP TABLE IF EXISTS app.ai_page_cost_ledger;
        DROP TABLE IF EXISTS app.webhook_deliveries;
        DROP TABLE IF EXISTS app.webhook_endpoints;
        DROP FUNCTION IF EXISTS app.resolve_inbound_email_business(text);
        DROP FUNCTION IF EXISTS app.lookup_public_api_key(text);
        DROP TABLE IF EXISTS app.public_api_keys;
        DROP TABLE IF EXISTS app.inbound_invoice_emails;
        DROP TABLE IF EXISTS app.connector_sync_runs;
        DROP TABLE IF EXISTS app.connector_accounts;
    """)
