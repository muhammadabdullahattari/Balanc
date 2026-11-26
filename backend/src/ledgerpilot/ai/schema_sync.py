"""Idempotent startup schema guard for P3, P4, and W0 tables on pre-stamped deployments."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

_EXTENDED_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS app.ai_ocr_cost_benchmarks (
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

CREATE TABLE IF NOT EXISTS app.accounting_expansion_workflows (
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
  recognized_or_settled_amount numeric(18,6) NOT NULL
    CHECK (recognized_or_settled_amount > 0),
  deferred_or_variance_amount numeric(18,6) NOT NULL DEFAULT 0,
  resolution_policy text NOT NULL DEFAULT '',
  schedule_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  journal_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
  journal_id uuid,
  source_draft_id uuid,
  status text NOT NULL DEFAULT 'posted'
    CHECK (status IN ('previewed','draft_created','posted')),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.enterprise_sso_cell_configs (
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
    'us-east-va-cell-1','eu-central-fra-cell-1',
    'me-central-dxb-cell-1','ap-south-khi-cell-1'
  )),
  data_residency_locked boolean NOT NULL DEFAULT true,
  cross_region_egress_blocked boolean NOT NULL DEFAULT true,
  residency_attestation_sha256 text NOT NULL DEFAULT '',
  last_verified_at timestamptz,
  actor uuid NOT NULL REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_enterprise_tenant_quotas (
  business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
  service_tier text NOT NULL DEFAULT 'enterprise' CHECK (service_tier IN (
    'starter','growth','enterprise','sovereign'
  )),
  daily_page_quota integer NOT NULL DEFAULT 50000 CHECK (daily_page_quota >= 100),
  monthly_ai_budget_usd numeric(14,4) NOT NULL DEFAULT 500.0000
    CHECK (monthly_ai_budget_usd > 0),
  storage_quota_gb integer NOT NULL DEFAULT 250 CHECK (storage_quota_gb >= 1),
  max_concurrent_workers integer NOT NULL DEFAULT 16
    CHECK (max_concurrent_workers BETWEEN 1 AND 256),
  fair_share_weight integer NOT NULL DEFAULT 100
    CHECK (fair_share_weight BETWEEN 10 AND 1000),
  hard_quota_enforcement boolean NOT NULL DEFAULT true,
  alert_threshold_percent integer NOT NULL DEFAULT 80
    CHECK (alert_threshold_percent BETWEEN 50 AND 99),
  runbook_owner text NOT NULL DEFAULT 'sre-oncall@balanc.enterprise',
  incident_state text NOT NULL DEFAULT 'healthy' CHECK (incident_state IN (
    'healthy','elevated_load','noisy_neighbor_throttled','quota_exceeded'
  )),
  pages_ingested_today integer NOT NULL DEFAULT 0,
  ai_spend_mtd_usd numeric(14,4) NOT NULL DEFAULT 0,
  storage_used_mb numeric(14,2) NOT NULL DEFAULT 0,
  actor uuid NOT NULL REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_online_ledger_migrations (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  migration_name text NOT NULL,
  target_table text NOT NULL CHECK (target_table IN (
    'app.journal_lines','app.journals','app.trade_documents','app.bank_rows'
  )),
  pattern text NOT NULL DEFAULT 'expand_backfill_verify_contract' CHECK (pattern IN (
    'expand_backfill_verify_contract',
    'online_index_concurrently',
    'dual_read_write_backfill'
  )),
  phase text NOT NULL DEFAULT 'completed_verified' CHECK (phase IN (
    'expanded','backfilling','paused','verified_ready',
    'completed_verified','rolled_back'
  )),
  batch_size integer NOT NULL DEFAULT 1000 CHECK (batch_size BETWEEN 50 AND 50000),
  lock_timeout_ms integer NOT NULL DEFAULT 50
    CHECK (lock_timeout_ms BETWEEN 10 AND 5000),
  total_rows integer NOT NULL DEFAULT 0,
  processed_rows integer NOT NULL DEFAULT 0,
  last_cursor text NOT NULL DEFAULT '',
  pre_backfill_debit_total numeric(18,6) NOT NULL DEFAULT 0,
  pre_backfill_credit_total numeric(18,6) NOT NULL DEFAULT 0,
  post_backfill_debit_total numeric(18,6) NOT NULL DEFAULT 0,
  post_backfill_credit_total numeric(18,6) NOT NULL DEFAULT 0,
  trial_balance_tied_out boolean NOT NULL DEFAULT true,
  availability_percent numeric(6,3) NOT NULL DEFAULT 100.000,
  checksum_sha256 text NOT NULL DEFAULT '',
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_bulk_ingestion_batches (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  batch_reference text NOT NULL,
  worker_lane text NOT NULL DEFAULT 'bulk_background' CHECK (worker_lane IN (
    'live_priority','bulk_background'
  )),
  source_channel text NOT NULL DEFAULT 'archive_backfill_zip'
    CHECK (source_channel IN (
      'archive_backfill_zip','erp_historical_pdfs',
      'supplier_portal_bulk','multi_entity_box_import'
    )),
  total_pages integer NOT NULL CHECK (total_pages >= 1),
  processed_pages integer NOT NULL DEFAULT 0,
  failed_pages integer NOT NULL DEFAULT 0,
  tenant_concurrency_slots integer NOT NULL DEFAULT 8
    CHECK (tenant_concurrency_slots BETWEEN 1 AND 64),
  throughput_pages_per_sec numeric(10,2) NOT NULL DEFAULT 185.00,
  foreground_p95_latency_ms numeric(8,2) NOT NULL DEFAULT 42.50,
  foreground_p99_latency_ms numeric(8,2) NOT NULL DEFAULT 88.00,
  backpressure_status text NOT NULL DEFAULT 'normal'
    CHECK (backpressure_status IN (
      'normal','throttled_fair_share','backpressure_queued','cancelled'
    )),
  unit_cost_per_page_usd numeric(10,6) NOT NULL DEFAULT 0.011500,
  status text NOT NULL DEFAULT 'completed' CHECK (status IN (
    'queued','draining','completed','cancelled'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_regional_dr_drills (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  primary_cell text NOT NULL CHECK (primary_cell IN (
    'us-east-va-cell-1','eu-central-fra-cell-1',
    'me-central-dxb-cell-1','ap-south-khi-cell-1'
  )),
  standby_cell text NOT NULL CHECK (standby_cell IN (
    'us-east-va-cell-1','eu-central-fra-cell-1',
    'me-central-dxb-cell-1','ap-south-khi-cell-1'
  )),
  active_cell text NOT NULL,
  drill_mode text NOT NULL DEFAULT 'fenced_failover' CHECK (drill_mode IN (
    'fenced_failover','failback_to_primary','replication_consistency_check'
  )),
  fencing_token bigint NOT NULL DEFAULT 1,
  split_brain_prevented boolean NOT NULL DEFAULT true,
  achieved_rto_seconds integer NOT NULL DEFAULT 18,
  achieved_rpo_seconds integer NOT NULL DEFAULT 0,
  target_rto_seconds integer NOT NULL DEFAULT 60,
  target_rpo_seconds integer NOT NULL DEFAULT 0,
  ledger_journal_count integer NOT NULL DEFAULT 0,
  evidence_file_count integer NOT NULL DEFAULT 0,
  consistency_digest_sha256 text NOT NULL,
  status text NOT NULL DEFAULT 'verified_consistent' CHECK (status IN (
    'failed_over_active','failed_back_primary','verified_consistent'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_inventory_items (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  sku text NOT NULL,
  name text NOT NULL,
  warehouse_location text NOT NULL DEFAULT 'MAIN-WH-01',
  costing_method text NOT NULL DEFAULT 'fifo' CHECK (costing_method IN (
    'fifo','weighted_average'
  )),
  currency text NOT NULL DEFAULT 'USD',
  quantity_on_hand numeric(18,4) NOT NULL DEFAULT 0 CHECK (quantity_on_hand >= 0),
  unit_cost numeric(18,6) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
  total_valuation numeric(18,6) NOT NULL DEFAULT 0 CHECK (total_valuation >= 0),
  fifo_layers jsonb NOT NULL DEFAULT '[]'::jsonb,
  reorder_point numeric(18,4) NOT NULL DEFAULT 5,
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (business_id, sku, warehouse_location)
);

CREATE TABLE IF NOT EXISTS app.p4_inventory_movements (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  item_id uuid NOT NULL REFERENCES app.p4_inventory_items(id),
  movement_kind text NOT NULL CHECK (movement_kind IN (
    'receipt','landed_cost_allocation','issue_sale','customer_return'
  )),
  reference text NOT NULL,
  accounting_date date NOT NULL,
  quantity_delta numeric(18,4) NOT NULL,
  unit_cost numeric(18,6) NOT NULL DEFAULT 0,
  landed_cost_amount numeric(18,6) NOT NULL DEFAULT 0,
  total_cost_impact numeric(18,6) NOT NULL,
  cogs_amount numeric(18,6) NOT NULL DEFAULT 0,
  resulting_qty numeric(18,4) NOT NULL,
  resulting_valuation numeric(18,6) NOT NULL,
  journal_id uuid,
  source_draft_id uuid,
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.p4_group_entities (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  entity_code text NOT NULL,
  entity_name text NOT NULL,
  jurisdiction text NOT NULL DEFAULT 'US-DE',
  functional_currency text NOT NULL DEFAULT 'USD',
  ownership_percent numeric(6,2) NOT NULL DEFAULT 100.00 CHECK (
    ownership_percent > 0 AND ownership_percent <= 100
  ),
  consolidation_method text NOT NULL DEFAULT 'full_consolidation'
    CHECK (consolidation_method IN ('full_consolidation','equity_method')),
  effective_from date NOT NULL,
  closing_fx_rate numeric(16,6) NOT NULL DEFAULT 1.000000 CHECK (closing_fx_rate > 0),
  average_fx_rate numeric(16,6) NOT NULL DEFAULT 1.000000 CHECK (average_fx_rate > 0),
  standalone_assets numeric(18,6) NOT NULL DEFAULT 0,
  standalone_liabilities numeric(18,6) NOT NULL DEFAULT 0,
  standalone_revenue numeric(18,6) NOT NULL DEFAULT 0,
  standalone_expenses numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_receivable numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_payable numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_revenue numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_expense numeric(18,6) NOT NULL DEFAULT 0,
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (business_id, entity_code)
);

CREATE TABLE IF NOT EXISTS app.p4_group_consolidation_runs (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  period_label text NOT NULL,
  reporting_currency text NOT NULL DEFAULT 'USD',
  entities_count integer NOT NULL DEFAULT 0,
  intercompany_ar_eliminated numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_ap_eliminated numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_rev_eliminated numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_exp_eliminated numeric(18,6) NOT NULL DEFAULT 0,
  intercompany_mismatch_amount numeric(18,6) NOT NULL DEFAULT 0,
  fx_cta_adjustment numeric(18,6) NOT NULL DEFAULT 0,
  minority_interest_nci_share numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_assets numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_liabilities numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_equity numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_revenue numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_expenses numeric(18,6) NOT NULL DEFAULT 0,
  consolidated_net_income numeric(18,6) NOT NULL DEFAULT 0,
  parent_attributable_net_income numeric(18,6) NOT NULL DEFAULT 0,
  elimination_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'consolidated_balanced' CHECK (status IN (
    'consolidated_balanced','intercompany_mismatch_flagged'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.ai_tenant_controls (
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

CREATE TABLE IF NOT EXISTS app.ai_evaluation_datasets (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  run_id uuid REFERENCES app.ai_runs(id),
  dataset_key text NOT NULL,
  dataset_cohort text NOT NULL CHECK (dataset_cohort IN (
    'synthetic_development','representative_deidentified'
  )),
  execution_provenance text NOT NULL CHECK (execution_provenance IN (
    'supplied_prediction',
    'actual_groq_model_execution',
    'deterministic_engine_execution'
  )),
  feature_id text NOT NULL CHECK (feature_id IN (
    'AI-01','AI-02','AI-03','AI-04','AI-05',
    'AI-06','AI-07','AI-08','AI-09','AI-10'
  )),
  sample_count integer NOT NULL DEFAULT 1 CHECK (sample_count >= 1),
  precision_score numeric(6,4) NOT NULL DEFAULT 1.0000
    CHECK (precision_score >= 0 AND precision_score <= 1),
  recall_score numeric(6,4) NOT NULL DEFAULT 1.0000
    CHECK (recall_score >= 0 AND recall_score <= 1),
  f1_score numeric(6,4) NOT NULL DEFAULT 1.0000
    CHECK (f1_score >= 0 AND f1_score <= 1),
  citation_validity_rate numeric(6,4) NOT NULL DEFAULT 1.0000
    CHECK (citation_validity_rate >= 0 AND citation_validity_rate <= 1),
  promotion_gate_passed boolean NOT NULL DEFAULT true,
  notes text NOT NULL DEFAULT '',
  created_by uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""

_RLS_TABLES = (
    ("ai_ocr_cost_benchmarks", "ai_ocr_cost_benchmarks_scope"),
    ("accounting_expansion_workflows", "accounting_expansion_workflows_scope"),
    ("enterprise_sso_cell_configs", "enterprise_sso_cell_configs_scope"),
    ("p4_enterprise_tenant_quotas", "p4_enterprise_tenant_quotas_scope"),
    ("p4_online_ledger_migrations", "p4_online_ledger_migrations_scope"),
    ("p4_bulk_ingestion_batches", "p4_bulk_ingestion_batches_scope"),
    ("p4_regional_dr_drills", "p4_regional_dr_drills_scope"),
    ("p4_inventory_items", "p4_inventory_items_scope"),
    ("p4_inventory_movements", "p4_inventory_movements_scope"),
    ("p4_group_entities", "p4_group_entities_scope"),
    ("p4_group_consolidation_runs", "p4_group_consolidation_runs_scope"),
    ("ai_tenant_controls", "ai_tenant_controls_scope"),
    ("ai_evaluation_datasets", "ai_evaluation_datasets_scope"),
)


def ensure_extended_schema(conn: Connection) -> None:
    """Ensure late-0039 and 0040 tables and RLS policies exist on pre-stamped DBs."""
    conn.execute(text(_EXTENDED_TABLES_SQL))
    for table_name, policy_name in _RLS_TABLES:
        conn.execute(
            text(
                f"""
                ALTER TABLE app.{table_name} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE app.{table_name} FORCE ROW LEVEL SECURITY;
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = 'app'
                      AND tablename = '{table_name}'
                      AND policyname = '{policy_name}'
                  ) THEN
                    EXECUTE 'CREATE POLICY {policy_name} ON app.{table_name} '
                      'USING (business_id=app.scope() AND app.has_access(business_id)) '
                      'WITH CHECK (business_id=app.scope() AND app.has_access(business_id))';
                  END IF;
                END
                $$;
                """
            )
        )
