"""Tenant projects, posted line tags and billable draft reservations."""

from alembic import op
from sqlalchemy import text

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
    ALTER TABLE app.journal_lines ADD UNIQUE (business_id,id);
    ALTER TABLE app.source_drafts ADD UNIQUE (business_id,id);
    CREATE TABLE app.projects (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      business_id uuid NOT NULL REFERENCES app.businesses(id),
      name varchar(120) NOT NULL CHECK (length(trim(name)) > 0),
      customer_id uuid NOT NULL,
      UNIQUE (business_id,id), UNIQUE (business_id,name),
      FOREIGN KEY (business_id,customer_id) REFERENCES app.parties(business_id,id)
    );
    CREATE TABLE app.project_line_tags (
      business_id uuid NOT NULL,
      line_id uuid NOT NULL,
      project_id uuid NOT NULL,
      location varchar(120),
      billable boolean NOT NULL,
      PRIMARY KEY (business_id,line_id),
      FOREIGN KEY (business_id,line_id) REFERENCES app.journal_lines(business_id,id),
      FOREIGN KEY (business_id,project_id) REFERENCES app.projects(business_id,id)
    );
    CREATE INDEX project_line_tags_project ON app.project_line_tags(business_id,project_id,line_id);
    CREATE TABLE app.billable_allocations (
      business_id uuid NOT NULL,
      source_line_id uuid NOT NULL,
      draft_id uuid PRIMARY KEY,
      invoice_line_id uuid UNIQUE,
      amount numeric(24,6) NOT NULL CHECK (amount>0),
      state varchar(12) NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved','billed','released')),
      CHECK ((state='billed')=(invoice_line_id IS NOT NULL)),
      FOREIGN KEY (business_id,source_line_id)
        REFERENCES app.project_line_tags(business_id,line_id),
      FOREIGN KEY (business_id,draft_id) REFERENCES app.source_drafts(business_id,id),
      FOREIGN KEY (business_id,invoice_line_id) REFERENCES app.journal_lines(business_id,id)
    );
    CREATE UNIQUE INDEX billable_source_active
      ON app.billable_allocations(business_id,source_line_id) WHERE state<>'released';
    DO $$ DECLARE t text; BEGIN
      FOREACH t IN ARRAY ARRAY['projects','project_line_tags','billable_allocations'] LOOP
        EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scope ON app.%I USING
          (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND
          app.has_role(business_id,ARRAY[''owner'',''accountant'',''bookkeeper'']))',t);
      END LOOP;
    END $$;
    CREATE FUNCTION app.guard_billable_allocation() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.state<>'reserved' OR NOT EXISTS (
          SELECT 1 FROM app.project_line_tags t
          JOIN app.journal_lines l ON l.business_id=t.business_id AND l.id=t.line_id
          JOIN app.accounts a ON a.business_id=l.business_id AND a.id=l.account_id
          JOIN app.source_drafts d ON d.business_id=t.business_id AND d.id=NEW.draft_id
          WHERE t.business_id=NEW.business_id AND t.line_id=NEW.source_line_id AND t.billable
            AND a.kind='expense' AND l.debit=NEW.amount AND d.status='draft'
        ) THEN RAISE check_violation USING MESSAGE='Invalid billable source'; END IF;
      ELSIF OLD.state<>'reserved' OR NEW.state NOT IN ('billed','released')
        OR NOT EXISTS (
          SELECT 1 FROM app.source_drafts d
          LEFT JOIN app.trade_documents doc ON doc.business_id=d.business_id
            AND doc.id=d.document_id
          LEFT JOIN app.journal_lines l ON l.business_id=doc.business_id
            AND l.journal_id=doc.journal_id AND l.id=NEW.invoice_line_id
          WHERE d.business_id=NEW.business_id AND d.id=NEW.draft_id
            AND ((NEW.state='released' AND d.status='void') OR
              (NEW.state='billed' AND d.status='issued' AND doc.kind='invoice'
                AND l.credit=NEW.amount AND app.can_post(NEW.business_id)))
        )
      THEN RAISE check_violation USING MESSAGE='Invalid billable allocation transition'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER billable_allocation_guard BEFORE INSERT OR UPDATE ON app.billable_allocations
      FOR EACH ROW EXECUTE FUNCTION app.guard_billable_allocation();
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(
        text(
            "GRANT SELECT,INSERT ON app.projects, app.project_line_tags, "
            f"app.billable_allocations TO {role}"
        )
    )
    op.execute(text(f"GRANT UPDATE(state,invoice_line_id) ON app.billable_allocations TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve project and billing history; use a forward repair")
