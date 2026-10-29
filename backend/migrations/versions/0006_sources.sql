ALTER TABLE app.audit_events ADD COLUMN details jsonb NOT NULL DEFAULT '{}';
CREATE TABLE app.parties (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 name varchar(160) NOT NULL, email varchar(254), roles jsonb NOT NULL,
 active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 0,
 UNIQUE(business_id,id), CHECK(jsonb_typeof(roles)='array' AND jsonb_array_length(roles) BETWEEN 1 AND 4),
 CHECK(roles <@ '["customer","supplier","lender","borrower"]'::jsonb)
);
CREATE TABLE app.operations (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 key varchar(100) NOT NULL, request_hash varchar(64) NOT NULL, action varchar(60) NOT NULL,
 journal_id uuid, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(business_id,key), UNIQUE(business_id,id), UNIQUE(business_id,journal_id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE TABLE app.trade_documents (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 kind varchar(30) NOT NULL CHECK(kind IN ('invoice','bill','sales_receipt','customer_credit','vendor_credit')),
 number varchar(80) NOT NULL, party_id uuid NOT NULL,
 accounting_date date NOT NULL, due_date date NOT NULL CHECK(due_date>=accounting_date),
 service_start date, service_end date, control_account_id uuid NOT NULL, journal_id uuid NOT NULL,
 total numeric(24,6) NOT NULL CHECK(total>0), tax numeric(24,6) NOT NULL CHECK(tax>=0 AND tax<total),
 lines jsonb NOT NULL CHECK(jsonb_typeof(lines)='array'), original_id uuid,
 disputed boolean NOT NULL DEFAULT false, version integer NOT NULL DEFAULT 0,
 UNIQUE(business_id,id), UNIQUE(business_id,kind,number), UNIQUE(business_id,journal_id),
 CHECK((service_start IS NULL AND service_end IS NULL) OR service_start<=service_end),
 CHECK((kind IN ('customer_credit','vendor_credit'))=(original_id IS NOT NULL)),
 FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id),
 FOREIGN KEY(business_id,control_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id),
 FOREIGN KEY(business_id,original_id) REFERENCES app.trade_documents(business_id,id)
);
CREATE TABLE app.payments (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 kind varchar(20) NOT NULL CHECK(kind IN ('receipt','payment','customer_refund','vendor_refund')),
 party_id uuid NOT NULL, accounting_date date NOT NULL, bank_account_id uuid NOT NULL,
 control_account_id uuid NOT NULL, total numeric(24,6) NOT NULL CHECK(total>0),
 journal_id uuid NOT NULL, allocations jsonb NOT NULL, original_payment_id uuid,
 UNIQUE(business_id,id), UNIQUE(business_id,journal_id),
 FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id),
 FOREIGN KEY(business_id,bank_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,control_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id),
 FOREIGN KEY(business_id,original_payment_id) REFERENCES app.payments(business_id,id)
);
CREATE TABLE app.allocations (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 payment_id uuid NOT NULL, document_id uuid NOT NULL,
 amount numeric(24,6) NOT NULL CHECK(amount>0), accounting_date date NOT NULL,
 UNIQUE(payment_id,document_id),
 FOREIGN KEY(business_id,payment_id) REFERENCES app.payments(business_id,id),
 FOREIGN KEY(business_id,document_id) REFERENCES app.trade_documents(business_id,id)
);
CREATE TABLE app.loans (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), party_id uuid NOT NULL,
 direction varchar(10) NOT NULL CHECK(direction IN ('payable','receivable')),
 control_account_id uuid NOT NULL, name varchar(160) NOT NULL, schedule jsonb NOT NULL,
 UNIQUE(business_id,id), FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id),
 FOREIGN KEY(business_id,control_account_id) REFERENCES app.accounts(business_id,id)
);
CREATE TABLE app.loan_movements (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), loan_id uuid NOT NULL,
 accounting_date date NOT NULL, kind varchar(20) NOT NULL CHECK(kind IN ('drawdown','repayment')),
 principal numeric(24,6) NOT NULL CHECK(principal>=0),
 interest numeric(24,6) NOT NULL CHECK(interest>=0), fees numeric(24,6) NOT NULL CHECK(fees>=0),
 journal_id uuid NOT NULL, UNIQUE(business_id,journal_id),
 CHECK(principal+interest+fees>0), CHECK(kind='repayment' OR (interest=0 AND fees=0)),
 FOREIGN KEY(business_id,loan_id) REFERENCES app.loans(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE TABLE app.bank_imports (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), account_id uuid NOT NULL,
 file_hash varchar(64) NOT NULL, mapping jsonb NOT NULL, row_count integer NOT NULL CHECK(row_count BETWEEN 1 AND 500),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(business_id,id),
 UNIQUE(business_id,account_id,file_hash),
 FOREIGN KEY(business_id,account_id) REFERENCES app.accounts(business_id,id)
);
CREATE TABLE app.bank_rows (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), import_id uuid NOT NULL,
 account_id uuid NOT NULL, position integer NOT NULL, accounting_date date NOT NULL,
 description varchar(1000) NOT NULL, amount numeric(24,6) NOT NULL CHECK(amount<>0), raw jsonb NOT NULL,
 external_id varchar(160), state varchar(20) NOT NULL DEFAULT 'for_review'
 CHECK(state IN ('for_review','categorized','matched','excluded')), version integer NOT NULL DEFAULT 0,
 journal_id uuid, reason varchar(1000), peer_id uuid,
 UNIQUE(business_id,id), UNIQUE(import_id,position), UNIQUE(business_id,account_id,external_id),
 UNIQUE(business_id,account_id,journal_id),
 CHECK((state IN ('categorized','matched'))=(journal_id IS NOT NULL)),
 CHECK(state='for_review' OR length(reason)>0),
 FOREIGN KEY(business_id,import_id) REFERENCES app.bank_imports(business_id,id),
 FOREIGN KEY(business_id,account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id),
 FOREIGN KEY(business_id,peer_id) REFERENCES app.bank_rows(business_id,id)
);
CREATE TABLE app.bank_rules (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), name varchar(160) NOT NULL,
 contains varchar(120) NOT NULL, account_id uuid NOT NULL, priority integer NOT NULL,
 version integer NOT NULL DEFAULT 0, active boolean NOT NULL DEFAULT false,
 preview_hash varchar(64), history jsonb NOT NULL DEFAULT '[]',
 FOREIGN KEY(business_id,account_id) REFERENCES app.accounts(business_id,id)
);
-- Evidence reference is added after private document metadata in the next migration.
CREATE TABLE app.statements (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), account_id uuid NOT NULL,
 as_of date NOT NULL, closing_balance numeric(24,6) NOT NULL, outstanding jsonb NOT NULL,
 ledger_balance numeric(24,6) NOT NULL, evidence_id uuid NOT NULL, reviewer_id uuid NOT NULL REFERENCES app.users(id),
 UNIQUE(business_id,account_id,as_of), FOREIGN KEY(business_id,account_id) REFERENCES app.accounts(business_id,id)
);
CREATE INDEX trade_open ON app.trade_documents(business_id,party_id,accounting_date,id);
CREATE INDEX allocations_asof ON app.allocations(business_id,accounting_date,document_id);
CREATE INDEX loan_asof ON app.loan_movements(business_id,loan_id,accounting_date);
CREATE INDEX bank_review ON app.bank_rows(business_id,state,accounting_date,id);

CREATE FUNCTION app.source_provenance() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE source uuid; BEGIN
 source:=nullif(current_setting('app.source_id',true),'')::uuid;
 IF NEW.status='posted' AND source IS NOT NULL THEN
  NEW.source_id:=source; NEW.source_kind:='business_operation';
 END IF;
 IF NEW.reversal_of IS NOT NULL AND EXISTS(SELECT 1 FROM app.journals
  WHERE id=NEW.reversal_of AND business_id=NEW.business_id AND source_kind='business_operation') THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Correct a source transaction through its subledger';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_source_provenance BEFORE INSERT OR UPDATE ON app.journals
FOR EACH ROW EXECUTE FUNCTION app.source_provenance();
CREATE FUNCTION app.source_exists() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.source_kind='business_operation' AND NOT EXISTS(SELECT 1 FROM app.operations
 WHERE id=NEW.source_id AND business_id=NEW.business_id AND journal_id=NEW.id) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Source operation must commit with its journal';
 END IF; RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER source_exists AFTER INSERT OR UPDATE ON app.journals
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.source_exists();

CREATE FUNCTION app.source_edit_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_TABLE_NAME='trade_documents' THEN
  IF (to_jsonb(NEW)-'version'-'disputed')<>(to_jsonb(OLD)-'version'-'disputed') THEN
   RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Issued documents are immutable';
  END IF;
 ELSIF TG_TABLE_NAME='bank_rows' THEN
  IF OLD.state<>'for_review' OR
   (to_jsonb(NEW)-'version'-'state'-'reason'-'journal_id'-'peer_id')<>
   (to_jsonb(OLD)-'version'-'state'-'reason'-'journal_id'-'peer_id') THEN
   RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Bank source rows and dispositions are immutable';
  END IF;
 END IF;
 IF NEW.version<>OLD.version+1 THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='ERR_VERSION';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app.can_post(b uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.memberships WHERE business_id=b AND user_id=app.actor()
 AND active AND (role='accountant' OR can_post))
$$;
REVOKE ALL ON FUNCTION app.can_post(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.can_post(uuid) TO __RUNTIME__;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['parties','operations','trade_documents','payments','allocations','loans',
  'loan_movements','bank_imports','bank_rows','bank_rules','statements'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant ON app.%I USING(business_id=app.scope() AND app.has_access(business_id)) WITH CHECK(business_id=app.scope() AND app.has_access(business_id))',t);
  EXECUTE format('CREATE POLICY source_writer ON app.%I AS RESTRICTIVE FOR INSERT WITH CHECK(app.has_role(business_id,ARRAY[''owner'',''accountant'',''bookkeeper'']) OR app.can_post(business_id))',t);
  EXECUTE format('GRANT SELECT,INSERT ON app.%I TO __RUNTIME__',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['operations','trade_documents','payments','allocations','loans','loan_movements','statements'] LOOP
  EXECUTE format('CREATE POLICY posting_writer ON app.%I AS RESTRICTIVE FOR INSERT WITH CHECK(app.can_post(business_id))',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['operations','payments','allocations','loans','loan_movements','bank_imports','statements'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.immutable()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['parties','trade_documents','bank_rows','bank_rules'] LOOP
  EXECUTE format('GRANT UPDATE ON app.%I TO __RUNTIME__',t);
  EXECUTE format('CREATE POLICY source_update ON app.%I AS RESTRICTIVE FOR UPDATE USING(app.has_role(business_id,ARRAY[''owner'',''accountant'',''bookkeeper'']) OR app.can_post(business_id))',t);
  EXECUTE format('CREATE TRIGGER edit_guard BEFORE UPDATE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.source_edit_guard()',t);
 END LOOP;
END $$;
