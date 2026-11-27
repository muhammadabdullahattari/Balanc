"""LangGraph financial audit runs table with strict tenant RLS."""

from alembic import op

revision = "0041"
down_revision = "0040"
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
        CREATE TABLE app.financial_audit_runs (
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
          scope_filters jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          status text NOT NULL DEFAULT 'completed' CHECK (status IN (
            'queued','running','completed','completed_empty','failed'
          )),
          job_id uuid REFERENCES app.background_jobs(id),
          thread_id text NOT NULL,
          loaded_data_summary jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          severity_summary jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          findings jsonb NOT NULL DEFAULT '[]'::jsonb,
          raw_report jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          polished_report jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          retrieved_context jsonb NOT NULL DEFAULT '[]'::jsonb,
          metadata_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK (period_end >= period_start)
        );
        CREATE INDEX financial_audit_runs_biz_created_idx
          ON app.financial_audit_runs (business_id, created_at DESC, id DESC);
        CREATE INDEX financial_audit_runs_biz_type_period_idx
          ON app.financial_audit_runs (business_id, audit_type, period_start, period_end);
        ALTER TABLE app.financial_audit_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.financial_audit_runs FORCE ROW LEVEL SECURITY;
        CREATE POLICY financial_audit_runs_scope ON app.financial_audit_runs
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.financial_audit_runs TO {role};
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.financial_audit_runs;")
