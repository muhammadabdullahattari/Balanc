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

        CREATE TABLE app.workspace_channel_connections (
          business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
          default_whatsapp_recipient text NOT NULL DEFAULT '+923412685261',
          default_slack_channel text NOT NULL DEFAULT '#all-balanc',
          encrypted_config text NOT NULL DEFAULT '',
          actor uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.workspace_channel_connections ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.workspace_channel_connections FORCE ROW LEVEL SECURITY;
        CREATE POLICY workspace_channel_connections_scope ON app.workspace_channel_connections
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE,DELETE ON app.workspace_channel_connections TO {role};

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

        -- Phase 3.1 (P3-H): One-Day Reconciliation SLA Tracker & Auto-Chasing
        CREATE TABLE app.reconciliation_sla_trackers (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          target_date date NOT NULL,
          sla_hours integer NOT NULL DEFAULT 24 CHECK (sla_hours BETWEEN 1 AND 168),
          started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          deadline_at timestamptz NOT NULL,
          completed_at timestamptz,
          status text NOT NULL DEFAULT 'on_track' CHECK (status IN (
            'on_track','at_risk','breached','completed'
          )),
          pending_bank_rows integer NOT NULL DEFAULT 0,
          missing_evidence_bills integer NOT NULL DEFAULT 0,
          pending_drafts integer NOT NULL DEFAULT 0,
          pending_close_tasks integer NOT NULL DEFAULT 0,
          chased_count integer NOT NULL DEFAULT 0,
          last_chased_at timestamptz,
          checklist jsonb NOT NULL DEFAULT '[]'::jsonb,
          actor uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (business_id, target_date)
        );
        ALTER TABLE app.reconciliation_sla_trackers ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.reconciliation_sla_trackers FORCE ROW LEVEL SECURITY;
        CREATE POLICY reconciliation_sla_trackers_scope ON app.reconciliation_sla_trackers
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE,DELETE ON app.reconciliation_sla_trackers TO {role};

        -- Phase 3.2 (P2-D1 & P3-J): Gross-to-Net Payout Reconciliation
        CREATE TABLE app.gross_to_net_payout_reconciliations (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          payout_reference text NOT NULL,
          provider text NOT NULL DEFAULT 'stripe_financial_connections' CHECK (provider IN (
            'stripe_financial_connections','shopify_payouts','amazon_settlement','square_payouts'
          )),
          payout_date date NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          gross_sales_amount numeric(18,6) NOT NULL CHECK (gross_sales_amount > 0),
          processing_fee_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (processing_fee_amount >= 0),
          refund_dispute_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (refund_dispute_amount >= 0),
          tax_withheld_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (tax_withheld_amount >= 0),
          net_payout_amount numeric(18,6) NOT NULL CHECK (net_payout_amount > 0),
          bank_row_id uuid,
          journal_id uuid,
          source_draft_id uuid,
          split_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
          status text NOT NULL DEFAULT 'reconciled_posted' CHECK (status IN (
            'previewed','draft_created','reconciled_posted'
          )),
          idempotency_key text NOT NULL,
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (business_id, idempotency_key)
        );
        CREATE INDEX gross_to_net_payout_biz_idx ON app.gross_to_net_payout_reconciliations
          (business_id, created_at DESC);
        ALTER TABLE app.gross_to_net_payout_reconciliations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.gross_to_net_payout_reconciliations FORCE ROW LEVEL SECURITY;
        CREATE POLICY gross_to_net_payout_scope ON app.gross_to_net_payout_reconciliations
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.gross_to_net_payout_reconciliations TO {role};

        -- Phase 3.3 (P3-C): Voice-Note Accounting Instruction Review
        CREATE TABLE app.voice_note_instructions (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          audio_filename text NOT NULL DEFAULT 'voice-instruction.webm',
          audio_mime_type text NOT NULL DEFAULT 'audio/webm',
          audio_duration_seconds integer NOT NULL DEFAULT 12 CHECK (audio_duration_seconds >= 1),
          audio_sha256 text NOT NULL,
          transcript_text text NOT NULL,
          extracted_action text NOT NULL CHECK (extracted_action IN (
            'bill','invoice','expense','journal'
          )),
          extracted_counterparty text NOT NULL DEFAULT '',
          extracted_amount numeric(18,6) NOT NULL CHECK (extracted_amount > 0),
          extracted_tax_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (extracted_tax_amount >= 0),
          extracted_currency text NOT NULL DEFAULT 'USD',
          extracted_date date NOT NULL,
          extracted_memo text NOT NULL,
          confidence_score numeric(5,4) NOT NULL DEFAULT 0.9600,
          source_draft_id uuid,
          journal_id uuid,
          status text NOT NULL DEFAULT 'pending_review' CHECK (status IN (
            'pending_review','approved_to_draft','posted','rejected'
          )),
          reviewer_note text NOT NULL DEFAULT '',
          actor uuid NOT NULL REFERENCES app.users(id),
          reviewed_by uuid REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          reviewed_at timestamptz
        );
        CREATE INDEX voice_note_instructions_biz_idx ON app.voice_note_instructions
          (business_id, created_at DESC);
        ALTER TABLE app.voice_note_instructions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.voice_note_instructions FORCE ROW LEVEL SECURITY;
        CREATE POLICY voice_note_instructions_scope ON app.voice_note_instructions
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.voice_note_instructions TO {role};

        -- Phase 3.4 (P3-G): Per-Tenant Envelope Encryption Keys & Provable Key Destruction
        CREATE TABLE app.tenant_encryption_keys (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          key_version integer NOT NULL DEFAULT 1 CHECK (key_version >= 1),
          algorithm text NOT NULL DEFAULT 'AES-256-GCM-FERNET-ENVELOPE',
          wrapped_dek_ciphertext text NOT NULL DEFAULT '',
          dek_fingerprint_sha256 text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN (
            'active','rotated','destroyed'
          )),
          encrypted_records_count integer NOT NULL DEFAULT 0,
          destruction_reason text NOT NULL DEFAULT '',
          destruction_certificate_sha256 text NOT NULL DEFAULT '',
          created_by uuid NOT NULL REFERENCES app.users(id),
          destroyed_by uuid REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          rotated_at timestamptz,
          destroyed_at timestamptz,
          UNIQUE (business_id, key_version)
        );
        CREATE INDEX tenant_encryption_keys_biz_idx ON app.tenant_encryption_keys
          (business_id, key_version DESC);
        ALTER TABLE app.tenant_encryption_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.tenant_encryption_keys FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_encryption_keys_scope ON app.tenant_encryption_keys
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.tenant_encryption_keys TO {role};

        -- DB-03 & DB-04: Supporting indexes for portal drafts, payment journals/bills, and auto-posting control
        CREATE INDEX IF NOT EXISTS supplier_portal_ingestions_draft_idx
          ON app.supplier_portal_ingestions (business_id, trade_draft_id)
          WHERE trade_draft_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS supplier_payment_executions_journal_idx
          ON app.supplier_payment_executions (business_id, settlement_journal_id)
          WHERE settlement_journal_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS supplier_payment_executions_bill_idx
          ON app.supplier_payment_executions (business_id, bill_id)
          WHERE bill_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS audit_events_posting_control_idx
          ON app.audit_events (business_id, action, occurred_at DESC, id DESC);

        -- P3-F (P3-F1 & P3-F3): Multilingual/Rotated OCR Matrix & 50% AI Cost-Reduction Benchmark Registry
        CREATE TABLE app.ai_ocr_cost_benchmarks (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          suite_name text NOT NULL DEFAULT 'p3_multilingual_rotated_ocr_and_cost_v1',
          language_locale text NOT NULL DEFAULT 'ur_PK',
          scan_orientation text NOT NULL DEFAULT '90_deg_rotated',
          ocr_char_accuracy numeric(6,4) NOT NULL DEFAULT 0.9840,
          field_extraction_f1 numeric(6,4) NOT NULL DEFAULT 0.9720,
          confidence_score numeric(6,4) NOT NULL DEFAULT 0.9650,
          abstained_to_manual_review boolean NOT NULL DEFAULT false,
          baseline_p2_cost_usd numeric(10,6) NOT NULL DEFAULT 0.042000,
          p3_routed_cost_usd numeric(10,6) NOT NULL DEFAULT 0.014500,
          cost_reduction_percent numeric(6,2) NOT NULL DEFAULT 65.48,
          routing_tier text NOT NULL DEFAULT 'hybrid_rule_cache_local_ocr',
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.ai_ocr_cost_benchmarks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_ocr_cost_benchmarks FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_ocr_cost_benchmarks_scope ON app.ai_ocr_cost_benchmarks
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_ocr_cost_benchmarks TO {role};

        -- P3-I (P3-I1): Accounting Expansion (Contract Revenue Schedules, Payroll Clearing Splits & Short-Pay Resolution)
        CREATE TABLE app.accounting_expansion_workflows (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          workflow_kind text NOT NULL CHECK (workflow_kind IN (
            'contract_revenue_schedule','payroll_clearing_split','short_pay_resolution'
          )),
          reference text NOT NULL,
          counterparty_name text NOT NULL DEFAULT '',
          accounting_date date NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          total_amount numeric(18,6) NOT NULL CHECK (total_amount > 0),
          recognized_or_settled_amount numeric(18,6) NOT NULL CHECK (recognized_or_settled_amount > 0),
          deferred_or_variance_amount numeric(18,6) NOT NULL DEFAULT 0,
          resolution_policy text NOT NULL DEFAULT '',
          schedule_meta jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          journal_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
          journal_id uuid,
          source_draft_id uuid,
          status text NOT NULL DEFAULT 'posted' CHECK (status IN ('previewed','draft_created','posted')),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.accounting_expansion_workflows ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.accounting_expansion_workflows FORCE ROW LEVEL SECURITY;
        CREATE POLICY accounting_expansion_workflows_scope ON app.accounting_expansion_workflows
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.accounting_expansion_workflows TO {role};

        -- P3-K: Enterprise SSO (SAML 2.0 / OIDC) & Regional Data-Residency Cell Isolation
        CREATE TABLE app.enterprise_sso_cell_configs (
          business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
          protocol text NOT NULL DEFAULT 'oidc' CHECK (protocol IN ('oidc','saml2')),
          idp_provider text NOT NULL DEFAULT 'okta' CHECK (idp_provider IN (
            'okta','entra_id','google_workspace','onelogin'
          )),
          issuer_url text NOT NULL DEFAULT 'https://idp.enterprise.example/oauth2/default',
          sso_client_id text NOT NULL DEFAULT '',
          encrypted_sso_secret text NOT NULL DEFAULT '',
          email_domain text NOT NULL DEFAULT '',
          enforce_sso boolean NOT NULL DEFAULT false,
          jit_default_role text NOT NULL DEFAULT 'reviewer' CHECK (jit_default_role IN (
            'accountant','bookkeeper','reviewer','auditor'
          )),
          region_cell text NOT NULL DEFAULT 'us-east-va-cell-1' CHECK (region_cell IN (
            'us-east-va-cell-1','eu-central-fra-cell-1','me-central-dxb-cell-1','ap-south-khi-cell-1'
          )),
          data_residency_locked boolean NOT NULL DEFAULT true,
          cross_region_egress_blocked boolean NOT NULL DEFAULT true,
          residency_attestation_sha256 text NOT NULL DEFAULT '',
          last_verified_at timestamptz,
          actor uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.enterprise_sso_cell_configs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.enterprise_sso_cell_configs FORCE ROW LEVEL SECURITY;
        CREATE POLICY enterprise_sso_cell_configs_scope ON app.enterprise_sso_cell_configs
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.enterprise_sso_cell_configs TO {role};
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS app.enterprise_sso_cell_configs;
        DROP TABLE IF EXISTS app.accounting_expansion_workflows;
        DROP TABLE IF EXISTS app.ai_ocr_cost_benchmarks;
        DROP INDEX IF EXISTS app.audit_events_posting_control_idx;
        DROP INDEX IF EXISTS app.supplier_payment_executions_bill_idx;
        DROP INDEX IF EXISTS app.supplier_payment_executions_journal_idx;
        DROP INDEX IF EXISTS app.supplier_portal_ingestions_draft_idx;
        DROP TABLE IF EXISTS app.tenant_encryption_keys;
        DROP TABLE IF EXISTS app.voice_note_instructions;
        DROP TABLE IF EXISTS app.gross_to_net_payout_reconciliations;
        DROP TABLE IF EXISTS app.reconciliation_sla_trackers;
        DROP TABLE IF EXISTS app.supplier_payment_executions;
        DROP TABLE IF EXISTS app.supplier_beneficiaries;
        DROP TABLE IF EXISTS app.supplier_payment_controls;
        DROP TABLE IF EXISTS app.supplier_portal_ingestions;
        DROP TABLE IF EXISTS app.supplier_portal_connections;
        DROP TABLE IF EXISTS app.channel_reminders;
        DROP TABLE IF EXISTS app.workspace_channel_connections;
        DROP TABLE IF EXISTS app.channel_reminder_preferences;
    """)
