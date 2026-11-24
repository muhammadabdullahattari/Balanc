"""Append-only portfolio exception assignment history."""

from alembic import op

revision = "0038"
down_revision = "0037"
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
        CREATE TABLE app.exception_assignments (
          event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          kind text NOT NULL CHECK (kind IN ('bank','bill','close')),
          source_id uuid NOT NULL,
          actor uuid NOT NULL REFERENCES app.users(id),
          previous_user uuid REFERENCES app.users(id),
          assigned_user uuid REFERENCES app.users(id),
          changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK (previous_user IS DISTINCT FROM assigned_user)
        );
        CREATE INDEX exception_assignment_lookup ON app.exception_assignments
          (business_id,kind,source_id,event_id DESC);
        ALTER TABLE app.exception_assignments ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.exception_assignments FORCE ROW LEVEL SECURITY;
        CREATE POLICY exception_assignment_scope ON app.exception_assignments
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT ON app.exception_assignments TO {role};
        CREATE TABLE app.fx_proposals (
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          period_end date NOT NULL, monetary_account uuid NOT NULL, currency text NOT NULL,
          inputs jsonb NOT NULL, draft jsonb NOT NULL, actor uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (business_id,period_end,monetary_account,currency)
        );
        ALTER TABLE app.fx_proposals ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.fx_proposals FORCE ROW LEVEL SECURITY;
        CREATE POLICY fx_proposal_scope ON app.fx_proposals
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT ON app.fx_proposals TO {role};
        CREATE TABLE app.fixed_assets (
          id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
          name text NOT NULL, acquired date NOT NULL, cost numeric(18,6) NOT NULL CHECK(cost>0),
          residual numeric(18,6) NOT NULL CHECK(residual>=0 AND residual<cost),
          months integer NOT NULL CHECK(months BETWEEN 1 AND 1200),
          disposed_on date CHECK(disposed_on>=acquired), version integer NOT NULL DEFAULT 1,
          actor uuid NOT NULL REFERENCES app.users(id)
        );
        CREATE INDEX fixed_asset_list ON app.fixed_assets(business_id,id);
        ALTER TABLE app.fixed_assets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.fixed_assets FORCE ROW LEVEL SECURITY;
        CREATE POLICY fixed_asset_scope ON app.fixed_assets
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT,UPDATE ON app.fixed_assets TO {role};
        CREATE TABLE app.fx_quotes (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id), day date NOT NULL,
          foreign_currency text NOT NULL, base_currency text NOT NULL,
          rate numeric(16,8) NOT NULL CHECK(rate>0), manual boolean NOT NULL, source text NOT NULL,
          actor uuid NOT NULL REFERENCES app.users(id), recorded_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX fx_quote_lookup ON app.fx_quotes
          (business_id,day,foreign_currency,base_currency,manual DESC,id DESC);
        ALTER TABLE app.fx_quotes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.fx_quotes FORCE ROW LEVEL SECURITY;
        CREATE POLICY quote_scope ON app.fx_quotes
          USING (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND app.has_access(business_id));
        GRANT SELECT,INSERT ON app.fx_quotes TO {role};
        CREATE INDEX portfolio_open_close ON app.close_tasks(business_id,period_id,id)
          WHERE state='prepared';
        CREATE INDEX portfolio_pending_bank ON app.bank_rows(business_id,id)
          WHERE state='for_review';
        CREATE INDEX portfolio_bills ON app.trade_documents(business_id,id) WHERE kind='bill';
        CREATE FUNCTION app.portfolio_counts() RETURNS TABLE
        (business_id uuid, business_name text, exceptions bigint, severity integer, assignees uuid[])
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET jit=off AS $$
        DECLARE b record; original_scope text := current_setting('app.business_id',true);
        BEGIN
          FOR b IN SELECT m.business_id AS bid, w.name, w.timezone FROM app.memberships m
            JOIN app.businesses w ON w.id=m.business_id WHERE m.user_id=app.actor()
            AND m.active AND m.role IN ('owner','accountant') LOOP
            PERFORM set_config('app.business_id', b.bid::text,true);
            RETURN QUERY WITH open_items(kind,source_id,level) AS (
              SELECT 'bank',r.id,1 FROM app.bank_rows r WHERE r.business_id=b.bid AND r.state='for_review'
              UNION ALL SELECT 'bill',d.id,2 FROM app.trade_documents d WHERE d.business_id=b.bid
                AND d.kind='bill' AND NOT EXISTS (SELECT FROM app.evidence_links l WHERE
                l.business_id=b.bid AND l.resource_kind='trade_document' AND l.resource_id=d.id)
              UNION ALL SELECT 'close',t.id,3 FROM app.close_tasks t JOIN app.periods p ON p.id=t.period_id
                AND p.business_id=b.bid WHERE t.business_id=b.bid AND t.state='prepared'
                AND p.end_date < timezone(b.timezone,now())::date
            ) SELECT b.bid,b.name::text,count(*),coalesce(max(o.level),0),
              coalesce(array_agg(DISTINCT h.assigned_user ORDER BY h.assigned_user)
                FILTER(WHERE h.assigned_user IS NOT NULL),ARRAY[]::uuid[])
              FROM open_items o LEFT JOIN LATERAL (SELECT a.assigned_user FROM app.exception_assignments a
                WHERE a.business_id=b.bid AND a.source_id=o.source_id AND a.kind=o.kind
                ORDER BY a.event_id DESC LIMIT 1) h ON true;
          END LOOP;
          PERFORM set_config('app.business_id',coalesce(original_scope,''),true);
        END $$;
        REVOKE ALL ON FUNCTION app.portfolio_counts() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.portfolio_counts() TO {role};
    """)


def downgrade() -> None:
    op.execute("DROP INDEX app.portfolio_open_close,app.portfolio_pending_bank,app.portfolio_bills")
    op.execute("DROP FUNCTION app.portfolio_counts()")
    op.execute("DROP TABLE app.fx_quotes, app.fixed_assets, app.fx_proposals, app.exception_assignments")
