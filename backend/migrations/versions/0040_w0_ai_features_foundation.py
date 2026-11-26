"""W0 Foundation and AI-01 through AI-10: tenant-scoped AI runs, tool logs, and workflows."""

from alembic import op

revision = "0040"
down_revision = "0039"
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
        -- W0 & AI-10: Verifiable AI Evidence Record & Run Lifecycle
        CREATE TABLE app.ai_runs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          feature_id text NOT NULL CHECK (feature_id IN (
            'AI-01','AI-02','AI-03','AI-04','AI-05','AI-06','AI-07','AI-08','AI-09','AI-10'
          )),
          wave text NOT NULL DEFAULT 'W0' CHECK (wave IN ('W0','W1','W2','W3','W4','W5','W6')),
          provider text NOT NULL DEFAULT 'groq' CHECK (provider IN (
            'groq','deterministic_fallback'
          )),
          model_name text NOT NULL DEFAULT 'llama-3.3-70b-versatile',
          prompt_version text NOT NULL DEFAULT 'v1.0',
          tool_versions jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          status text NOT NULL DEFAULT 'completed' CHECK (status IN (
            'running','completed','abstained','canceled','failed'
          )),
          input_summary text NOT NULL,
          structured_output jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          citations jsonb NOT NULL DEFAULT '[]'::jsonb,
          tool_invocations jsonb NOT NULL DEFAULT '[]'::jsonb,
          confidence_score numeric(6,4) NOT NULL DEFAULT 1.0000 CHECK (
            confidence_score >= 0 AND confidence_score <= 1
          ),
          uncertainty_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
          snapshot_hash text NOT NULL,
          business_version integer NOT NULL DEFAULT 0,
          policy_version integer NOT NULL DEFAULT 1,
          prompt_tokens integer NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
          completion_tokens integer NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
          total_tokens integer NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
          latency_ms integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
          estimated_cost_usd numeric(18,6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
          approval_state text NOT NULL DEFAULT 'pending_review' CHECK (approval_state IN (
            'not_required','pending_review','approved','rejected','stale_invalidated','executed'
          )),
          approval_content_hash text NOT NULL,
          approved_by uuid REFERENCES app.users(id),
          approved_at timestamptz,
          rejection_reason text NOT NULL DEFAULT '',
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_runs_business_feature_idx ON app.ai_runs
          (business_id, feature_id, created_at DESC);
        ALTER TABLE app.ai_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_runs FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_runs_scope ON app.ai_runs
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_runs TO {role};

        -- W0: Read-only Tool Invocation Audit Log
        CREATE TABLE app.ai_tool_audit_logs (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id) ON DELETE CASCADE,
          tool_name text NOT NULL,
          parameters_hash text NOT NULL,
          row_count integer NOT NULL DEFAULT 0 CHECK (row_count >= 0),
          result_bytes integer NOT NULL DEFAULT 0 CHECK (result_bytes >= 0),
          untrusted_content_sanitized boolean NOT NULL DEFAULT true,
          duration_ms integer NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
          actor uuid NOT NULL REFERENCES app.users(id),
          executed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_tool_audit_logs_biz_idx ON app.ai_tool_audit_logs
          (business_id, run_id, executed_at DESC);
        ALTER TABLE app.ai_tool_audit_logs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_tool_audit_logs FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_tool_audit_logs_scope ON app.ai_tool_audit_logs
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT ON app.ai_tool_audit_logs TO {role};

        -- AI-04: Approved Business Accounting Memory
        CREATE TABLE app.ai_accounting_memories (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          memory_key text NOT NULL,
          title text NOT NULL,
          accounting_framework text NOT NULL DEFAULT 'US_GAAP' CHECK (accounting_framework IN (
            'US_GAAP','IFRS','TAX_BASIS'
          )),
          category text NOT NULL DEFAULT 'categorization' CHECK (category IN (
            'categorization','recognition','materiality','vendor_policy','close_rule'
          )),
          binding_kind text NOT NULL DEFAULT 'binding_policy' CHECK (binding_kind IN (
            'binding_policy','past_example'
          )),
          rule_condition text NOT NULL,
          account_code text NOT NULL,
          account_name text NOT NULL,
          materiality_threshold numeric(18,6) NOT NULL DEFAULT 0 CHECK (materiality_threshold >= 0),
          effective_from date NOT NULL,
          effective_to date,
          version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
          status text NOT NULL DEFAULT 'pending_review' CHECK (status IN (
            'pending_review','approved','superseded','revoked'
          )),
          evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_by uuid NOT NULL REFERENCES app.users(id),
          reviewed_by uuid REFERENCES app.users(id),
          approved_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK (effective_to IS NULL OR effective_to >= effective_from),
          UNIQUE (business_id, memory_key, version)
        );
        CREATE INDEX ai_accounting_memories_lookup_idx ON app.ai_accounting_memories
          (business_id, accounting_framework, status, effective_from DESC);
        ALTER TABLE app.ai_accounting_memories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_accounting_memories FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_accounting_memories_scope ON app.ai_accounting_memories
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_accounting_memories TO {role};

        -- AI-01: Reconciliation Detective Investigations
        CREATE TABLE app.ai_reconciliation_investigations (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id),
          bank_row_id uuid,
          statement_description text NOT NULL,
          statement_amount numeric(18,6) NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          classification text NOT NULL CHECK (classification IN (
            'exact_match','duplicate_import','repeated_legitimate_charge',
            'partial_settlement','bundled_payment','fee_deducted',
            'fx_timing_difference','ambiguous_needs_evidence'
          )),
          candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
          deterministic_delta numeric(18,6) NOT NULL DEFAULT 0,
          fee_amount numeric(18,6) NOT NULL DEFAULT 0,
          proposed_allocation jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          explanation text NOT NULL,
          status text NOT NULL DEFAULT 'proposed' CHECK (status IN (
            'open','proposed','approved','rejected','abstained'
          )),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_recon_investigations_biz_idx ON app.ai_reconciliation_investigations
          (business_id, created_at DESC);
        ALTER TABLE app.ai_reconciliation_investigations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_reconciliation_investigations FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_reconciliation_investigations_scope ON app.ai_reconciliation_investigations
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_reconciliation_investigations TO {role};

        -- AI-03: Closed-Loop Client Clarification
        CREATE TABLE app.ai_client_clarifications (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id),
          exception_kind text NOT NULL CHECK (exception_kind IN (
            'bank_row','missing_invoice','unmatched_payment','contract_variance'
          )),
          exception_ref text NOT NULL,
          recipient_key text NOT NULL,
          channel text NOT NULL DEFAULT 'email' CHECK (channel IN ('email','whatsapp','slack')),
          questions jsonb NOT NULL DEFAULT '[]'::jsonb,
          required_documents text[] NOT NULL DEFAULT ARRAY[]::text[],
          client_reply_text text NOT NULL DEFAULT '',
          uploaded_evidence_ref text NOT NULL DEFAULT '',
          completeness_score numeric(6,4) NOT NULL DEFAULT 0 CHECK (
            completeness_score >= 0 AND completeness_score <= 1
          ),
          remaining_gaps text[] NOT NULL DEFAULT ARRAY[]::text[],
          status text NOT NULL DEFAULT 'sent_sandbox' CHECK (status IN (
            'draft','sent_sandbox','partially_answered','resolved_closed','opted_out','escalated_dispute'
          )),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_client_clarifications_biz_idx ON app.ai_client_clarifications
          (business_id, created_at DESC);
        ALTER TABLE app.ai_client_clarifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_client_clarifications FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_client_clarifications_scope ON app.ai_client_clarifications
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_client_clarifications TO {role};

        -- AI-06: Multilingual Voice Accounting Sessions
        CREATE TABLE app.ai_voice_accounting_sessions (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id),
          language_locale text NOT NULL DEFAULT 'en_US' CHECK (language_locale IN (
            'en_US','ur_PK','en_ur_mixed'
          )),
          audio_format text NOT NULL DEFAULT 'audio/webm',
          duration_seconds integer NOT NULL DEFAULT 6 CHECK (duration_seconds BETWEEN 1 AND 300),
          stt_provider text NOT NULL DEFAULT 'groq_whisper_large_v3_turbo' CHECK (stt_provider IN (
            'groq_whisper_large_v3_turbo','browser_web_speech','deterministic_fixture'
          )),
          raw_transcript text NOT NULL,
          corrected_transcript text NOT NULL,
          extracted_intent text NOT NULL,
          extracted_entities jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          uncertain_fields text[] NOT NULL DEFAULT ARRAY[]::text[],
          critical_fields_confirmed boolean NOT NULL DEFAULT false,
          confirmed_by uuid REFERENCES app.users(id),
          linked_draft_id uuid,
          tts_readback_text text NOT NULL,
          livekit_room_name text NOT NULL DEFAULT '',
          status text NOT NULL DEFAULT 'transcribed_needs_confirmation' CHECK (status IN (
            'transcribed_needs_confirmation','confirmed_draft_created','abstained_noisy'
          )),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_voice_sessions_biz_idx ON app.ai_voice_accounting_sessions
          (business_id, created_at DESC);
        ALTER TABLE app.ai_voice_accounting_sessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_voice_accounting_sessions FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_voice_accounting_sessions_scope ON app.ai_voice_accounting_sessions
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_voice_accounting_sessions TO {role};

        -- AI-07: Contract / Invoice / Payment Verification
        CREATE TABLE app.ai_contract_verifications (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id),
          contract_reference text NOT NULL,
          vendor_or_customer text NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          contracted_amount numeric(18,6) NOT NULL CHECK (contracted_amount >= 0),
          invoiced_amount numeric(18,6) NOT NULL CHECK (invoiced_amount >= 0),
          paid_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK (paid_amount >= 0),
          discrepancy_amount numeric(18,6) NOT NULL DEFAULT 0,
          discrepancy_kind text NOT NULL CHECK (discrepancy_kind IN (
            'aligned','rate_overcharge','unapproved_milestone',
            'duplicate_payment','missing_credit','fx_rate_drift'
          )),
          extracted_clauses jsonb NOT NULL DEFAULT '[]'::jsonb,
          proposed_action text NOT NULL,
          status text NOT NULL DEFAULT 'discrepancy_flagged' CHECK (status IN (
            'verified_aligned','discrepancy_flagged','schedule_proposed','resolved'
          )),
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_contract_verifications_biz_idx ON app.ai_contract_verifications
          (business_id, created_at DESC);
        ALTER TABLE app.ai_contract_verifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_contract_verifications FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_contract_verifications_scope ON app.ai_contract_verifications
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_contract_verifications TO {role};

        -- AI-08: Action-Based Cash-Flow Scenarios
        CREATE TABLE app.ai_cash_scenarios (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid NOT NULL REFERENCES app.ai_runs(id),
          scenario_name text NOT NULL,
          natural_language_prompt text NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          horizon_weeks integer NOT NULL DEFAULT 13 CHECK (horizon_weeks BETWEEN 4 AND 52),
          parameters jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          baseline_ending_cash numeric(18,6) NOT NULL DEFAULT 0,
          scenario_ending_cash numeric(18,6) NOT NULL DEFAULT 0,
          net_cash_delta numeric(18,6) NOT NULL DEFAULT 0,
          lowest_weekly_cash numeric(18,6) NOT NULL DEFAULT 0,
          shortfall_weeks integer NOT NULL DEFAULT 0 CHECK (shortfall_weeks >= 0),
          weekly_series jsonb NOT NULL DEFAULT '[]'::jsonb,
          actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_cash_scenarios_biz_idx ON app.ai_cash_scenarios
          (business_id, created_at DESC);
        ALTER TABLE app.ai_cash_scenarios ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_cash_scenarios FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_cash_scenarios_scope ON app.ai_cash_scenarios
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_cash_scenarios TO {role};

        -- W0 Item 7: Per-Business AI Feature Flag, Pause & Monthly Budget Controls
        CREATE TABLE app.ai_tenant_controls (
          business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
          ai_enabled boolean NOT NULL DEFAULT true,
          operator_paused boolean NOT NULL DEFAULT false,
          pause_reason text NOT NULL DEFAULT '',
          monthly_budget_usd numeric(18,6) NOT NULL DEFAULT 25.000000
            CHECK (monthly_budget_usd >= 0),
          monthly_token_budget integer NOT NULL DEFAULT 500000
            CHECK (monthly_token_budget >= 0),
          max_concurrent_runs integer NOT NULL DEFAULT 4
            CHECK (max_concurrent_runs BETWEEN 1 AND 32),
          updated_by uuid REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE app.ai_tenant_controls ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_tenant_controls FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_tenant_controls_scope ON app.ai_tenant_controls
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_tenant_controls TO {role};

        -- W0 Item 5 & 8: Dataset & Evaluation Registry
        CREATE TABLE app.ai_evaluation_datasets (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          run_id uuid REFERENCES app.ai_runs(id),
          dataset_key text NOT NULL,
          dataset_cohort text NOT NULL CHECK (dataset_cohort IN (
            'synthetic_development','representative_deidentified'
          )),
          execution_provenance text NOT NULL CHECK (execution_provenance IN (
            'supplied_prediction','actual_groq_model_execution','deterministic_engine_execution'
          )),
          feature_id text NOT NULL CHECK (feature_id IN (
            'AI-01','AI-02','AI-03','AI-04','AI-05','AI-06','AI-07','AI-08','AI-09','AI-10'
          )),
          sample_count integer NOT NULL DEFAULT 1 CHECK (sample_count >= 1),
          precision_score numeric(6,4) NOT NULL DEFAULT 1.0000 CHECK (
            precision_score >= 0 AND precision_score <= 1
          ),
          recall_score numeric(6,4) NOT NULL DEFAULT 1.0000 CHECK (
            recall_score >= 0 AND recall_score <= 1
          ),
          f1_score numeric(6,4) NOT NULL DEFAULT 1.0000 CHECK (
            f1_score >= 0 AND f1_score <= 1
          ),
          citation_validity_rate numeric(6,4) NOT NULL DEFAULT 1.0000 CHECK (
            citation_validity_rate >= 0 AND citation_validity_rate <= 1
          ),
          promotion_gate_passed boolean NOT NULL DEFAULT true,
          notes text NOT NULL DEFAULT '',
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ai_evaluation_datasets_biz_idx ON app.ai_evaluation_datasets
          (business_id, created_at DESC);
        ALTER TABLE app.ai_evaluation_datasets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.ai_evaluation_datasets FORCE ROW LEVEL SECURITY;
        CREATE POLICY ai_evaluation_datasets_scope ON app.ai_evaluation_datasets
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.ai_evaluation_datasets TO {role};
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS app.ai_evaluation_datasets;
        DROP TABLE IF EXISTS app.ai_tenant_controls;
        DROP TABLE IF EXISTS app.ai_cash_scenarios;
        DROP TABLE IF EXISTS app.ai_contract_verifications;
        DROP TABLE IF EXISTS app.ai_voice_accounting_sessions;
        DROP TABLE IF EXISTS app.ai_client_clarifications;
        DROP TABLE IF EXISTS app.ai_reconciliation_investigations;
        DROP TABLE IF EXISTS app.ai_accounting_memories;
        DROP TABLE IF EXISTS app.ai_tool_audit_logs;
        DROP TABLE IF EXISTS app.ai_runs;
    """)
