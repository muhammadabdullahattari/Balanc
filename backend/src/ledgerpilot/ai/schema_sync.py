"""Idempotent startup schema guard for P3, P4, and W0..AI-10 tables on pre-stamped DBs."""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.engine import Connection

_EARLY_0039_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS app.channel_reminder_preferences (
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

CREATE TABLE IF NOT EXISTS app.workspace_channel_connections (
  business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
  default_whatsapp_recipient text NOT NULL DEFAULT '+923412685261',
  default_slack_channel text NOT NULL DEFAULT '#all-balanc',
  encrypted_config text NOT NULL DEFAULT '',
  actor uuid NOT NULL REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.channel_reminders (
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
    'skipped_resolved','skipped_opted_out',
    'skipped_quiet_hours','skipped_cap_reached','failed'
  )),
  provider_message_id text NOT NULL DEFAULT '',
  provider_response jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text NOT NULL,
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (business_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS app.supplier_portal_connections (
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

CREATE TABLE IF NOT EXISTS app.supplier_portal_ingestions (
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
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL CHECK (status IN (
    'ingested_to_draft','duplicate_skipped','manual_review_required'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.supplier_payment_controls (
  business_id uuid PRIMARY KEY REFERENCES app.businesses(id),
  kill_switch_active boolean NOT NULL DEFAULT false,
  kill_switch_reason text NOT NULL DEFAULT '',
  single_payment_limit numeric(18,6) NOT NULL DEFAULT 50000.000000
    CHECK (single_payment_limit > 0),
  dual_approval_threshold numeric(18,6) NOT NULL DEFAULT 5000.000000
    CHECK (dual_approval_threshold > 0),
  default_rail text NOT NULL DEFAULT 'stripe_live' CHECK (default_rail IN (
    'stripe_live','ach_nacha','swift_wire'
  )),
  updated_by uuid NOT NULL REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.supplier_beneficiaries (
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

CREATE TABLE IF NOT EXISTS app.supplier_payment_executions (
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
  provider_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  remittance_advice text NOT NULL DEFAULT '',
  settlement_journal_id uuid,
  failure_reason text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  executed_at timestamptz,
  UNIQUE (business_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS app.reconciliation_sla_trackers (
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

CREATE TABLE IF NOT EXISTS app.gross_to_net_payout_reconciliations (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  payout_reference text NOT NULL,
  provider text NOT NULL DEFAULT 'stripe_financial_connections' CHECK (provider IN (
    'stripe_financial_connections','shopify_payouts','amazon_settlement','square_payouts'
  )),
  payout_date date NOT NULL,
  currency text NOT NULL DEFAULT 'USD',
  gross_sales_amount numeric(18,6) NOT NULL CHECK (gross_sales_amount > 0),
  processing_fee_amount numeric(18,6) NOT NULL DEFAULT 0
    CHECK (processing_fee_amount >= 0),
  refund_dispute_amount numeric(18,6) NOT NULL DEFAULT 0
    CHECK (refund_dispute_amount >= 0),
  tax_withheld_amount numeric(18,6) NOT NULL DEFAULT 0
    CHECK (tax_withheld_amount >= 0),
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

CREATE TABLE IF NOT EXISTS app.voice_note_instructions (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  audio_filename text NOT NULL DEFAULT 'voice-instruction.webm',
  audio_mime_type text NOT NULL DEFAULT 'audio/webm',
  audio_duration_seconds integer NOT NULL DEFAULT 12
    CHECK (audio_duration_seconds >= 1),
  audio_sha256 text NOT NULL,
  transcript_text text NOT NULL,
  extracted_action text NOT NULL CHECK (extracted_action IN (
    'bill','invoice','expense','journal'
  )),
  extracted_counterparty text NOT NULL DEFAULT '',
  extracted_amount numeric(18,6) NOT NULL CHECK (extracted_amount > 0),
  extracted_tax_amount numeric(18,6) NOT NULL DEFAULT 0
    CHECK (extracted_tax_amount >= 0),
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

CREATE TABLE IF NOT EXISTS app.tenant_encryption_keys (
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
"""

_LATE_0039_AND_0040_TABLES_SQL = """
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

CREATE TABLE IF NOT EXISTS app.ai_runs (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  feature_id text NOT NULL CHECK (feature_id IN (
    'AI-01','AI-02','AI-03','AI-04','AI-05',
    'AI-06','AI-07','AI-08','AI-09','AI-10'
  )),
  wave text NOT NULL DEFAULT 'W0' CHECK (wave IN (
    'W0','W1','W2','W3','W4','W5','W6'
  )),
  provider text NOT NULL DEFAULT 'groq' CHECK (provider IN (
    'groq','deterministic_fallback'
  )),
  model_name text NOT NULL DEFAULT 'llama-3.3-70b-versatile',
  prompt_version text NOT NULL DEFAULT 'v1.0',
  tool_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'completed' CHECK (status IN (
    'running','completed','abstained','canceled','failed'
  )),
  input_summary text NOT NULL,
  structured_output jsonb NOT NULL DEFAULT '{}'::jsonb,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  tool_invocations jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence_score numeric(6,4) NOT NULL DEFAULT 1.0000
    CHECK (confidence_score >= 0 AND confidence_score <= 1),
  uncertainty_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  snapshot_hash text NOT NULL,
  business_version integer NOT NULL DEFAULT 0,
  policy_version integer NOT NULL DEFAULT 1,
  prompt_tokens integer NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
  completion_tokens integer NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
  total_tokens integer NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
  latency_ms integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
  estimated_cost_usd numeric(18,6) NOT NULL DEFAULT 0
    CHECK (estimated_cost_usd >= 0),
  approval_state text NOT NULL DEFAULT 'pending_review' CHECK (approval_state IN (
    'not_required','pending_review','approved',
    'rejected','stale_invalidated','executed'
  )),
  approval_content_hash text NOT NULL,
  approved_by uuid REFERENCES app.users(id),
  approved_at timestamptz,
  rejection_reason text NOT NULL DEFAULT '',
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.ai_tool_audit_logs (
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

CREATE TABLE IF NOT EXISTS app.ai_accounting_memories (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  memory_key text NOT NULL,
  title text NOT NULL,
  accounting_framework text NOT NULL DEFAULT 'US_GAAP'
    CHECK (accounting_framework IN ('US_GAAP','IFRS','TAX_BASIS')),
  category text NOT NULL DEFAULT 'categorization' CHECK (category IN (
    'categorization','recognition','materiality','vendor_policy','close_rule'
  )),
  binding_kind text NOT NULL DEFAULT 'binding_policy' CHECK (binding_kind IN (
    'binding_policy','past_example'
  )),
  rule_condition text NOT NULL,
  account_code text NOT NULL,
  account_name text NOT NULL,
  materiality_threshold numeric(18,6) NOT NULL DEFAULT 0
    CHECK (materiality_threshold >= 0),
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

CREATE TABLE IF NOT EXISTS app.ai_reconciliation_investigations (
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
  proposed_allocation jsonb NOT NULL DEFAULT '{}'::jsonb,
  explanation text NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN (
    'open','proposed','approved','rejected','abstained'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.ai_client_clarifications (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  run_id uuid NOT NULL REFERENCES app.ai_runs(id),
  exception_kind text NOT NULL CHECK (exception_kind IN (
    'bank_row','missing_invoice','unmatched_payment','contract_variance'
  )),
  exception_ref text NOT NULL,
  recipient_key text NOT NULL,
  channel text NOT NULL DEFAULT 'email'
    CHECK (channel IN ('email','whatsapp','slack')),
  questions jsonb NOT NULL DEFAULT '[]'::jsonb,
  required_documents text[] NOT NULL DEFAULT ARRAY[]::text[],
  client_reply_text text NOT NULL DEFAULT '',
  uploaded_evidence_ref text NOT NULL DEFAULT '',
  completeness_score numeric(6,4) NOT NULL DEFAULT 0
    CHECK (completeness_score >= 0 AND completeness_score <= 1),
  remaining_gaps text[] NOT NULL DEFAULT ARRAY[]::text[],
  status text NOT NULL DEFAULT 'sent' CHECK (status IN (
    'draft','sent','sent_sandbox','delivery_failed','partially_answered',
    'resolved_closed','opted_out','escalated_dispute'
  )),
  actor uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS app.ai_voice_accounting_sessions (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  run_id uuid NOT NULL REFERENCES app.ai_runs(id),
  language_locale text NOT NULL DEFAULT 'en_US' CHECK (language_locale IN (
    'en_US','ur_PK','en_ur_mixed'
  )),
  audio_format text NOT NULL DEFAULT 'audio/webm',
  duration_seconds integer NOT NULL DEFAULT 6
    CHECK (duration_seconds BETWEEN 1 AND 300),
  stt_provider text NOT NULL DEFAULT 'groq_whisper_large_v3_turbo'
    CHECK (stt_provider IN (
      'groq_whisper_large_v3_turbo','browser_web_speech','deterministic_fixture'
    )),
  raw_transcript text NOT NULL,
  corrected_transcript text NOT NULL,
  extracted_intent text NOT NULL,
  extracted_entities jsonb NOT NULL DEFAULT '{}'::jsonb,
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

CREATE TABLE IF NOT EXISTS app.ai_contract_verifications (
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

CREATE TABLE IF NOT EXISTS app.ai_cash_scenarios (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  run_id uuid NOT NULL REFERENCES app.ai_runs(id),
  scenario_name text NOT NULL,
  natural_language_prompt text NOT NULL,
  currency text NOT NULL DEFAULT 'USD',
  horizon_weeks integer NOT NULL DEFAULT 13
    CHECK (horizon_weeks BETWEEN 4 AND 52),
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  baseline_ending_cash numeric(18,6) NOT NULL DEFAULT 0,
  scenario_ending_cash numeric(18,6) NOT NULL DEFAULT 0,
  net_cash_delta numeric(18,6) NOT NULL DEFAULT 0,
  lowest_weekly_cash numeric(18,6) NOT NULL DEFAULT 0,
  shortfall_weeks integer NOT NULL DEFAULT 0 CHECK (shortfall_weeks >= 0),
  weekly_series jsonb NOT NULL DEFAULT '[]'::jsonb,
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

CREATE TABLE IF NOT EXISTS app.financial_audit_runs (
  id uuid PRIMARY KEY,
  business_id uuid NOT NULL REFERENCES app.businesses(id),
  audit_type text NOT NULL CHECK (audit_type IN (
    'monthly','yearly','emergency','auto_balance_sheet','auto_period_close'
  )),
  trigger_source text NOT NULL DEFAULT 'manual' CHECK (trigger_source IN (
    'manual','balance_sheet_generated','period_locked',
    'close_package_prepared','background_job'
  )),
  period_start date NOT NULL,
  period_end date NOT NULL,
  scope_filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'completed' CHECK (status IN (
    'queued','running','completed','completed_empty','failed'
  )),
  job_id uuid REFERENCES app.background_jobs(id),
  thread_id text NOT NULL,
  loaded_data_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  severity_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  findings jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw_report jsonb NOT NULL DEFAULT '{}'::jsonb,
  polished_report jsonb NOT NULL DEFAULT '{}'::jsonb,
  retrieved_context jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (period_end >= period_start)
);
"""

_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS channel_reminders_target_idx
  ON app.channel_reminders (business_id, target_kind, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS supplier_portal_connections_biz_idx
  ON app.supplier_portal_connections (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS supplier_portal_ingestions_lookup_idx
  ON app.supplier_portal_ingestions (
    business_id, connection_id, external_invoice_id, created_at DESC
  );
CREATE INDEX IF NOT EXISTS supplier_beneficiaries_biz_idx
  ON app.supplier_beneficiaries (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS supplier_payment_executions_biz_idx
  ON app.supplier_payment_executions (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS gross_to_net_payout_biz_idx
  ON app.gross_to_net_payout_reconciliations (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS voice_note_instructions_biz_idx
  ON app.voice_note_instructions (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tenant_encryption_keys_biz_idx
  ON app.tenant_encryption_keys (business_id, key_version DESC);
CREATE INDEX IF NOT EXISTS ai_runs_business_feature_idx
  ON app.ai_runs (business_id, feature_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_tool_audit_logs_biz_idx
  ON app.ai_tool_audit_logs (business_id, run_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS ai_accounting_memories_lookup_idx
  ON app.ai_accounting_memories (business_id, accounting_framework, status, effective_from DESC);
CREATE INDEX IF NOT EXISTS ai_recon_investigations_biz_idx
  ON app.ai_reconciliation_investigations (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_client_clarifications_biz_idx
  ON app.ai_client_clarifications (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_voice_sessions_biz_idx
  ON app.ai_voice_accounting_sessions (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_contract_verifications_biz_idx
  ON app.ai_contract_verifications (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_cash_scenarios_biz_idx
  ON app.ai_cash_scenarios (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_evaluation_datasets_biz_idx
  ON app.ai_evaluation_datasets (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS financial_audit_runs_biz_created_idx
  ON app.financial_audit_runs (business_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS financial_audit_runs_biz_type_period_idx
  ON app.financial_audit_runs (business_id, audit_type, period_start, period_end);
"""

_RLS_TABLES = (
    ("channel_reminder_preferences", "channel_reminder_preferences_scope"),
    ("workspace_channel_connections", "workspace_channel_connections_scope"),
    ("channel_reminders", "channel_reminders_scope"),
    ("supplier_portal_connections", "supplier_portal_connections_scope"),
    ("supplier_portal_ingestions", "supplier_portal_ingestions_scope"),
    ("supplier_payment_controls", "supplier_payment_controls_scope"),
    ("supplier_beneficiaries", "supplier_beneficiaries_scope"),
    ("supplier_payment_executions", "supplier_payment_executions_scope"),
    ("reconciliation_sla_trackers", "reconciliation_sla_trackers_scope"),
    ("gross_to_net_payout_reconciliations", "gross_to_net_payout_scope"),
    ("voice_note_instructions", "voice_note_instructions_scope"),
    ("tenant_encryption_keys", "tenant_encryption_keys_scope"),
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
    ("ai_runs", "ai_runs_scope"),
    ("ai_tool_audit_logs", "ai_tool_audit_logs_scope"),
    ("ai_accounting_memories", "ai_accounting_memories_scope"),
    ("ai_reconciliation_investigations", "ai_reconciliation_investigations_scope"),
    ("ai_client_clarifications", "ai_client_clarifications_scope"),
    ("ai_voice_accounting_sessions", "ai_voice_accounting_sessions_scope"),
    ("ai_contract_verifications", "ai_contract_verifications_scope"),
    ("ai_cash_scenarios", "ai_cash_scenarios_scope"),
    ("ai_tenant_controls", "ai_tenant_controls_scope"),
    ("ai_evaluation_datasets", "ai_evaluation_datasets_scope"),
    ("financial_audit_runs", "financial_audit_runs_scope"),
)


def _resolve_runtime_roles(conn: Connection) -> list[str]:
    """Return quoted role identifiers that exist in pg_roles for runtime grants."""
    candidates = {
        os.environ.get("APP_DB_USER", "").strip(),
        os.environ.get("LEDGERPILOT_DB_RUNTIME_USER", "").strip(),
        "ledgerpilot_app",
    }
    roles: list[str] = []
    preparer = conn.dialect.identifier_preparer
    for candidate in sorted(c for c in candidates if c):
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
            {"r": candidate},
        ).scalar()
        if exists:
            roles.append(preparer.quote(candidate))
    return roles


def ensure_extended_schema(conn: Connection) -> None:
    """Ensure all 0039 and 0040 tables, indexes, RLS policies, and grants exist."""
    conn.execute(text(_EARLY_0039_TABLES_SQL))
    conn.execute(text(_LATE_0039_AND_0040_TABLES_SQL))
    conn.execute(
        text(
            """
            DO $$
            DECLARE
              cname text;
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE nsp.nspname = 'app'
                  AND rel.relname = 'ai_client_clarifications'
                  AND con.contype = 'c'
                  AND pg_get_constraintdef(con.oid) LIKE '%delivery_failed%'
              ) THEN
                FOR cname IN
                  SELECT con.conname
                  FROM pg_constraint con
                  JOIN pg_class rel ON rel.oid = con.conrelid
                  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                  WHERE nsp.nspname = 'app'
                    AND rel.relname = 'ai_client_clarifications'
                    AND con.contype = 'c'
                    AND pg_get_constraintdef(con.oid) LIKE '%status%'
                LOOP
                  EXECUTE format(
                    'ALTER TABLE app.ai_client_clarifications DROP CONSTRAINT %I',
                    cname
                  );
                END LOOP;
                ALTER TABLE app.ai_client_clarifications
                  ALTER COLUMN status SET DEFAULT 'sent',
                  ADD CONSTRAINT ai_client_clarifications_status_check CHECK (
                    status IN (
                      'draft',
                      'sent',
                      'sent_sandbox',
                      'delivery_failed',
                      'partially_answered',
                      'resolved_closed',
                      'opted_out',
                      'escalated_dispute'
                    )
                  );
              END IF;
            END $$;
            """
        )
    )
    conn.execute(text(_INDEXES_SQL))
    extra_roles = _resolve_runtime_roles(conn)
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
                GRANT SELECT, INSERT, UPDATE, DELETE ON app.{table_name} TO CURRENT_USER;
                """
            )
        )
        for quoted_role in extra_roles:
            conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON app.{table_name} TO {quoted_role}")
            )
