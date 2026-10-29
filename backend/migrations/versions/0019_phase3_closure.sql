ALTER TABLE app.opening_items ADD CONSTRAINT opening_tenant_id UNIQUE(business_id,id);
ALTER TABLE app.privacy_requests ADD CONSTRAINT privacy_tenant_id UNIQUE(business_id,id);
CREATE TABLE app.document_identities (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),evidence_id uuid NOT NULL,
 supplier varchar(160) NOT NULL,normalized_supplier varchar(320) NOT NULL,document_date date NOT NULL,
 total numeric(24,6) NOT NULL CHECK(total>0),currency varchar(3) NOT NULL,
 version integer NOT NULL CHECK(version BETWEEN 1 AND 100),reason varchar(1000) NOT NULL,
 reviewed_by uuid NOT NULL REFERENCES app.users(id),UNIQUE(business_id,evidence_id,version),
 FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
CREATE INDEX identity_match ON app.document_identities(business_id,normalized_supplier,document_date,total);
CREATE TABLE app.export_snapshots (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),key varchar(100) NOT NULL,
 request_hash varchar(64) NOT NULL,format varchar(20) NOT NULL CHECK(format IN ('journals_csv','evidence_zip')),
 created_by uuid NOT NULL REFERENCES app.users(id),business_version integer NOT NULL,
 digest varchar(64) NOT NULL,size integer NOT NULL CHECK(size BETWEEN 1 AND 10485760),
 payload bytea NOT NULL CHECK(octet_length(payload)<=14000000),expires_at timestamptz NOT NULL,
 UNIQUE(business_id,created_by,key)
);
CREATE INDEX export_expiry ON app.export_snapshots(business_id,created_by,expires_at);
CREATE TABLE app.privacy_reviews (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),request_id uuid NOT NULL,
 outcome varchar(30) NOT NULL CHECK(outcome IN ('export_ready','retained','requires_followup')),
 reason varchar(1000) NOT NULL,policy_reference varchar(200) NOT NULL,
 retained_categories jsonb NOT NULL,retain_until date,legal_hold boolean NOT NULL,
 reviewed_by uuid NOT NULL REFERENCES app.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(business_id,request_id) REFERENCES app.privacy_requests(business_id,id)
);
CREATE INDEX privacy_review_request ON app.privacy_reviews(business_id,request_id,created_at,id);
CREATE TABLE app.opening_corrections (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),opening_id uuid NOT NULL,
 accounting_date date NOT NULL,delta numeric(24,6) NOT NULL CHECK(delta<>0),document_id uuid,
 loan_movement_id uuid,journal_id uuid NOT NULL,reason varchar(1000) NOT NULL,
 reviewed_by uuid NOT NULL REFERENCES app.users(id),
 CHECK((document_id IS NULL)<>(loan_movement_id IS NULL)),UNIQUE(business_id,journal_id),
 FOREIGN KEY(business_id,opening_id) REFERENCES app.opening_items(business_id,id),
 FOREIGN KEY(business_id,document_id) REFERENCES app.trade_documents(business_id,id),
 FOREIGN KEY(business_id,loan_movement_id) REFERENCES app.loan_movements(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE INDEX opening_correction_source ON app.opening_corrections(business_id,opening_id,accounting_date);
CREATE TABLE app.opening_cash_policies (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),opening_id uuid NOT NULL,
 treatment varchar(30) NOT NULL CHECK(treatment IN ('on_settlement','previously_recognized')),
 tax_treatment varchar(30) NOT NULL CHECK(tax_treatment IN ('no_tax_component','separately_accounted')),
 components jsonb NOT NULL,reason varchar(1000) NOT NULL,evidence_id uuid NOT NULL,
 reviewed_by uuid NOT NULL REFERENCES app.users(id),UNIQUE(business_id,opening_id),
 FOREIGN KEY(business_id,opening_id) REFERENCES app.opening_items(business_id,id),
 FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['document_identities','export_snapshots','privacy_reviews',
                         'opening_corrections','opening_cash_policies'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant ON app.%I USING(business_id=app.scope() AND app.has_access(business_id))
   WITH CHECK(business_id=app.scope() AND app.has_access(business_id))',t);
  EXECUTE format('GRANT SELECT,INSERT ON app.%I TO __RUNTIME__',t);
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%I
    FOR EACH ROW EXECUTE FUNCTION app.immutable()',t);
 END LOOP;
END $$;
CREATE POLICY identity_writer ON app.document_identities AS RESTRICTIVE FOR INSERT
 WITH CHECK(reviewed_by=app.actor() AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper','reviewer']));
CREATE POLICY export_owner ON app.export_snapshots AS RESTRICTIVE
 USING(created_by=app.actor()) WITH CHECK(created_by=app.actor());
CREATE POLICY privacy_reader ON app.privacy_reviews AS RESTRICTIVE FOR SELECT
 USING(app.has_role(business_id,ARRAY['owner','accountant']) OR EXISTS(
  SELECT 1 FROM app.privacy_requests p WHERE p.business_id=privacy_reviews.business_id
  AND p.id=request_id AND p.user_id=app.actor()));
CREATE POLICY privacy_writer ON app.privacy_reviews AS RESTRICTIVE FOR INSERT
 WITH CHECK(reviewed_by=app.actor() AND app.has_role(business_id,ARRAY['owner','accountant'])
 AND EXISTS(SELECT 1 FROM app.privacy_requests p WHERE p.business_id=privacy_reviews.business_id
 AND p.id=request_id AND p.user_id<>app.actor()));
CREATE POLICY opening_writer ON app.opening_corrections AS RESTRICTIVE FOR INSERT
 WITH CHECK(reviewed_by=app.actor() AND app.has_role(business_id,ARRAY['accountant']));
CREATE POLICY cash_writer ON app.opening_cash_policies AS RESTRICTIVE FOR INSERT
 WITH CHECK(reviewed_by=app.actor() AND app.has_role(business_id,ARRAY['accountant']));
ALTER TABLE app.loan_movements DROP CONSTRAINT loan_movements_kind_check;
ALTER TABLE app.loan_movements ADD CONSTRAINT loan_movements_kind_check
 CHECK(kind IN ('drawdown','repayment','opening','opening_increase','opening_decrease'));
DROP POLICY self ON app.privacy_requests;
CREATE POLICY request_read ON app.privacy_requests AS RESTRICTIVE FOR SELECT
 USING(user_id=app.actor() OR app.has_role(business_id,ARRAY['owner','accountant']));
CREATE POLICY request_insert ON app.privacy_requests AS RESTRICTIVE FOR INSERT
 WITH CHECK(user_id=app.actor());
CREATE FUNCTION app.expired_export_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_OP<>'DELETE' OR OLD.expires_at>now() OR OLD.created_by<>app.actor()
 OR OLD.business_id<>app.scope() THEN
  RAISE check_violation USING MESSAGE='Only expired owned export artifacts may be removed';
 END IF;
 RETURN OLD;
END $$;
DROP TRIGGER immutable ON app.export_snapshots;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.export_snapshots
 FOR EACH ROW EXECUTE FUNCTION app.expired_export_only();
GRANT DELETE ON app.export_snapshots TO __RUNTIME__;
CREATE POLICY expired_export ON app.export_snapshots AS RESTRICTIVE FOR DELETE USING(expires_at<=now());
CREATE OR REPLACE FUNCTION app.opening_source_exists() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_TABLE_NAME='trade_documents' THEN
  IF (to_jsonb(NEW)->>'opening_source_date') IS NOT NULL AND NOT (
   EXISTS(SELECT 1 FROM app.opening_items i WHERE i.business_id=NEW.business_id AND i.document_id=NEW.id
    AND i.original_date=(to_jsonb(NEW)->>'opening_source_date')::date
    AND i.due_date=NEW.due_date AND i.cutover_date=NEW.accounting_date
    AND i.amount=NEW.total AND i.party_id=NEW.party_id)
   OR EXISTS(SELECT 1 FROM app.opening_corrections c JOIN app.opening_items i
    ON i.business_id=c.business_id AND i.id=c.opening_id
    WHERE c.business_id=NEW.business_id AND c.document_id=NEW.id AND c.delta=NEW.total
    AND c.accounting_date=NEW.accounting_date AND i.party_id=NEW.party_id
    AND i.original_date=(to_jsonb(NEW)->>'opening_source_date')::date AND i.due_date=NEW.due_date)
  ) THEN RAISE check_violation USING MESSAGE='Reviewed opening source required'; END IF;
 ELSIF NEW.kind='opening' AND NOT EXISTS(
  SELECT 1 FROM app.opening_items i WHERE i.business_id=NEW.business_id AND i.loan_movement_id=NEW.id
  AND i.cutover_date=NEW.accounting_date AND i.amount=NEW.principal
 ) THEN RAISE check_violation USING MESSAGE='Reviewed opening principal required'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION app.source_link_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j app.journals; o app.operations; BEGIN
 SELECT * INTO j FROM app.journals WHERE business_id=NEW.business_id AND id=NEW.journal_id;
 SELECT * INTO o FROM app.operations WHERE business_id=NEW.business_id AND journal_id=NEW.journal_id;
 IF j.id IS NULL OR j.status<>'posted' OR j.source_kind<>'business_operation' OR o.id IS NULL
 OR (o.result->>'id') IS DISTINCT FROM NEW.id::text THEN
  RAISE check_violation USING MESSAGE='Financial source must be linked to its own posting operation';
 END IF;
 IF o.action='opening.corrected' THEN
  IF NOT EXISTS(SELECT 1 FROM app.opening_corrections i WHERE i.business_id=NEW.business_id
   AND i.reviewed_by=j.approved_by AND i.journal_id=j.id AND
   ((TG_TABLE_NAME='trade_documents' AND i.document_id=NEW.id)
    OR (TG_TABLE_NAME='loan_movements' AND i.loan_movement_id=NEW.id))) THEN
   RAISE check_violation USING MESSAGE='Accountant-reviewed opening correction required';
  END IF;
  RETURN NULL;
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
