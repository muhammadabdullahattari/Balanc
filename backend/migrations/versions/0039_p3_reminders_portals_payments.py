"""P3 Features C, D, and E: Multichannel reminders, supplier portal connectors, and human-approved supplier payments."""

from alembic import op

revision = "0039"
down_revision = "0038"
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
        -- P3 Feature C: Multichannel WhatsApp & Slack Reminders
        CREATE TABLE app.channel_reminder_preferences (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          recipient_key text NOT NULL,
          recipient_name text NOT NULL,
          whatsapp_phone text NOT NULL DEFAULT '',
          slack_channel text NOT NULL DEFAULT '#all-balanc',
          email_address text NOT NULL DEFAULT '',
          whatsapp_opt_in boolean NOT NULL DEFAULT true,
          slack_opt_in boolean NOT NULL DEFAULT true,
          email_fallback boolean NOT NULL DEFAULT true,
          channel_order text[] NOT NULL DEFAULT ARRAY['whatsapp','slack','email'],
          quiet_start_hour integer NOT NULL DEFAULT 22 CHECK (quiet_start_hour BETWEEN 0 AND 23),
          quiet_end_hour integer NOT NULL DEFAULT 7 CHECK (quiet_end_hour BETWEEN 0 AND 23),
          timezone text NOT NULL DEFAULT 'UTC',
          locale text NOT NULL DEFAULT 'en_US',
          max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
          opted_out boolean NOT NULL DEFAULT false,
          opted_out_at timestamptz,
          actor uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (business_id, recipient_key)
        );
        ALTER TABLE app.channel_reminder_preferences ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.channel_reminder_preferences FORCE ROW LEVEL SECURITY;
        CREATE POLICY channel_reminder_preferences_scope ON app.channel_reminder_preferences
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE,DELETE ON app.channel_reminder_preferences TO {role};

        CREATE TABLE app.channel_reminders (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          target_kind text NOT NULL CHECK (target_kind IN ('bill','invoice','evidence','approval')),
          target_id uuid NOT NULL,
          target_label text NOT NULL,
          recipient_key text NOT NULL,
          channel text NOT NULL CHECK (channel IN ('whatsapp','slack','email')),
          destination text NOT NULL,
          template_name text NOT NULL DEFAULT 'hello_world',
          message_body text NOT NULL,
          attempt_number integer NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
          status text NOT NULL CHECK (status IN (
            'sent','delivered','read',
            'skipped_resolved','skipped_opted_out','skipped_quiet_hours','skipped_cap_reached','failed'
          )),
          provider_message_id text NOT NULL DEFAULT '',
          provider_response jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          idempotency_key text NOT NULL,
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (business_id, idempotency_key)
        );
        CREATE INDEX channel_reminders_target_idx ON app.channel_reminders
          (business_id, target_kind, target_id, created_at DESC);
        ALTER TABLE app.channel_reminders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.channel_reminders FORCE ROW LEVEL SECURITY;
        CREATE POLICY channel_reminders_scope ON app.channel_reminders
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.channel_reminders TO {role};

        -- P3 Feature D: Authorized Supplier Portal Connectors (Method A API & Method B Web + 32-char TOTP)
        CREATE TABLE app.supplier_portal_connections (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          supplier_id uuid,
          vendor_name text NOT NULL,
          portal_kind text NOT NULL CHECK (portal_kind IN (
            'web_totp','api_stripe','utility_portal','telecom_portal','cloud_portal'
          )),
          portal_url text NOT NULL,
          login_username text NOT NULL,
          encrypted_password text NOT NULL,
          encrypted_totp_seed text NOT NULL DEFAULT '',
          totp_enabled boolean NOT NULL DEFAULT true,
          schedule_frequency text NOT NULL DEFAULT 'monthly' CHECK (schedule_frequency IN (
            'manual','daily','weekly','monthly'
          )),
          status text NOT NULL DEFAULT 'active' CHECK (status IN (
            'active','revoked','auth_failed','portal_changed','rate_limited'
          )),
          last_sync_at timestamptz,
          last_totp_verified_at timestamptz,
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX supplier_portal_connections_biz_idx ON app.supplier_portal_connections
          (business_id, created_at DESC);
        ALTER TABLE app.supplier_portal_connections ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.supplier_portal_connections FORCE ROW LEVEL SECURITY;
        CREATE POLICY supplier_portal_connections_scope ON app.supplier_portal_connections
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE,DELETE ON app.supplier_portal_connections TO {role};

        CREATE TABLE app.supplier_portal_ingestions (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          connection_id uuid NOT NULL REFERENCES app.supplier_portal_connections(id),
          external_invoice_id text NOT NULL,
          invoice_number text NOT NULL,
          vendor_name text NOT NULL,
          invoice_date date NOT NULL,
          due_date date NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          total_amount numeric(18,6) NOT NULL CHECK (total_amount > 0),
          tax_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
          content_sha256 text NOT NULL,
          evidence_file_id uuid,
          trade_draft_id uuid,
          totp_code_used_hash text NOT NULL DEFAULT '',
          ocr_confidence numeric(5,4) NOT NULL DEFAULT 0.9800,
          provenance jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          status text NOT NULL CHECK (status IN (
            'ingested_to_draft','duplicate_skipped','manual_review_required'
          )),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX supplier_portal_ingestions_lookup_idx ON app.supplier_portal_ingestions
          (business_id, connection_id, external_invoice_id, created_at DESC);
        ALTER TABLE app.supplier_portal_ingestions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.supplier_portal_ingestions FORCE ROW LEVEL SECURITY;
        CREATE POLICY supplier_portal_ingestions_scope ON app.supplier_portal_ingestions
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.supplier_portal_ingestions TO {role};

        -- P3 Feature E: Human-Approved Supplier Payment Execution
        CREATE TABLE app.supplier_payment_controls (
          business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
          kill_switch_active boolean NOT NULL DEFAULT false,
          kill_switch_reason text NOT NULL DEFAULT '',
          single_payment_limit numeric(18,6) NOT NULL DEFAULT 50000.000000 CHECK (single_payment_limit > 0),
          dual_approval_threshold numeric(18,6) NOT NULL DEFAULT 5000.000000 CHECK (dual_approval_threshold > 0),
          default_rail text NOT NULL DEFAULT 'stripe_live' CHECK (default_rail IN (
            'stripe_live','ach_nacha','swift_wire'
          )),
          updated_by uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.supplier_payment_controls ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.supplier_payment_controls FORCE ROW LEVEL SECURITY;
        CREATE POLICY supplier_payment_controls_scope ON app.supplier_payment_controls
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.supplier_payment_controls TO {role};

        CREATE TABLE app.supplier_beneficiaries (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          supplier_id uuid,
          supplier_name text NOT NULL,
          account_holder_name text NOT NULL,
          bank_name text NOT NULL,
          routing_or_swift text NOT NULL,
          account_number_masked text NOT NULL,
          account_fingerprint_sha256 text NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          rail text NOT NULL DEFAULT 'stripe_live' CHECK (rail IN (
            'stripe_live','ach_nacha','swift_wire'
          )),
          out_of_band_verified boolean NOT NULL DEFAULT false,
          verification_reference text NOT NULL DEFAULT '',
          verified_by uuid REFERENCES app.users(id),
          verified_at timestamptz,
          version integer NOT NULL DEFAULT 1,
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX supplier_beneficiaries_biz_idx ON app.supplier_beneficiaries
          (business_id, created_at DESC);
        ALTER TABLE app.supplier_beneficiaries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.supplier_beneficiaries FORCE ROW LEVEL SECURITY;
        CREATE POLICY supplier_beneficiaries_scope ON app.supplier_beneficiaries
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.supplier_beneficiaries TO {role};

        CREATE TABLE app.supplier_payment_executions (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          bill_id uuid,
          bill_reference text NOT NULL,
          supplier_name text NOT NULL,
          beneficiary_id uuid NOT NULL REFERENCES app.supplier_beneficiaries(id),
          beneficiary_version integer NOT NULL,
          beneficiary_fingerprint text NOT NULL,
          amount numeric(18,6) NOT NULL CHECK (amount > 0),
          currency text NOT NULL DEFAULT 'USD',
          rail text NOT NULL CHECK (rail IN ('stripe_live','ach_nacha','swift_wire')),
          idempotency_key text NOT NULL,
          state text NOT NULL CHECK (state IN (
            'prepared','first_approved','approved_ready','executed',
            'settled_posted','invalidated','rejected','failed'
          )),
          requires_dual_approval boolean NOT NULL DEFAULT false,
          prepared_by uuid NOT NULL REFERENCES app.users(id),
          first_approved_by uuid REFERENCES app.users(id),
          final_approved_by uuid REFERENCES app.users(id),
          step_up_verified boolean NOT NULL DEFAULT false,
          step_up_method text NOT NULL DEFAULT '',
          provider_reference text NOT NULL DEFAULT '',
          provider_payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          remittance_advice text NOT NULL DEFAULT '',
          settlement_journal_id uuid,
          failure_reason text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          executed_at timestamptz,
          UNIQUE (business_id, idempotency_key)
        );
        CREATE INDEX supplier_payment_executions_biz_idx ON app.supplier_payment_executions
          (business_id, created_at DESC);
        ALTER TABLE app.supplier_payment_executions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.supplier_payment_executions FORCE ROW LEVEL SECURITY;
        CREATE POLICY supplier_payment_executions_scope ON app.supplier_payment_executions
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.supplier_payment_executions TO {role};
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS app.supplier_payment_executions;
        DROP TABLE IF EXISTS app.supplier_beneficiaries;
        DROP TABLE IF EXISTS app.supplier_payment_controls;
        DROP TABLE IF EXISTS app.supplier_portal_ingestions;
        DROP TABLE IF EXISTS app.supplier_portal_connections;
        DROP TABLE IF EXISTS app.channel_reminders;
        DROP TABLE IF EXISTS app.channel_reminder_preferences;
    """)
