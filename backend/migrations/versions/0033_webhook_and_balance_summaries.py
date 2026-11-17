"""Add webhook inbox payload, balance summaries table, and refresh function."""

from alembic import op
from sqlalchemy import text

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    runtime_attr = op.get_context().config.attributes.get("runtime_role")
    runtime = quote(runtime_attr) if runtime_attr else "CURRENT_USER"

    op.execute(
        text(f"""
        ALTER TABLE app.background_jobs ALTER COLUMN business_id DROP NOT NULL;
        ALTER TABLE app.background_jobs ALTER COLUMN created_by DROP NOT NULL;

        DROP POLICY IF EXISTS background_jobs_tenant ON app.background_jobs;
        CREATE POLICY background_jobs_tenant ON app.background_jobs
            USING (business_id IS NULL OR (business_id = app.scope() AND app.has_access(business_id)))
            WITH CHECK (business_id IS NULL OR (business_id = app.scope() AND app.has_access(business_id)));

        ALTER TABLE app.stripe_event_inbox ADD COLUMN IF NOT EXISTS payload jsonb;
        ALTER TABLE app.stripe_event_inbox ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'received';
        ALTER TABLE app.stripe_event_inbox ADD COLUMN IF NOT EXISTS processed_at timestamptz;
        ALTER TABLE app.stripe_event_inbox ADD COLUMN IF NOT EXISTS error_detail text;

        CREATE OR REPLACE FUNCTION app.stripe_claim_event(
            event_id text,
            kind text,
            event_payload jsonb
        ) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          WITH inserted AS (
            INSERT INTO app.stripe_event_inbox(id, event_type, payload, status)
              VALUES(event_id, kind, event_payload, 'received')
              ON CONFLICT(id) DO NOTHING RETURNING id
          ) SELECT EXISTS(SELECT 1 FROM inserted)
        $$;

        CREATE OR REPLACE FUNCTION app.stripe_claim_event(
            event_id text,
            kind text
        ) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT app.stripe_claim_event(event_id, kind, NULL::jsonb)
        $$;

        CREATE OR REPLACE FUNCTION app.stripe_record_event_result(
            event_id text,
            new_status text,
            err text DEFAULT NULL
        ) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          UPDATE app.stripe_event_inbox
          SET status = new_status,
              processed_at = now(),
              error_detail = err
          WHERE id = event_id;
        $$;

        CREATE TABLE IF NOT EXISTS app.balance_summaries (
            business_id uuid NOT NULL REFERENCES app.businesses(id) ON DELETE CASCADE,
            account_id uuid NOT NULL REFERENCES app.accounts(id) ON DELETE CASCADE,
            ledger_version integer NOT NULL,
            debit_total numeric(18, 4) NOT NULL DEFAULT 0,
            credit_total numeric(18, 4) NOT NULL DEFAULT 0,
            net_balance numeric(18, 4) NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_id, account_id)
        );

        CREATE INDEX IF NOT EXISTS balance_summaries_version ON app.balance_summaries (
            business_id, ledger_version
        );

        ALTER TABLE app.balance_summaries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.balance_summaries FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS balance_summaries_tenant ON app.balance_summaries;
        CREATE POLICY balance_summaries_tenant ON app.balance_summaries
            USING (business_id = app.scope() AND app.has_access(business_id))
            WITH CHECK (business_id = app.scope() AND app.has_access(business_id));

        CREATE OR REPLACE FUNCTION app.refresh_balance_summaries(
            p_business_id uuid,
            p_version integer
        ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            row_count integer;
        BEGIN
            INSERT INTO app.balance_summaries (
                business_id, account_id, ledger_version, debit_total, credit_total, net_balance, updated_at
            )
            SELECT
                j.business_id,
                jl.account_id,
                p_version,
                COALESCE(SUM(jl.debit), 0),
                COALESCE(SUM(jl.credit), 0),
                COALESCE(SUM(jl.debit - jl.credit), 0),
                now()
            FROM app.journal_lines jl
            JOIN app.journals j ON j.id = jl.journal_id AND j.business_id = jl.business_id
            WHERE j.business_id = p_business_id AND j.status = 'posted'
            GROUP BY j.business_id, jl.account_id
            ON CONFLICT (business_id, account_id) DO UPDATE SET
                ledger_version = EXCLUDED.ledger_version,
                debit_total = EXCLUDED.debit_total,
                credit_total = EXCLUDED.credit_total,
                net_balance = EXCLUDED.net_balance,
                updated_at = now();

            GET DIAGNOSTICS row_count = ROW_COUNT;
            RETURN row_count;
        END;
        $$;

        REVOKE ALL ON FUNCTION app.stripe_claim_event(text, text, jsonb),
            app.stripe_claim_event(text, text),
            app.stripe_record_event_result(text, text, text),
            app.refresh_balance_summaries(uuid, integer) FROM PUBLIC;

        GRANT EXECUTE ON FUNCTION app.stripe_claim_event(text, text, jsonb),
            app.stripe_claim_event(text, text),
            app.stripe_record_event_result(text, text, text),
            app.refresh_balance_summaries(uuid, integer) TO {runtime};

        GRANT SELECT, INSERT, UPDATE, DELETE ON app.stripe_event_inbox TO {runtime};
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.balance_summaries TO {runtime};
    """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DROP FUNCTION IF EXISTS app.refresh_balance_summaries(uuid, integer);
        DROP TABLE IF EXISTS app.balance_summaries;
        DROP FUNCTION IF EXISTS app.stripe_record_event_result(text, text, text);
        ALTER TABLE app.stripe_event_inbox DROP COLUMN IF EXISTS error_detail;
        ALTER TABLE app.stripe_event_inbox DROP COLUMN IF EXISTS processed_at;
        ALTER TABLE app.stripe_event_inbox DROP COLUMN IF EXISTS status;
        ALTER TABLE app.stripe_event_inbox DROP COLUMN IF EXISTS payload;
    """)
    )
