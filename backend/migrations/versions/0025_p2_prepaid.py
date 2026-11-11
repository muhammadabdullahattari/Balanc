"""Add prepaid schedules and bind recognition to posted journal entries."""

from alembic import op
from sqlalchemy import text

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
    ALTER TABLE app.close_tasks ADD COLUMN review_comment varchar(2000) NOT NULL DEFAULT '';
    CREATE TABLE app.prepaid_schedules (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      business_id uuid NOT NULL REFERENCES app.businesses(id),
      source_line_id uuid NOT NULL,
      expense_account_id uuid NOT NULL,
      prepared_by uuid NOT NULL REFERENCES app.users(id),
      description varchar(200) NOT NULL,
      UNIQUE(business_id,id), UNIQUE(business_id,source_line_id),
      FOREIGN KEY(business_id,source_line_id) REFERENCES app.journal_lines(business_id,id),
      FOREIGN KEY(business_id,expense_account_id) REFERENCES app.accounts(business_id,id)
    );
    CREATE TABLE app.prepaid_installments (
      business_id uuid NOT NULL,
      schedule_id uuid NOT NULL,
      due_date date NOT NULL,
      amount numeric(24,6) NOT NULL CHECK(amount>0),
      journal_id uuid UNIQUE,
      PRIMARY KEY(business_id,schedule_id,due_date),
      FOREIGN KEY(business_id,schedule_id) REFERENCES app.prepaid_schedules(business_id,id),
      FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
    );
    DO $$ DECLARE t text; BEGIN
      FOREACH t IN ARRAY ARRAY['prepaid_schedules','prepaid_installments'] LOOP
        EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY prepaid_scope ON app.%I USING
          (business_id=app.scope() AND app.has_access(business_id))
          WITH CHECK (business_id=app.scope() AND
          app.has_role(business_id,ARRAY[''owner'',''accountant'',''bookkeeper'']))',t);
      END LOOP;
    END $$;
    CREATE FUNCTION app.prepaid_schedule_balance() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ DECLARE sid uuid; original numeric; allocated numeric;
    BEGIN
      sid := CASE WHEN TG_TABLE_NAME='prepaid_schedules' THEN
        (to_jsonb(NEW)->>'id')::uuid ELSE (to_jsonb(NEW)->>'schedule_id')::uuid END;
      SELECT l.debit INTO original FROM app.prepaid_schedules s
      JOIN app.journal_lines l ON l.business_id=s.business_id AND l.id=s.source_line_id
      JOIN app.journals j ON j.business_id=l.business_id AND j.id=l.journal_id
      JOIN app.accounts a ON a.business_id=l.business_id AND a.id=l.account_id
      WHERE s.business_id=NEW.business_id AND s.id=sid AND l.debit>0
        AND a.kind='asset' AND a.control IS NULL AND j.status='posted'
        AND j.source_kind='manual_journal' AND j.reversal_of IS NULL
        AND s.prepared_by=app.actor()
        AND NOT EXISTS (SELECT 1 FROM app.journals r WHERE r.business_id=j.business_id
          AND r.reversal_of=j.id);
      SELECT sum(amount) INTO allocated FROM app.prepaid_installments
        WHERE business_id=NEW.business_id AND schedule_id=sid;
      IF original IS NULL OR allocated IS DISTINCT FROM original
      THEN RAISE check_violation USING MESSAGE='Prepaid installments must equal source debit';
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER prepaid_source_balance AFTER INSERT ON app.prepaid_schedules
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.prepaid_schedule_balance();
    CREATE CONSTRAINT TRIGGER prepaid_installment_balance AFTER INSERT ON app.prepaid_installments
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.prepaid_schedule_balance();
    CREATE FUNCTION app.prepaid_recognition_guard() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ BEGIN
      IF OLD.journal_id IS NOT NULL OR NEW.journal_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM app.prepaid_schedules s
        JOIN app.journal_lines source ON source.business_id=s.business_id
          AND source.id=s.source_line_id
        JOIN app.journals j ON j.business_id=s.business_id AND j.id=NEW.journal_id
        JOIN app.journal_lines debit ON debit.business_id=j.business_id
          AND debit.journal_id=j.id AND debit.account_id=s.expense_account_id
          AND debit.debit=NEW.amount
        JOIN app.journal_lines credit ON credit.business_id=j.business_id
          AND credit.journal_id=j.id AND credit.account_id=source.account_id
          AND credit.credit=NEW.amount
        WHERE s.business_id=NEW.business_id AND s.id=NEW.schedule_id
          AND j.status='posted' AND j.accounting_date=NEW.due_date
          AND j.posting_xid=pg_current_xact_id()
          AND (SELECT count(*) FROM app.journal_lines
            WHERE business_id=j.business_id AND journal_id=j.id)=2
          AND s.prepared_by<>app.actor() AND app.can_post(s.business_id)
      ) THEN RAISE check_violation USING MESSAGE='Invalid prepaid recognition'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER prepaid_recognition_guard BEFORE UPDATE ON app.prepaid_installments
      FOR EACH ROW EXECUTE FUNCTION app.prepaid_recognition_guard();
    CREATE FUNCTION app.prepaid_reversal_guard() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.reversal_of IS NOT NULL AND (
        EXISTS (SELECT 1 FROM app.prepaid_installments
          WHERE business_id=NEW.business_id AND journal_id=NEW.reversal_of)
        OR EXISTS (SELECT 1 FROM app.prepaid_schedules s JOIN app.journal_lines l
          ON l.business_id=s.business_id AND l.id=s.source_line_id
          WHERE s.business_id=NEW.business_id AND l.journal_id=NEW.reversal_of)
      ) THEN RAISE check_violation USING MESSAGE='ERR_PREPAID_REVERSAL'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER prepaid_reversal_guard BEFORE INSERT ON app.journals
      FOR EACH ROW EXECUTE FUNCTION app.prepaid_reversal_guard();
    CREATE FUNCTION app.prepaid_period_guard() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.locked AND NOT OLD.locked AND EXISTS (
        SELECT 1 FROM app.prepaid_installments i WHERE i.business_id=NEW.business_id
          AND i.due_date BETWEEN NEW.start_date AND NEW.end_date AND i.journal_id IS NULL
      ) THEN RAISE check_violation USING MESSAGE='ERR_PREPAID_PENDING'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER prepaid_period_guard BEFORE UPDATE OF locked ON app.periods
      FOR EACH ROW EXECUTE FUNCTION app.prepaid_period_guard();
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(
        text(f"GRANT SELECT,INSERT ON app.prepaid_schedules,app.prepaid_installments TO {role}")
    )
    op.execute(text(f"GRANT UPDATE(journal_id) ON app.prepaid_installments TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve prepaid recognition history; use a forward repair")
