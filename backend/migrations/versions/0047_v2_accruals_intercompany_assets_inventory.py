"""Next-Gen v2.0 Phase K-N schema: accruals, intercompany pairs, VAPID push, MACRS, and landed cost."""

from alembic import op

revision = "0047"
down_revision = "0046"
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
        -- K1 / Module 11: Month-end accrual candidates & Day-1 auto-reversals
        CREATE TABLE IF NOT EXISTS app.accrual_candidates (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          period_end date NOT NULL,
          reverses_on date NOT NULL,
          source_kind text NOT NULL CHECK (
            source_kind IN ('open_po', 'signed_contract', 'delivery_note', 'recurring_bill')
          ),
          source_id text NOT NULL,
          description text NOT NULL,
          estimated_amount numeric(24,6) NOT NULL CHECK (estimated_amount > 0),
          currency text NOT NULL DEFAULT 'USD',
          expense_account_id uuid REFERENCES app.accounts(id),
          accrued_liability_account_id uuid REFERENCES app.accounts(id),
          status text NOT NULL DEFAULT 'candidate'
            CHECK (status IN ('candidate', 'posted', 'reversed', 'dismissed')),
          accrual_journal_id uuid REFERENCES app.journals(id),
          reversal_journal_id uuid REFERENCES app.journals(id),
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, period_end, source_kind, source_id)
        );
        ALTER TABLE app.accrual_candidates ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.accrual_candidates FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.accrual_candidates
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.accrual_candidates TO {role};

        -- M3 / Module 6: Intercompany links & real-time mirror pairs
        CREATE TABLE IF NOT EXISTS app.intercompany_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          counterparty_business_id uuid NOT NULL REFERENCES app.businesses(id),
          ic_receivable_account_id uuid REFERENCES app.accounts(id),
          ic_revenue_account_id uuid REFERENCES app.accounts(id),
          counterparty_payable_account_id uuid,
          counterparty_expense_account_id uuid,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, counterparty_business_id)
        );
        ALTER TABLE app.intercompany_links ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.intercompany_links FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.intercompany_links
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.intercompany_links TO {role};

        CREATE TABLE IF NOT EXISTS app.intercompany_pairs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          counterparty_business_id uuid NOT NULL REFERENCES app.businesses(id),
          reference text NOT NULL,
          accounting_date date NOT NULL,
          amount numeric(24,6) NOT NULL CHECK (amount > 0),
          currency text NOT NULL DEFAULT 'USD',
          source_journal_or_doc_id uuid,
          mirror_draft_id uuid,
          status text NOT NULL DEFAULT 'mirrored'
            CHECK (status IN ('mirrored', 'eliminated', 'mismatched')),
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, counterparty_business_id, reference)
        );
        ALTER TABLE app.intercompany_pairs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.intercompany_pairs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.intercompany_pairs
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.intercompany_pairs TO {role};

        -- N1: Local VAPID Web Push subscriptions for owner phone alerts
        CREATE TABLE IF NOT EXISTS app.push_subscriptions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          user_id uuid NOT NULL REFERENCES app.users(id),
          endpoint text NOT NULL,
          p256dh_key text NOT NULL,
          auth_secret text NOT NULL,
          device_label text NOT NULL DEFAULT 'Owner browser',
          active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, user_id, endpoint)
        );
        ALTER TABLE app.push_subscriptions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.push_subscriptions FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.push_subscriptions
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.push_subscriptions TO {role};

        -- N2 / Module 12: Dual GAAP & MACRS / Section 179 tax depreciation fields
        ALTER TABLE app.fixed_assets
          ADD COLUMN IF NOT EXISTS tax_method text NOT NULL DEFAULT 'macrs_gds_200db'
            CHECK (tax_method IN ('macrs_gds_200db', 'macrs_gds_150db', 'sl', 'section_179', 'bonus')),
          ADD COLUMN IF NOT EXISTS recovery_period integer NOT NULL DEFAULT 5
            CHECK (recovery_period IN (3, 5, 7, 10, 15, 20, 27, 39)),
          ADD COLUMN IF NOT EXISTS convention text NOT NULL DEFAULT 'half_year'
            CHECK (convention IN ('half_year', 'mid_quarter')),
          ADD COLUMN IF NOT EXISTS section_179_amount numeric(24,6) NOT NULL DEFAULT 0;

        -- N3 / Module 13: Multi-SKU landed cost proration
        ALTER TABLE app.p4_inventory_items
          ADD COLUMN IF NOT EXISTS unit_weight numeric(18,4) NOT NULL DEFAULT 1.0000,
          ADD COLUMN IF NOT EXISTS unit_volume numeric(18,4) NOT NULL DEFAULT 1.0000;

        CREATE TABLE IF NOT EXISTS app.landed_cost_allocations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          bill_reference text NOT NULL,
          cost_kind text NOT NULL DEFAULT 'freight'
            CHECK (cost_kind IN ('freight', 'customs', 'duty', 'insurance')),
          proration_basis text NOT NULL DEFAULT 'value'
            CHECK (proration_basis IN ('weight', 'volume', 'value', 'quantity')),
          total_landed_cost numeric(24,6) NOT NULL CHECK (total_landed_cost > 0),
          currency text NOT NULL DEFAULT 'USD',
          lines_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.landed_cost_allocations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.landed_cost_allocations FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.landed_cost_allocations
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.landed_cost_allocations TO {role};
    """)


def downgrade() -> None:
    raise RuntimeError("Financial history schema migrations do not support destructive downgrade.")
