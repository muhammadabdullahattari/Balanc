"""Read-only SQL reporting functions and SELECT-only policies for Grafana analytics."""

from alembic import op

revision = "0043"
down_revision = "0042"
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
        DO $$
        DECLARE
          tbl text;
        BEGIN
          FOREACH tbl IN ARRAY ARRAY[
            'businesses',
            'accounts',
            'journals',
            'journal_lines',
            'trade_documents',
            'payments',
            'financial_audit_runs'
          ]
          LOOP
            IF NOT EXISTS (
              SELECT 1
              FROM pg_policies
              WHERE schemaname = 'app'
                AND tablename = tbl
                AND policyname = 'grafana_readonly_select'
            ) THEN
              EXECUTE format(
                'CREATE POLICY grafana_readonly_select ON app.%I '
                'FOR SELECT USING (session_user::text = ''ledgerpilot_grafana_ro'')',
                tbl
              );
            END IF;
          END LOOP;
        END $$;

        CREATE OR REPLACE FUNCTION app.grafana_businesses()
        RETURNS TABLE (
          id uuid,
          name text,
          currency text,
          basis text
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          SELECT b.id, b.name, b.currency, b.basis
          FROM app.businesses b
          ORDER BY b.name, b.id;
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_kpi_summary(
          p_business_id uuid,
          p_start date,
          p_end date
        )
        RETURNS TABLE (
          total_income numeric(24,6),
          total_expense numeric(24,6),
          net_balance numeric(24,6),
          cash_balance numeric(24,6),
          total_assets numeric(24,6),
          total_liabilities numeric(24,6),
          total_equity numeric(24,6),
          audit_issue_count bigint,
          critical_issue_count bigint
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          WITH period_pl AS (
            SELECT
              coalesce(sum(CASE WHEN a.kind = 'income' THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS total_income,
              coalesce(sum(CASE WHEN a.kind = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS total_expense
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = p_business_id
              AND j.status = 'posted'
              AND j.accounting_date BETWEEN p_start AND p_end
          ),
          bs AS (
            SELECT
              coalesce(sum(CASE WHEN a.kind = 'asset' AND a.control IS NULL THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS cash_balance,
              coalesce(sum(CASE WHEN a.kind = 'asset' THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS total_assets,
              coalesce(sum(CASE WHEN a.kind = 'liability' THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS total_liabilities,
              coalesce(sum(CASE WHEN a.kind IN ('equity', 'income', 'expense') THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS total_equity
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = p_business_id
              AND j.status = 'posted'
              AND j.accounting_date <= p_end
          ),
          aud AS (
            SELECT
              coalesce((r.severity_summary->>'total')::bigint, 0) AS issue_cnt,
              coalesce((r.severity_summary->>'critical')::bigint, 0) AS crit_cnt
            FROM app.financial_audit_runs r
            WHERE r.business_id = p_business_id
              AND r.period_end >= p_start
              AND r.period_start <= p_end
            ORDER BY r.created_at DESC
            LIMIT 1
          )
          SELECT
            period_pl.total_income,
            period_pl.total_expense,
            (period_pl.total_income - period_pl.total_expense)::numeric(24,6) AS net_balance,
            bs.cash_balance,
            bs.total_assets,
            bs.total_liabilities,
            bs.total_equity,
            coalesce((SELECT issue_cnt FROM aud), 0) AS audit_issue_count,
            coalesce((SELECT crit_cnt FROM aud), 0) AS critical_issue_count
          FROM period_pl, bs;
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_income_expense_series(
          p_business_id uuid,
          p_start date,
          p_end date,
          p_bucket text DEFAULT 'month'
        )
        RETURNS TABLE (
          bucket_time timestamptz,
          period_label text,
          income numeric(24,6),
          expense numeric(24,6),
          net_profit numeric(24,6),
          cash_delta numeric(24,6)
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          WITH unit AS (
            SELECT CASE WHEN lower(p_bucket) = 'year' THEN 'year' ELSE 'month' END AS gran
          ),
          grouped AS (
            SELECT
              date_trunc((SELECT gran FROM unit), j.accounting_date::timestamp) AS b_ts,
              coalesce(sum(CASE WHEN a.kind = 'income' THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS inc,
              coalesce(sum(CASE WHEN a.kind = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS exp,
              coalesce(sum(CASE WHEN a.kind = 'asset' AND a.control IS NULL THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS cdelta
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = p_business_id
              AND j.status = 'posted'
              AND j.accounting_date BETWEEN p_start AND p_end
            GROUP BY 1
          )
          SELECT
            (grouped.b_ts AT TIME ZONE 'UTC') AS bucket_time,
            CASE
              WHEN (SELECT gran FROM unit) = 'year' THEN to_char(grouped.b_ts, 'YYYY')
              ELSE to_char(grouped.b_ts, 'YYYY-MM')
            END AS period_label,
            grouped.inc AS income,
            grouped.exp AS expense,
            (grouped.inc - grouped.exp)::numeric(24,6) AS net_profit,
            grouped.cdelta AS cash_delta
          FROM grouped
          ORDER BY grouped.b_ts ASC;
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_category_breakdown(
          p_business_id uuid,
          p_start date,
          p_end date,
          p_kind text DEFAULT 'expense',
          p_limit integer DEFAULT 6
        )
        RETURNS TABLE (
          category_label text,
          amount numeric(24,6),
          share_pct numeric(10,2)
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          WITH raw_cats AS (
            SELECT
              a.code || ' · ' || a.name AS label,
              CASE
                WHEN a.kind IN ('income', 'liability', 'equity')
                  THEN coalesce(sum(jl.credit - jl.debit), 0)
                ELSE coalesce(sum(jl.debit - jl.credit), 0)
              END::numeric(24,6) AS amt
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = p_business_id
              AND j.status = 'posted'
              AND j.accounting_date BETWEEN p_start AND p_end
              AND a.kind = lower(p_kind)
            GROUP BY a.id, a.code, a.name, a.kind
          ),
          positive_cats AS (
            SELECT label, amt
            FROM raw_cats
            WHERE amt > 0
          ),
          ranked AS (
            SELECT
              label,
              amt,
              row_number() OVER (ORDER BY amt DESC, label ASC) AS rn,
              sum(amt) OVER () AS grand_total
            FROM positive_cats
          ),
          collapsed AS (
            SELECT
              CASE WHEN rn <= greatest(p_limit, 3) THEN label ELSE 'Other categories' END AS category_label,
              CASE WHEN rn <= greatest(p_limit, 3) THEN rn ELSE 999999 END AS sort_key,
              sum(amt)::numeric(24,6) AS amount,
              max(grand_total) AS grand_total
            FROM ranked
            GROUP BY 1, 2
          )
          SELECT
            collapsed.category_label,
            collapsed.amount,
            CASE
              WHEN collapsed.grand_total > 0
                THEN round((collapsed.amount / collapsed.grand_total) * 100, 2)::numeric(10,2)
              ELSE 0::numeric(10,2)
            END AS share_pct
          FROM collapsed
          ORDER BY collapsed.sort_key ASC;
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_balance_sheet_summary(
          p_business_id uuid,
          p_as_of date
        )
        RETURNS TABLE (
          section text,
          amount numeric(24,6)
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          WITH totals AS (
            SELECT
              coalesce(sum(CASE WHEN a.kind = 'asset' THEN jl.debit - jl.credit ELSE 0 END), 0)::numeric(24,6) AS assets,
              coalesce(sum(CASE WHEN a.kind = 'liability' THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS liabilities,
              coalesce(sum(CASE WHEN a.kind = 'equity' THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS paid_equity,
              coalesce(sum(CASE WHEN a.kind IN ('income', 'expense') THEN jl.credit - jl.debit ELSE 0 END), 0)::numeric(24,6) AS retained_earnings
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = p_business_id
              AND j.status = 'posted'
              AND j.accounting_date <= p_as_of
          )
          SELECT 'Assets'::text AS section, assets AS amount FROM totals
          UNION ALL
          SELECT 'Liabilities'::text AS section, liabilities AS amount FROM totals
          UNION ALL
          SELECT 'Equity (incl. Retained Earnings)'::text AS section, (paid_equity + retained_earnings)::numeric(24,6) AS amount FROM totals;
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_top_items(
          p_business_id uuid,
          p_start date,
          p_end date,
          p_limit integer DEFAULT 8
        )
        RETURNS TABLE (
          item_label text,
          entry_date date,
          kind text,
          amount numeric(24,6)
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          SELECT
            coalesce(nullif(j.memo, ''), a.code || ' · ' || a.name) AS item_label,
            j.accounting_date AS entry_date,
            a.kind,
            greatest(jl.debit, jl.credit)::numeric(24,6) AS amount
          FROM app.journals j
          JOIN app.journal_lines jl
            ON jl.business_id = j.business_id AND jl.journal_id = j.id
          JOIN app.accounts a
            ON a.business_id = jl.business_id AND a.id = jl.account_id
          WHERE j.business_id = p_business_id
            AND j.status = 'posted'
            AND j.accounting_date BETWEEN p_start AND p_end
            AND a.control IS NULL
          ORDER BY greatest(jl.debit, jl.credit) DESC, j.accounting_date DESC
          LIMIT greatest(1, least(p_limit, 25));
        $$;

        CREATE OR REPLACE FUNCTION app.grafana_audit_series(
          p_business_id uuid,
          p_start date,
          p_end date,
          p_audit_type text DEFAULT 'all'
        )
        RETURNS TABLE (
          run_time timestamptz,
          period_label text,
          audit_type text,
          critical_count integer,
          high_count integer,
          medium_count integer,
          low_count integer,
          total_count integer
        )
        LANGUAGE sql
        STABLE
        SET search_path = app, pg_catalog
        AS $$
          SELECT
            r.created_at AS run_time,
            (r.period_start::text || ' .. ' || r.period_end::text) AS period_label,
            r.audit_type,
            coalesce((r.severity_summary->>'critical')::integer, 0) AS critical_count,
            coalesce((r.severity_summary->>'high')::integer, 0) AS high_count,
            coalesce((r.severity_summary->>'medium')::integer, 0) AS medium_count,
            coalesce((r.severity_summary->>'low')::integer, 0) AS low_count,
            coalesce((r.severity_summary->>'total')::integer, 0) AS total_count
          FROM app.financial_audit_runs r
          WHERE r.business_id = p_business_id
            AND r.period_end >= p_start
            AND r.period_start <= p_end
            AND (lower(p_audit_type) = 'all' OR r.audit_type = p_audit_type)
          ORDER BY r.created_at ASC;
        $$;

        GRANT EXECUTE ON FUNCTION
          app.grafana_businesses(),
          app.grafana_kpi_summary(uuid, date, date),
          app.grafana_income_expense_series(uuid, date, date, text),
          app.grafana_category_breakdown(uuid, date, date, text, integer),
          app.grafana_balance_sheet_summary(uuid, date),
          app.grafana_top_items(uuid, date, date, integer),
          app.grafana_audit_series(uuid, date, date, text)
        TO {role};

        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgerpilot_grafana_ro') THEN
            GRANT USAGE ON SCHEMA app TO ledgerpilot_grafana_ro;
            GRANT SELECT ON
              app.businesses,
              app.accounts,
              app.journals,
              app.journal_lines,
              app.trade_documents,
              app.payments,
              app.financial_audit_runs
            TO ledgerpilot_grafana_ro;
            GRANT EXECUTE ON FUNCTION
              app.grafana_businesses(),
              app.grafana_kpi_summary(uuid, date, date),
              app.grafana_income_expense_series(uuid, date, date, text),
              app.grafana_category_breakdown(uuid, date, date, text, integer),
              app.grafana_balance_sheet_summary(uuid, date),
              app.grafana_top_items(uuid, date, date, integer),
              app.grafana_audit_series(uuid, date, date, text)
            TO ledgerpilot_grafana_ro;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS app.grafana_audit_series(uuid, date, date, text);
        DROP FUNCTION IF EXISTS app.grafana_top_items(uuid, date, date, integer);
        DROP FUNCTION IF EXISTS app.grafana_balance_sheet_summary(uuid, date);
        DROP FUNCTION IF EXISTS app.grafana_category_breakdown(uuid, date, date, text, integer);
        DROP FUNCTION IF EXISTS app.grafana_income_expense_series(uuid, date, date, text);
        DROP FUNCTION IF EXISTS app.grafana_kpi_summary(uuid, date, date);
        DROP FUNCTION IF EXISTS app.grafana_businesses();

        DROP POLICY IF EXISTS grafana_readonly_select ON app.financial_audit_runs;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.payments;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.trade_documents;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.journal_lines;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.journals;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.accounts;
        DROP POLICY IF EXISTS grafana_readonly_select ON app.businesses;
    """)
