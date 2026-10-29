ALTER TABLE app.periods ADD CONSTRAINT period_tenant_id UNIQUE(business_id,id);
ALTER TABLE app.policy_history ADD CONSTRAINT policy_tenant_id UNIQUE(business_id,id);
ALTER TABLE app.periods ADD COLUMN status varchar(20) NOT NULL DEFAULT 'open';
ALTER TABLE app.periods ADD COLUMN locked_by uuid REFERENCES app.users(id);
UPDATE app.periods SET status=CASE WHEN locked THEN 'locked' ELSE 'open' END;
ALTER TABLE app.periods ADD CHECK(status IN ('open','closing','locked'));
ALTER TABLE app.periods ADD CHECK((status='locked')=locked);
CREATE FUNCTION app.period_approval() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.locked THEN NEW.status:='locked'; NEW.locked_by:=app.actor();
 ELSIF TG_OP='UPDATE' AND OLD.locked THEN NEW.status:='open'; NEW.locked_by:=NULL;
 END IF;
 IF NEW.locked AND NEW.locked_by IS NULL THEN
  RAISE insufficient_privilege USING MESSAGE='A lock approver is required';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER period_approval BEFORE INSERT OR UPDATE ON app.periods
FOR EACH ROW EXECUTE FUNCTION app.period_approval();

-- Backfill immutable metadata once under migration control before restoring guards.
DROP TRIGGER journal_immutable ON app.journals;
ALTER TABLE app.journals ADD COLUMN operation_key uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE app.journals ADD CONSTRAINT journal_operation_key UNIQUE(business_id,operation_key);
ALTER TABLE app.journals ADD COLUMN status varchar(20) NOT NULL DEFAULT 'posted';
ALTER TABLE app.journals ADD COLUMN version integer NOT NULL DEFAULT 0;
ALTER TABLE app.journals ADD COLUMN period_id uuid;
ALTER TABLE app.journals ADD COLUMN policy_id uuid;
ALTER TABLE app.journals ADD COLUMN source_kind varchar(40) NOT NULL DEFAULT 'manual_journal';
ALTER TABLE app.journals ADD COLUMN source_id uuid;
ALTER TABLE app.journals ADD COLUMN approved_by uuid REFERENCES app.users(id);
ALTER TABLE app.journals ADD COLUMN posted_at timestamptz;
ALTER TABLE app.journals ADD COLUMN book varchar(20) NOT NULL DEFAULT 'local';
-- Migration owner is subject to RLS. Existing rows require an explicit per-tenant
-- backfill by a DBA; do not silently migrate historical rows invisible to this role.
-- This revision is introduced before any product ledger data in the Phase 2 release.
ALTER TABLE app.journals ADD CHECK(status IN ('draft','posted'));
ALTER TABLE app.journals ADD FOREIGN KEY(business_id,period_id) REFERENCES app.periods(business_id,id);
ALTER TABLE app.journals ADD FOREIGN KEY(business_id,policy_id) REFERENCES app.policy_history(business_id,id);
ALTER TABLE app.journals ADD CHECK(status='draft' OR (period_id IS NOT NULL AND policy_id IS NOT NULL
 AND source_id IS NOT NULL AND approved_by IS NOT NULL AND posted_at IS NOT NULL));

CREATE FUNCTION app.journal_header_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ DECLARE b app.businesses; BEGIN
 IF TG_OP='UPDATE' OR TG_OP='DELETE' THEN
  IF OLD.status='posted' THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted records are immutable';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF NEW.id<>OLD.id OR NEW.business_id<>OLD.business_id OR NEW.created_by<>OLD.created_by THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Draft identity cannot change';
  END IF;
 END IF;
 IF NEW.status='posted' THEN
  SELECT * INTO b FROM app.businesses WHERE id=NEW.business_id FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM app.memberships WHERE business_id=b.id
    AND user_id=app.actor() AND active AND (role='accountant' OR can_post)) THEN
   RAISE insufficient_privilege USING MESSAGE='Posting permission required';
  END IF;
  SELECT id INTO NEW.period_id FROM app.periods WHERE business_id=b.id AND NOT locked
   AND NEW.accounting_date BETWEEN start_date AND end_date;
  IF NEW.period_id IS NULL THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_PERIOD_LOCKED';
  END IF;
  SELECT id INTO NEW.policy_id FROM app.policy_history WHERE business_id=b.id
   AND effective_date<=NEW.accounting_date ORDER BY effective_date DESC,version DESC LIMIT 1;
  IF NEW.policy_id IS NULL THEN
   SELECT id INTO NEW.policy_id FROM app.policy_history WHERE business_id=b.id ORDER BY version LIMIT 1;
  END IF;
  IF NEW.currency<>b.currency THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Foreign currency is not enabled';
  END IF;
  IF NEW.reversal_of IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.journals WHERE id=NEW.reversal_of
   AND business_id=b.id AND status='posted') THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid reversal';
  END IF;
  NEW.source_id:=coalesce(NEW.reversal_of,NEW.id);
  NEW.source_kind:=CASE WHEN NEW.reversal_of IS NULL THEN 'manual_journal' ELSE 'reversal' END;
  NEW.approved_by:=app.actor(); NEW.posted_at:=now(); NEW.posting_xid:=pg_current_xact_id();
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER journal_header_guard BEFORE INSERT OR UPDATE OR DELETE ON app.journals
FOR EACH ROW EXECUTE FUNCTION app.journal_header_guard();
CREATE TRIGGER journal_no_truncate BEFORE TRUNCATE ON app.journals
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();

CREATE OR REPLACE FUNCTION app.check_balance() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ DECLARE n integer; d numeric; c numeric; BEGIN
 IF NEW.status<>'posted' THEN RETURN NULL; END IF;
 SELECT count(*),sum(debit),sum(credit) INTO n,d,c FROM app.journal_lines
 WHERE journal_id=NEW.id AND business_id=NEW.business_id;
 IF n<2 OR n>200 OR d<>c OR d IS NULL OR d=0 THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Journal must balance';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_update_balance AFTER UPDATE ON app.journals
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.check_balance();

DROP TRIGGER lines_immutable ON app.journal_lines;
CREATE FUNCTION app.draft_line_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ DECLARE parent app.journals; BEGIN
 SELECT * INTO parent FROM app.journals WHERE id=OLD.journal_id AND business_id=OLD.business_id FOR UPDATE;
 IF NOT FOUND OR parent.status<>'draft' THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted records are immutable';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.id<>OLD.id OR NEW.business_id<>OLD.business_id
   OR NEW.journal_id<>OLD.journal_id) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Draft line identity cannot change';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER draft_line_guard BEFORE UPDATE OR DELETE ON app.journal_lines
FOR EACH ROW EXECUTE FUNCTION app.draft_line_guard();
CREATE TRIGGER lines_no_truncate BEFORE TRUNCATE ON app.journal_lines
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();
CREATE OR REPLACE FUNCTION app.check_line_transaction() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ DECLARE parent app.journals; BEGIN
 SELECT * INTO parent FROM app.journals WHERE id=NEW.journal_id AND business_id=NEW.business_id FOR UPDATE;
 IF NOT FOUND OR (parent.status='posted' AND parent.posting_xid<>pg_current_xact_id()) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted records are immutable';
 END IF;
 RETURN NEW;
END $$;

GRANT INSERT,UPDATE,DELETE ON app.journals,app.journal_lines TO __RUNTIME__;
CREATE POLICY runtime_draft_insert ON app.journals AS RESTRICTIVE FOR INSERT TO __RUNTIME__
WITH CHECK(status='draft' AND created_by=app.actor() AND
 app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
CREATE POLICY runtime_draft_update ON app.journals AS RESTRICTIVE FOR UPDATE TO __RUNTIME__
USING(status='draft' AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']))
WITH CHECK(status='draft' AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
CREATE POLICY runtime_draft_delete ON app.journals AS RESTRICTIVE FOR DELETE TO __RUNTIME__
USING(status='draft' AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
CREATE POLICY runtime_line_insert ON app.journal_lines AS RESTRICTIVE FOR INSERT TO __RUNTIME__
WITH CHECK(EXISTS(SELECT 1 FROM app.journals j WHERE j.id=journal_id AND j.business_id=journal_lines.business_id
 AND j.status='draft') AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
CREATE POLICY runtime_line_update ON app.journal_lines AS RESTRICTIVE FOR UPDATE TO __RUNTIME__
USING(EXISTS(SELECT 1 FROM app.journals j WHERE j.id=journal_id AND j.business_id=journal_lines.business_id
 AND j.status='draft') AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']))
WITH CHECK(EXISTS(SELECT 1 FROM app.journals j WHERE j.id=journal_id AND j.business_id=journal_lines.business_id AND j.status='draft'));
CREATE POLICY runtime_line_delete ON app.journal_lines AS RESTRICTIVE FOR DELETE TO __RUNTIME__
USING(EXISTS(SELECT 1 FROM app.journals j WHERE j.id=journal_id AND j.business_id=journal_lines.business_id
 AND j.status='draft') AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));

CREATE FUNCTION app._post_journal(p_id uuid,p_date date,p_memo text,p_currency text,p_key text,
 p_hash text,p_version integer,p_lines jsonb,p_reversal uuid,p_draft uuid,p_draft_version integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.businesses; previous app.journals; item jsonb; a app.accounts;
 j uuid:=coalesce(p_draft,gen_random_uuid()); draft app.journals; n integer:=0; d numeric; c numeric; total numeric:=0;
BEGIN
 SELECT * INTO b FROM app.businesses WHERE id=p_id AND id=app.scope() FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM app.memberships WHERE business_id=p_id
 AND user_id=app.actor() AND active AND (role='accountant' OR can_post)) THEN
  RAISE insufficient_privilege USING MESSAGE='Posting permission required';
 END IF;
 SELECT * INTO previous FROM app.journals WHERE business_id=p_id AND idempotency_key=p_key;
 IF FOUND THEN
  IF previous.request_hash<>p_hash THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_IDEMPOTENCY_CONFLICT';
  END IF;
  RETURN previous.id;
 END IF;
 IF b.version<>p_version THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_VERSION';
 END IF;
 IF p_currency<>b.currency THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Foreign currency is not enabled';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM app.periods WHERE business_id=p_id
 AND p_date BETWEEN start_date AND end_date AND NOT locked) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_PERIOD_LOCKED';
 END IF;
 IF jsonb_typeof(p_lines)<>'array' OR jsonb_array_length(p_lines) NOT BETWEEN 2 AND 200 THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid journal lines';
 END IF;
 IF p_reversal IS NOT NULL THEN
  SELECT * INTO previous FROM app.journals WHERE business_id=p_id AND id=p_reversal;
  IF NOT FOUND OR previous.reversal_of IS NOT NULL THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid reversal';
  END IF;
 END IF;
 IF p_draft IS NULL THEN
 INSERT INTO app.journals(id,business_id,accounting_date,memo,currency,idempotency_key,
 request_hash,reversal_of,created_by) VALUES(j,p_id,p_date,p_memo,p_currency,p_key,p_hash,
 p_reversal,app.actor());
 ELSE
  SELECT * INTO draft FROM app.journals WHERE business_id=p_id AND id=p_draft FOR UPDATE;
  IF NOT FOUND OR draft.status<>'draft' OR draft.version<>p_draft_version THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_VERSION';
  END IF;
  DELETE FROM app.journal_lines WHERE business_id=p_id AND journal_id=p_draft;
  UPDATE app.journals SET accounting_date=p_date,memo=p_memo,currency=p_currency,
  idempotency_key=p_key,request_hash=p_hash,reversal_of=p_reversal,status='posted',
  version=version+1 WHERE id=p_draft AND business_id=p_id;
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_lines) LOOP
  n:=n+1;
  IF (item->>'debit')!~'^[0-9]+(\.[0-9]{1,6})?$'
   OR (item->>'credit')!~'^[0-9]+(\.[0-9]{1,6})?$' THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid decimal amount';
  END IF;
  d:=(item->>'debit')::numeric; c:=(item->>'credit')::numeric;
  IF d<>round(d,b.currency_precision) OR c<>round(c,b.currency_precision) THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Unsupported currency precision';
  END IF;
  SELECT * INTO a FROM app.accounts WHERE business_id=p_id AND id=(item->>'account_id')::uuid;
  IF NOT FOUND OR NOT a.active THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Account is unavailable';
  END IF;
  INSERT INTO app.journal_lines(id,business_id,journal_id,account_id,position,debit,credit)
  VALUES(gen_random_uuid(),p_id,j,a.id,n,d,c);
  total:=total+d-c;
 END LOOP;
 IF total<>0 THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Journal must balance';
 END IF;
 IF p_reversal IS NOT NULL AND EXISTS(
  (SELECT account_id,debit,credit,position FROM app.journal_lines WHERE journal_id=j
   EXCEPT SELECT account_id,credit,debit,position FROM app.journal_lines WHERE journal_id=p_reversal)
  UNION ALL
  (SELECT account_id,credit,debit,position FROM app.journal_lines WHERE journal_id=p_reversal
   EXCEPT SELECT account_id,debit,credit,position FROM app.journal_lines WHERE journal_id=j)
 ) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Reversal must exactly offset its journal';
 END IF;
 UPDATE app.businesses SET version=version+1 WHERE id=p_id;
 INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id)
 VALUES(gen_random_uuid(),app.actor(),p_id,'journal.posted',j);
 RETURN j;
END $$;

REVOKE ALL ON FUNCTION app._post_journal(uuid,date,text,text,text,text,integer,jsonb,uuid,uuid,integer) FROM PUBLIC,__RUNTIME__;
CREATE OR REPLACE FUNCTION app.post_journal(p_id uuid,p_date date,p_memo text,p_currency text,p_key text,
 p_hash text,p_version integer,p_lines jsonb,p_reversal uuid DEFAULT NULL) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT app._post_journal(p_id,p_date,p_memo,p_currency,p_key,p_hash,p_version,p_lines,p_reversal,NULL,NULL)
$$;
CREATE FUNCTION app.post_draft_journal(p_id uuid,p_date date,p_memo text,p_currency text,p_key text,
 p_hash text,p_version integer,p_lines jsonb,p_reversal uuid,p_draft uuid,p_draft_version integer) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT app._post_journal(p_id,p_date,p_memo,p_currency,p_key,p_hash,p_version,p_lines,p_reversal,p_draft,p_draft_version)
$$;
REVOKE ALL ON FUNCTION app.post_draft_journal(uuid,date,text,text,text,text,integer,jsonb,uuid,uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.post_draft_journal(uuid,date,text,text,text,text,integer,jsonb,uuid,uuid,integer) TO __RUNTIME__;

CREATE OR REPLACE FUNCTION app.guard_business() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN
 IF (NEW.currency<>OLD.currency OR NEW.currency_precision<>OLD.currency_precision)
 AND EXISTS(SELECT 1 FROM app.journals WHERE business_id=OLD.id AND status='posted') THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted currency is immutable';
 END IF;
 RETURN NEW;
END $$;
