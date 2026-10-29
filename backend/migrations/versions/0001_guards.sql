-- Transaction-local context, RLS and posting gateway. Identifiers are fully qualified.
CREATE FUNCTION app.actor() RETURNS uuid LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
 SELECT nullif(current_setting('app.user_id',true),'')::uuid
$$;
CREATE FUNCTION app.scope() RETURNS uuid LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
 SELECT nullif(current_setting('app.business_id',true),'')::uuid
$$;

ALTER TABLE app.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.users FORCE ROW LEVEL SECURITY;
CREATE POLICY users_read ON app.users FOR SELECT USING (
 id=app.actor() OR email=nullif(current_setting('app.login_email',true),'')
);
CREATE POLICY users_insert ON app.users FOR INSERT WITH CHECK (id=app.actor());
CREATE POLICY users_update ON app.users FOR UPDATE USING (id=app.actor()) WITH CHECK(id=app.actor());

ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY memberships_self ON app.memberships USING(user_id=app.actor())
WITH CHECK(user_id=app.actor());

CREATE FUNCTION app.has_access(b uuid) RETURNS boolean LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.memberships WHERE business_id=b AND user_id=app.actor() AND active)
$$;
CREATE FUNCTION app.has_role(b uuid, roles text[]) RETURNS boolean LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.memberships WHERE business_id=b AND user_id=app.actor()
 AND active AND role=ANY(roles))
$$;

ALTER TABLE app.businesses ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.businesses FORCE ROW LEVEL SECURITY;
CREATE POLICY business_read ON app.businesses FOR SELECT USING(app.has_access(id));
CREATE POLICY business_insert ON app.businesses FOR INSERT WITH CHECK(id=app.scope());
CREATE POLICY business_update ON app.businesses FOR UPDATE USING(id=app.scope() AND app.has_access(id))
WITH CHECK(id=app.scope() AND app.has_access(id));

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['sessions','mfa_factors','recovery_codes'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY self ON app.%I USING(user_id=app.actor()) WITH CHECK(user_id=app.actor())',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['refresh_tokens','action_tokens'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY token_read ON app.%I FOR SELECT USING(user_id=app.actor() OR token_hash=nullif(current_setting(''app.token_hash'',true),''''))',t);
  EXECUTE format('CREATE POLICY token_insert ON app.%I FOR INSERT WITH CHECK(user_id=app.actor())',t);
  EXECUTE format('CREATE POLICY token_update ON app.%I FOR UPDATE USING(user_id=app.actor()) WITH CHECK(user_id=app.actor())',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['accounts','periods','policy_history','journals','journal_lines'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant ON app.%I USING(business_id=app.scope() AND app.has_access(business_id)) WITH CHECK(business_id=app.scope() AND app.has_access(business_id))',t);
 END LOOP;
END $$;

ALTER TABLE app.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY invites ON app.invitations USING (
 token_hash=nullif(current_setting('app.token_hash',true),'') OR
 (business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']))
) WITH CHECK(token_hash=nullif(current_setting('app.token_hash',true),'') OR
 (business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant'])));

ALTER TABLE app.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_read ON app.audit_events FOR SELECT USING (
 (business_id IS NULL AND user_id=app.actor()) OR
 (business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant','auditor']))
);
CREATE POLICY audit_insert ON app.audit_events FOR INSERT WITH CHECK (
 user_id=app.actor() AND (business_id IS NULL OR (business_id=app.scope() AND app.has_access(business_id)))
);

CREATE FUNCTION app.immutable() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN
 RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted records are immutable';
END $$;
CREATE TRIGGER journal_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON app.journals
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();
CREATE TRIGGER lines_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON app.journal_lines
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON app.audit_events
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();
CREATE TRIGGER policy_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON app.policy_history
FOR EACH STATEMENT EXECUTE FUNCTION app.immutable();

-- Only lines inserted in the header's transaction may be appended.
ALTER TABLE app.journals ADD COLUMN posting_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
CREATE FUNCTION app.check_line_transaction() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM app.journals WHERE id=NEW.journal_id
   AND business_id=NEW.business_id AND posting_xid=pg_current_xact_id()) THEN
   RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted records are immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER line_transaction BEFORE INSERT ON app.journal_lines
FOR EACH ROW EXECUTE FUNCTION app.check_line_transaction();

CREATE FUNCTION app.check_balance() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ DECLARE n integer; d numeric; c numeric; BEGIN
 SELECT count(*),sum(debit),sum(credit) INTO n,d,c FROM app.journal_lines
 WHERE journal_id=NEW.id AND business_id=NEW.business_id;
 IF n < 2 OR n > 200 OR d<>c OR d IS NULL OR d=0 THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Journal must balance';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_balance AFTER INSERT ON app.journals
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.check_balance();

CREATE FUNCTION app.guard_business() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN
 IF (NEW.currency<>OLD.currency OR NEW.currency_precision<>OLD.currency_precision)
 AND EXISTS(SELECT 1 FROM app.journals WHERE business_id=OLD.id) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted currency is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER business_currency BEFORE UPDATE ON app.businesses
FOR EACH ROW EXECUTE FUNCTION app.guard_business();

CREATE FUNCTION app.create_business(p_id uuid,p_name text,p_currency text,p_precision integer,
 p_timezone text,p_basis text,p_fiscal integer,p_jurisdiction text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF app.actor() IS NULL OR NOT EXISTS(SELECT 1 FROM app.users WHERE id=app.actor()
 AND verified AND NOT suspended) OR NOT EXISTS(SELECT 1 FROM app.mfa_factors
 WHERE user_id=app.actor() AND verified) THEN
  RAISE insufficient_privilege USING MESSAGE='Verified identity and MFA required';
 END IF;
 PERFORM set_config('app.business_id',p_id::text,true);
 INSERT INTO app.businesses(id,name,currency,currency_precision,timezone,basis,fiscal_month,
 jurisdiction,policy_version,version) VALUES(p_id,p_name,p_currency,p_precision,p_timezone,
 p_basis,p_fiscal,p_jurisdiction,1,0);
 INSERT INTO app.memberships(business_id,user_id,role,can_post,can_invite,active)
 VALUES(p_id,app.actor(),'owner',false,true,true);
 INSERT INTO app.policy_history(id,business_id,version,effective_date,basis,jurisdiction)
 VALUES(gen_random_uuid(),p_id,1,current_date,p_basis,p_jurisdiction);
 RETURN p_id;
END $$;

CREATE FUNCTION app.accept_invite(p_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE invitation app.invitations; BEGIN
 PERFORM set_config('app.token_hash',p_hash,true);
 SELECT * INTO invitation FROM app.invitations WHERE token_hash=p_hash FOR UPDATE;
 IF NOT FOUND OR invitation.used_at IS NOT NULL OR invitation.expires_at<=now()
 OR NOT EXISTS(SELECT 1 FROM app.users WHERE id=app.actor() AND verified AND NOT suspended
 AND email=invitation.email) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invitation is invalid or expired';
 END IF;
 PERFORM set_config('app.business_id',invitation.business_id::text,true);
 INSERT INTO app.memberships(business_id,user_id,role,can_post,can_invite,active)
 VALUES(invitation.business_id,app.actor(),invitation.role,false,false,true);
 -- The invitation policy permits redemption by the matching token hash.
 UPDATE app.invitations SET used_at=now() WHERE token_hash=p_hash;
 RETURN invitation.business_id;
END $$;

CREATE FUNCTION app.post_journal(p_id uuid,p_date date,p_memo text,p_currency text,p_key text,
 p_hash text,p_version integer,p_lines jsonb,p_reversal uuid DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.businesses; previous app.journals; item jsonb; a app.accounts;
 j uuid:=gen_random_uuid(); n integer:=0; d numeric; c numeric; total numeric:=0;
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
 INSERT INTO app.journals(id,business_id,accounting_date,memo,currency,idempotency_key,
 request_hash,reversal_of,created_by) VALUES(j,p_id,p_date,p_memo,p_currency,p_key,p_hash,
 p_reversal,app.actor());
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

-- Remove broad bootstrap defaults from the actual domain tables.
REVOKE ALL ON ALL TABLES IN SCHEMA app FROM __RUNTIME__;
GRANT SELECT,INSERT,UPDATE ON app.users,app.sessions,app.refresh_tokens,app.action_tokens,
 app.mfa_factors,app.recovery_codes,app.auth_rate_buckets TO __RUNTIME__;
GRANT SELECT ON app.businesses,app.memberships,app.journals,app.journal_lines,app.policy_history TO __RUNTIME__;
GRANT SELECT,INSERT,UPDATE ON app.accounts,app.periods,app.invitations TO __RUNTIME__;
GRANT SELECT,INSERT ON app.audit_events TO __RUNTIME__;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.actor(),app.scope(),app.has_access(uuid),app.has_role(uuid,text[]),
 app.create_business(uuid,text,text,integer,text,text,integer,text),app.accept_invite(text),
 app.post_journal(uuid,date,text,text,text,text,integer,jsonb,uuid) TO __RUNTIME__;
CREATE INDEX journal_business_date_id ON app.journals(business_id,accounting_date,id);
CREATE INDEX journal_line_business_account ON app.journal_lines(business_id,account_id,journal_id);

-- Read-only members cannot mutate setup data even through raw runtime SQL.
CREATE POLICY account_insert_role ON app.accounts AS RESTRICTIVE FOR INSERT
WITH CHECK(app.has_role(business_id,ARRAY['owner','accountant']));
CREATE POLICY account_update_role ON app.accounts AS RESTRICTIVE FOR UPDATE
USING(app.has_role(business_id,ARRAY['owner','accountant']))
WITH CHECK(app.has_role(business_id,ARRAY['owner','accountant']));
CREATE POLICY period_insert_role ON app.periods AS RESTRICTIVE FOR INSERT
WITH CHECK(app.has_role(business_id,ARRAY['accountant']));
CREATE POLICY period_update_role ON app.periods AS RESTRICTIVE FOR UPDATE
USING(app.has_role(business_id,ARRAY['accountant']))
WITH CHECK(app.has_role(business_id,ARRAY['accountant']));

CREATE FUNCTION app.guard_period() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$ BEGIN
 PERFORM 1 FROM app.businesses WHERE id=NEW.business_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM app.periods WHERE business_id=NEW.business_id AND id<>NEW.id
  AND start_date<=NEW.end_date AND end_date>=NEW.start_date) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Periods cannot overlap';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.business_id<>OLD.business_id OR NEW.start_date<>OLD.start_date
  OR NEW.end_date<>OLD.end_date) THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Period boundaries are immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER period_guard BEFORE INSERT OR UPDATE ON app.periods
FOR EACH ROW EXECUTE FUNCTION app.guard_period();

CREATE FUNCTION app.change_policy(p_b uuid,p_basis text,p_jurisdiction text,p_date date,p_version integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.businesses; BEGIN
 IF NOT app.has_role(p_b,ARRAY['accountant']) THEN
  RAISE insufficient_privilege USING MESSAGE='Accountant permission required';
 END IF;
 SELECT * INTO b FROM app.businesses WHERE id=p_b AND id=app.scope() FOR UPDATE;
 IF NOT FOUND OR b.version<>p_version THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_VERSION';
 END IF;
 IF p_basis NOT IN ('cash','accrual') OR length(p_jurisdiction) NOT BETWEEN 1 AND 100
 OR p_date < (now() AT TIME ZONE b.timezone)::date THEN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Policy changes must be prospective';
 END IF;
 INSERT INTO app.policy_history(id,business_id,version,effective_date,basis,jurisdiction)
 VALUES(gen_random_uuid(),p_b,b.policy_version+1,p_date,p_basis,p_jurisdiction);
 UPDATE app.businesses SET policy_version=policy_version+1,version=version+1 WHERE id=p_b;
 RETURN b.policy_version+1;
END $$;
REVOKE ALL ON FUNCTION app.change_policy(uuid,text,text,date,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.change_policy(uuid,text,text,date,integer) TO __RUNTIME__;
