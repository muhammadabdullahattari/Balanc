"""Phase A to D schema: rule dismissals, payment leases, document fingerprints, line evidence, POs, and collections."""

from alembic import op

revision = "0044"
down_revision = "0043"
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
        -- A3: Dismissed bank-rule correction suggestions
        CREATE TABLE IF NOT EXISTS app.dismissed_rule_suggestions (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          descriptor text NOT NULL,
          account_id uuid NOT NULL,
          dismissed_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, descriptor, account_id)
        );
        ALTER TABLE app.dismissed_rule_suggestions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.dismissed_rule_suggestions FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.dismissed_rule_suggestions
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, DELETE ON app.dismissed_rule_suggestions TO {role};

        -- A5: Pre-execution lease & settled_unposted state on supplier_payment_executions
        ALTER TABLE app.supplier_payment_executions
          ADD COLUMN IF NOT EXISTS execution_lease_token text,
          ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
        ALTER TABLE app.supplier_payment_executions
          DROP CONSTRAINT IF EXISTS supplier_payment_executions_state_check;
        ALTER TABLE app.supplier_payment_executions
          ADD CONSTRAINT supplier_payment_executions_state_check CHECK (
            state IN (
              'pending_approval',
              'first_approved',
              'executing',
              'settled_posted',
              'settled_unposted',
              'invalidated',
              'failed'
            )
          );

        -- B2 & B3: Cross-channel document fingerprints & structured extraction summary
        CREATE TABLE IF NOT EXISTS app.document_fingerprints (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          content_sha256 text NOT NULL,
          semantic_key text,
          doc_type text NOT NULL DEFAULT 'other',
          vendor_name text NOT NULL DEFAULT '',
          doc_number text NOT NULL DEFAULT '',
          doc_date date,
          currency text NOT NULL DEFAULT 'USD',
          total_amount numeric(24,6),
          first_evidence_id uuid,
          source_draft_id uuid,
          trade_document_id uuid,
          channels jsonb NOT NULL DEFAULT '[]'::jsonb,
          extracted_fields jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          overall_confidence numeric(6,4) NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, content_sha256)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS document_fingerprints_semantic_uidx
          ON app.document_fingerprints (business_id, semantic_key)
          WHERE semantic_key IS NOT NULL;
        ALTER TABLE app.document_fingerprints ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.document_fingerprints FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.document_fingerprints
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.document_fingerprints TO {role};

        -- B4: Immutable per-line evidence chain (journal_line -> document -> region -> model -> approver)
        CREATE TABLE IF NOT EXISTS app.journal_line_evidence (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          journal_id uuid NOT NULL,
          journal_line_id uuid NOT NULL,
          evidence_id uuid NOT NULL,
          page integer NOT NULL DEFAULT 1 CHECK (page >= 1),
          region jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          model_version text NOT NULL DEFAULT '',
          extraction_field text NOT NULL DEFAULT '',
          approved_by uuid NOT NULL REFERENCES app.users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (business_id, journal_id) REFERENCES app.journals(business_id, id)
        );
        CREATE INDEX IF NOT EXISTS journal_line_evidence_lookup_idx
          ON app.journal_line_evidence (business_id, journal_id, journal_line_id);
        ALTER TABLE app.journal_line_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.journal_line_evidence FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.journal_line_evidence
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT ON app.journal_line_evidence TO {role};
        DROP TRIGGER IF EXISTS immutable ON app.journal_line_evidence;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.journal_line_evidence
          FOR EACH ROW EXECUTE FUNCTION app.immutable();

        -- C1: Purchase Orders & automated PO price-variance approval gate
        CREATE TABLE IF NOT EXISTS app.purchase_orders (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          number text NOT NULL,
          party_id uuid,
          supplier_name text NOT NULL,
          issue_date date NOT NULL,
          expected_date date,
          currency text NOT NULL DEFAULT 'USD',
          subtotal numeric(24,6) NOT NULL CHECK (subtotal >= 0),
          tax_total numeric(24,6) NOT NULL DEFAULT 0 CHECK (tax_total >= 0),
          total numeric(24,6) NOT NULL CHECK (total > 0),
          variance_tolerance_pct numeric(8,4) NOT NULL DEFAULT 5.0000 CHECK (variance_tolerance_pct >= 0),
          status text NOT NULL DEFAULT 'approved' CHECK (
            status IN ('draft', 'approved', 'partially_matched', 'matched', 'closed', 'cancelled')
          ),
          lines jsonb NOT NULL DEFAULT '[]'::jsonb,
          notes text NOT NULL DEFAULT '',
          approved_by uuid REFERENCES app.users(id),
          created_by uuid NOT NULL REFERENCES app.users(id),
          version integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, number)
        );
        ALTER TABLE app.purchase_orders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.purchase_orders FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.purchase_orders
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.purchase_orders TO {role};

        CREATE TABLE IF NOT EXISTS app.purchase_order_matches (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          purchase_order_id uuid NOT NULL REFERENCES app.purchase_orders(id),
          bill_id uuid,
          bill_reference text NOT NULL,
          invoiced_amount numeric(24,6) NOT NULL CHECK (invoiced_amount > 0),
          po_amount numeric(24,6) NOT NULL CHECK (po_amount > 0),
          variance_amount numeric(24,6) NOT NULL,
          variance_pct numeric(10,4) NOT NULL,
          status text NOT NULL CHECK (
            status IN ('matched_within_tolerance', 'pending_owner_approval', 'owner_approved', 'rejected')
          ),
          approval_reason text NOT NULL DEFAULT '',
          decided_by uuid REFERENCES app.users(id),
          decided_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS po_matches_business_status_idx
          ON app.purchase_order_matches (business_id, status, created_at DESC);
        ALTER TABLE app.purchase_order_matches ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.purchase_order_matches FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.purchase_order_matches
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.purchase_order_matches TO {role};

        -- C2: Business tone profiles & 60-day overdue phone-call escalation tasks
        CREATE TABLE IF NOT EXISTS app.collection_profiles (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id) UNIQUE,
          tone text NOT NULL DEFAULT 'friendly' CHECK (tone IN ('friendly', 'neutral', 'firm')),
          sample_phrases text NOT NULL DEFAULT '',
          signature_name text NOT NULL DEFAULT '',
          phone_escalation_days integer NOT NULL DEFAULT 60 CHECK (phone_escalation_days BETWEEN 15 AND 365),
          escalation_ladder jsonb NOT NULL DEFAULT '[]'::jsonb,
          updated_by uuid NOT NULL REFERENCES app.users(id),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.collection_profiles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.collection_profiles FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.collection_profiles
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.collection_profiles TO {role};

        CREATE TABLE IF NOT EXISTS app.collection_tasks (
          id uuid PRIMARY KEY,
          business_id uuid NOT NULL REFERENCES app.businesses(id),
          invoice_id uuid,
          invoice_number text NOT NULL,
          customer_name text NOT NULL,
          amount_due numeric(24,6) NOT NULL,
          currency text NOT NULL DEFAULT 'USD',
          days_overdue integer NOT NULL CHECK (days_overdue >= 0),
          task_kind text NOT NULL DEFAULT 'phone_call' CHECK (task_kind IN ('phone_call', 'personal_visit', 'legal_review')),
          status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'snoozed')),
          assigned_to uuid REFERENCES app.users(id),
          outcome_notes text NOT NULL DEFAULT '',
          completed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (business_id, invoice_number, task_kind)
        );
        ALTER TABLE app.collection_tasks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.collection_tasks FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.collection_tasks
          USING (business_id = app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id = app.scope() AND app.has_access(business_id));
        GRANT SELECT, INSERT, UPDATE ON app.collection_tasks TO {role};
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS app.collection_tasks;
        DROP TABLE IF EXISTS app.collection_profiles;
        DROP TABLE IF EXISTS app.purchase_order_matches;
        DROP TABLE IF EXISTS app.purchase_orders;
        DROP TRIGGER IF EXISTS immutable ON app.journal_line_evidence;
        DROP TABLE IF EXISTS app.journal_line_evidence;
        DROP TABLE IF EXISTS app.document_fingerprints;
        DROP TABLE IF EXISTS app.dismissed_rule_suggestions;
        ALTER TABLE app.supplier_payment_executions
          DROP CONSTRAINT IF EXISTS supplier_payment_executions_state_check;
        UPDATE app.supplier_payment_executions
          SET state = 'pending_approval'
          WHERE state IN ('executing', 'settled_unposted');
        ALTER TABLE app.supplier_payment_executions
          ADD CONSTRAINT supplier_payment_executions_state_check CHECK (
            state IN ('pending_approval', 'first_approved', 'settled_posted', 'invalidated', 'failed')
          );
        ALTER TABLE app.supplier_payment_executions
          DROP COLUMN IF EXISTS lease_expires_at,
          DROP COLUMN IF EXISTS execution_lease_token;
    """)
