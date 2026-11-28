"""Next-Gen v2.0 Phase I & J schema: scheduler, auto-post policies, Section 4 DDL tables, and merchants."""

from alembic import op

revision = "0046"
down_revision = "0045"
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
        -- I1: Scheduled job runner & immutable run log
        CREATE TABLE IF NOT EXISTS app.scheduled_jobs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          job_kind text NOT NULL CHECK (
            job_kind IN (
              'amortization_monthly',
              'accrual_month_end_scan',
              'accrual_day1_reversal',
              'flux_day1_analysis',
              'document_chase_24h'
            )
          ),
          cron_expr text NOT NULL,
          timezone text NOT NULL DEFAULT 'UTC',
          next_run_at timestamptz NOT NULL DEFAULT now(),
          last_run_at timestamptz,
          lease_token text NOT NULL DEFAULT '',
          lease_expires_at timestamptz,
          enabled boolean NOT NULL DEFAULT true,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, job_kind)
        );
        ALTER TABLE app.scheduled_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.scheduled_jobs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.scheduled_jobs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.scheduled_jobs TO {role};

        CREATE TABLE IF NOT EXISTS app.scheduled_job_runs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          job_id uuid NOT NULL REFERENCES app.scheduled_jobs(id),
          job_kind text NOT NULL,
          period_key text NOT NULL,
          idempotency_key text NOT NULL,
          status text NOT NULL CHECK (status IN ('completed', 'skipped', 'failed')),
          items_processed integer NOT NULL DEFAULT 0,
          summary_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          error_code text NOT NULL DEFAULT '',
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, idempotency_key)
        );
        ALTER TABLE app.scheduled_job_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.scheduled_job_runs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.scheduled_job_runs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT ON app.scheduled_job_runs TO {role};

        -- I2: Per-workspace auto-post policy (owner + MFA gated, off by default)
        CREATE TABLE IF NOT EXISTS app.auto_post_policies (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          job_kind text NOT NULL CHECK (
            job_kind IN (
              'amortization_monthly',
              'accrual_day1_reversal',
              'receipt_auto_match',
              'intercompany_mirror'
            )
          ),
          enabled boolean NOT NULL DEFAULT false,
          max_amount numeric(24,6) NOT NULL DEFAULT 5000.000000 CHECK (max_amount >= 0),
          enabled_by uuid REFERENCES app.users(id),
          enabled_at timestamptz,
          mfa_verified boolean NOT NULL DEFAULT false,
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, job_kind)
        );
        ALTER TABLE app.auto_post_policies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.auto_post_policies FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.auto_post_policies
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.auto_post_policies TO {role};

        -- I3 / Section 4 DDL: amortization_schedules (NUMERIC per D2)
        CREATE TABLE IF NOT EXISTS app.amortization_schedules (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          source_document_id uuid,
          schedule_kind text NOT NULL DEFAULT 'prepaid_expense'
            CHECK (schedule_kind IN ('prepaid_expense', 'deferred_revenue')),
          description text NOT NULL DEFAULT '',
          total_amount numeric(24,6) NOT NULL CHECK (total_amount > 0),
          currency text NOT NULL DEFAULT 'USD',
          start_date date NOT NULL,
          end_date date NOT NULL,
          periods_total integer NOT NULL CHECK (periods_total >= 1 AND periods_total <= 120),
          periods_posted integer NOT NULL DEFAULT 0 CHECK (periods_posted >= 0),
          debit_account_id uuid REFERENCES app.accounts(id),
          credit_account_id uuid REFERENCES app.accounts(id),
          status text NOT NULL DEFAULT 'ACTIVE'
            CHECK (status IN ('ACTIVE', 'COMPLETED', 'CANCELLED')),
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (end_date >= start_date)
        );
        ALTER TABLE app.amortization_schedules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.amortization_schedules FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.amortization_schedules
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.amortization_schedules TO {role};

        -- I3 / Section 4 DDL: document_chase_events (Email-first cascade + signed upload token)
        CREATE TABLE IF NOT EXISTS app.document_chase_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          bank_transaction_id uuid NOT NULL REFERENCES app.bank_rows(id),
          channel_used text NOT NULL DEFAULT 'EMAIL'
            CHECK (channel_used IN ('EMAIL', 'WHATSAPP', 'SLACK', 'SMS')),
          attempt_number integer NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
          status text NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'SENT', 'RESPONDED', 'EXPIRED')),
          recipient_contact text NOT NULL DEFAULT '',
          upload_token_sha256 text NOT NULL DEFAULT '',
          upload_token_expires_at timestamptz,
          chase_metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          responded_at timestamptz
        );
        CREATE INDEX IF NOT EXISTS idx_document_chase_events_biz_txn
          ON app.document_chase_events (business_id, bank_transaction_id, attempt_number DESC);
        ALTER TABLE app.document_chase_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.document_chase_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.document_chase_events
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.document_chase_events TO {role};

        -- I3 / Section 4 DDL: vendor_fraud_sentinel_logs
        CREATE TABLE IF NOT EXISTS app.vendor_fraud_sentinel_logs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          vendor_id uuid REFERENCES app.parties(id),
          vendor_name text NOT NULL DEFAULT '',
          tax_id_value text NOT NULL DEFAULT '',
          tin_verification_status text NOT NULL DEFAULT 'UNCHECKED'
            CHECK (tin_verification_status IN ('VALID', 'INVALID', 'UNCHECKED', 'UNAVAILABLE')),
          bank_routing_hash text NOT NULL DEFAULT '',
          risk_score integer NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
          frozen boolean NOT NULL DEFAULT false,
          flag_reason text NOT NULL DEFAULT '',
          signals jsonb NOT NULL DEFAULT '[]'::jsonb,
          verified_by_user_id uuid REFERENCES app.users(id),
          verified_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_vendor_fraud_logs_biz
          ON app.vendor_fraud_sentinel_logs (business_id, frozen, created_at DESC);
        ALTER TABLE app.vendor_fraud_sentinel_logs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.vendor_fraud_sentinel_logs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.vendor_fraud_sentinel_logs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.vendor_fraud_sentinel_logs TO {role};

        -- J1 / Module 8: LLM Merchant normalization & alias cache
        CREATE TABLE IF NOT EXISTS app.merchant_profiles (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          normalized_name text NOT NULL,
          business_category text NOT NULL DEFAULT 'General Expense',
          suggested_account_id uuid REFERENCES app.accounts(id),
          logo_url text NOT NULL DEFAULT '',
          source text NOT NULL DEFAULT 'deterministic'
            CHECK (source IN ('deterministic', 'llm', 'user')),
          confidence numeric(5,4) NOT NULL DEFAULT 0.8500,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, normalized_name)
        );
        ALTER TABLE app.merchant_profiles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.merchant_profiles FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.merchant_profiles
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.merchant_profiles TO {role};

        CREATE TABLE IF NOT EXISTS app.merchant_aliases (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          raw_pattern text NOT NULL,
          merchant_profile_id uuid NOT NULL REFERENCES app.merchant_profiles(id) ON DELETE CASCADE,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, raw_pattern)
        );
        ALTER TABLE app.merchant_aliases ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.merchant_aliases FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.merchant_aliases
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.merchant_aliases TO {role};
    """)


def downgrade() -> None:
    raise RuntimeError("Financial history schema migrations do not support destructive downgrade.")
