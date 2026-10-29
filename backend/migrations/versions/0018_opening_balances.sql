ALTER TABLE app.trade_documents ADD COLUMN opening_source_date date;
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='app.trade_documents'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%due_date >= accounting_date%'
 LOOP EXECUTE format('ALTER TABLE app.trade_documents DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE app.trade_documents ADD CONSTRAINT source_due_date CHECK(
 (opening_source_date IS NULL AND due_date>=accounting_date) OR
 (opening_source_date IS NOT NULL AND opening_source_date<=accounting_date
  AND due_date>=opening_source_date AND kind IN ('invoice','bill'))
);
ALTER TABLE app.import_batches DROP CONSTRAINT import_batches_mode_check;
ALTER TABLE app.import_batches ADD CONSTRAINT import_batches_mode_check
 CHECK(mode IN ('chart','journal','trade','opening'));
CREATE UNIQUE INDEX opening_batch_once ON app.import_batches(business_id) WHERE mode='opening';
ALTER TABLE app.loan_movements ADD CONSTRAINT loan_movement_tenant_id UNIQUE(business_id,id);
ALTER TABLE app.loan_movements DROP CONSTRAINT loan_movements_kind_check;
ALTER TABLE app.loan_movements ADD CONSTRAINT loan_movements_kind_check
 CHECK(kind IN ('drawdown','repayment','opening'));
CREATE TABLE app.opening_items (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),
 kind varchar(20) NOT NULL CHECK(kind IN ('customer','supplier','loan_payable','loan_receivable')),
 party_id uuid NOT NULL,reference varchar(80) NOT NULL,original_date date NOT NULL,
 due_date date NOT NULL,cutover_date date NOT NULL,amount numeric(24,6) NOT NULL CHECK(amount>0),
 equity_account_id uuid NOT NULL,document_id uuid,loan_movement_id uuid,
 reviewed_by uuid NOT NULL REFERENCES app.users(id),review_reason varchar(1000) NOT NULL,
 UNIQUE(business_id,kind,party_id,reference),
 CHECK(original_date<=cutover_date AND due_date>=original_date),
 CHECK((kind IN ('customer','supplier') AND document_id IS NOT NULL AND loan_movement_id IS NULL)
 OR (kind IN ('loan_payable','loan_receivable') AND document_id IS NULL AND loan_movement_id IS NOT NULL)),
 FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id),
 FOREIGN KEY(business_id,equity_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,document_id) REFERENCES app.trade_documents(business_id,id),
 FOREIGN KEY(business_id,loan_movement_id) REFERENCES app.loan_movements(business_id,id)
);
ALTER TABLE app.opening_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.opening_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.opening_items USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.opening_items AS RESTRICTIVE FOR INSERT
 WITH CHECK(app.has_role(business_id,ARRAY['accountant']) AND reviewed_by=app.actor());
GRANT SELECT,INSERT ON app.opening_items TO __RUNTIME__;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.opening_items
 FOR EACH ROW EXECUTE FUNCTION app.immutable();
CREATE UNIQUE INDEX opening_document ON app.opening_items(business_id,document_id)
 WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX opening_loan ON app.opening_items(business_id,loan_movement_id)
 WHERE loan_movement_id IS NOT NULL;
CREATE FUNCTION app.opening_source_exists() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_TABLE_NAME='trade_documents' THEN
  IF NEW.opening_source_date IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM app.opening_items i WHERE i.business_id=NEW.business_id AND i.document_id=NEW.id
   AND i.original_date=NEW.opening_source_date AND i.due_date=NEW.due_date
   AND i.cutover_date=NEW.accounting_date AND i.amount=NEW.total AND i.party_id=NEW.party_id
  ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Reviewed opening source required'; END IF;
 ELSIF NEW.kind='opening' AND NOT EXISTS(
  SELECT 1 FROM app.opening_items i WHERE i.business_id=NEW.business_id AND i.loan_movement_id=NEW.id
  AND i.cutover_date=NEW.accounting_date AND i.amount=NEW.principal
 ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Reviewed opening principal required'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER opening_source_exists AFTER INSERT ON app.trade_documents
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.opening_source_exists();
CREATE CONSTRAINT TRIGGER opening_source_exists AFTER INSERT ON app.loan_movements
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.opening_source_exists();
CREATE OR REPLACE FUNCTION app.source_link_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j app.journals; o app.operations; BEGIN
 SELECT * INTO j FROM app.journals WHERE business_id=NEW.business_id AND id=NEW.journal_id;
 SELECT * INTO o FROM app.operations WHERE business_id=NEW.business_id AND journal_id=NEW.journal_id;
 IF j.id IS NULL OR j.status<>'posted' OR j.source_kind<>'business_operation' OR o.id IS NULL
 OR (o.result->>'id') IS DISTINCT FROM NEW.id::text THEN
  RAISE check_violation USING MESSAGE='Financial source must be linked to its own posting operation';
 END IF;
 IF o.action='migration.opening' THEN
  IF NOT EXISTS(SELECT 1 FROM app.opening_items i WHERE i.business_id=NEW.business_id
   AND i.reviewed_by=j.approved_by AND
   ((TG_TABLE_NAME='trade_documents' AND i.document_id=NEW.id)
    OR (TG_TABLE_NAME='loan_movements' AND i.loan_movement_id=NEW.id))) THEN
   RAISE check_violation USING MESSAGE='Accountant-reviewed opening linkage required';
  END IF;
  RETURN NULL;
 END IF;
 IF TG_TABLE_NAME='trade_documents' AND o.action<>'trade.issued' OR
    TG_TABLE_NAME='payments' AND o.action<>'payment.posted' OR
    TG_TABLE_NAME='advances' AND o.action<>'advance.received' OR
    TG_TABLE_NAME='loan_movements' AND o.action<>'loan.'||(to_jsonb(NEW)->>'loan_id')||'.movement' THEN
  RAISE check_violation USING MESSAGE='Incompatible source operation';
 END IF;
 RETURN NULL;
END $$;
