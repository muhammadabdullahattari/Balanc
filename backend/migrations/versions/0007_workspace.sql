CREATE TABLE app.report_runs (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), kind varchar(40) NOT NULL,
 result jsonb NOT NULL, created_by uuid NOT NULL REFERENCES app.users(id), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.evidence (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), filename varchar(160) NOT NULL,
 content_hash varchar(64) NOT NULL, media_type varchar(100) NOT NULL, size integer NOT NULL CHECK(size>0),
 status varchar(30) NOT NULL CHECK(status IN ('clean','unsupported','extracted')),
 scan_version varchar(160) NOT NULL, object_key varchar(200) NOT NULL,
 raw_text text, extraction jsonb NOT NULL DEFAULT '{}', created_by uuid NOT NULL REFERENCES app.users(id),
 UNIQUE(business_id,id), UNIQUE(object_key)
);
CREATE INDEX evidence_hash ON app.evidence(business_id,content_hash);
CREATE TABLE app.evidence_notes (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), evidence_id uuid NOT NULL,
 note varchar(2000) NOT NULL, fields jsonb NOT NULL, created_by uuid NOT NULL REFERENCES app.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
CREATE TABLE app.evidence_links (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), evidence_id uuid NOT NULL,
 resource_kind varchar(30) NOT NULL, resource_id uuid NOT NULL,
 UNIQUE(business_id,evidence_id,resource_kind,resource_id),
 FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
ALTER TABLE app.statements ADD FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id);
CREATE TABLE app.notifications (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 recipient_id uuid NOT NULL REFERENCES app.users(id), kind varchar(40) NOT NULL, resource_id uuid,
 dedupe_key varchar(160) NOT NULL, read boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(business_id,recipient_id,dedupe_key)
);
CREATE TABLE app.notification_preferences (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), user_id uuid NOT NULL REFERENCES app.users(id),
 marketing boolean NOT NULL DEFAULT false, reminders boolean NOT NULL DEFAULT true,
 quiet_start integer NOT NULL DEFAULT 21 CHECK(quiet_start BETWEEN 0 AND 23),
 quiet_end integer NOT NULL DEFAULT 8 CHECK(quiet_end BETWEEN 0 AND 23),
 version integer NOT NULL DEFAULT 0, UNIQUE(business_id,user_id)
);
CREATE TABLE app.close_tasks (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), period_id uuid NOT NULL,
 name varchar(160) NOT NULL, assigned_to uuid NOT NULL REFERENCES app.users(id), required boolean NOT NULL DEFAULT true,
 state varchar(20) NOT NULL DEFAULT 'open' CHECK(state IN ('open','prepared','approved')),
 prepared_by uuid REFERENCES app.users(id), reviewed_by uuid REFERENCES app.users(id), evidence_id uuid,
 comment varchar(2000) NOT NULL DEFAULT '', version integer NOT NULL DEFAULT 0,
 CHECK(state<>'approved' OR (prepared_by IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_by<>prepared_by)),
 FOREIGN KEY(business_id,period_id) REFERENCES app.periods(business_id,id),
 FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
CREATE TABLE app.consents (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
 user_id uuid NOT NULL REFERENCES app.users(id), purpose varchar(50) NOT NULL,
 policy_version varchar(80) NOT NULL, granted boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.privacy_requests (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), user_id uuid NOT NULL REFERENCES app.users(id),
 kind varchar(20) NOT NULL CHECK(kind IN ('export','deletion')),
 status varchar(30) NOT NULL DEFAULT 'awaiting_retention_review', reason varchar(1000) NOT NULL
);
CREATE TABLE app.trial_entitlements (
 id uuid PRIMARY KEY, business_id uuid NOT NULL UNIQUE REFERENCES app.businesses(id),
 status varchar(20) NOT NULL DEFAULT 'manual_trial' CHECK(status IN ('manual_trial','read_only')),
 expires_at timestamptz, import_row_limit integer NOT NULL DEFAULT 500 CHECK(import_row_limit BETWEEN 1 AND 500),
 document_byte_limit integer NOT NULL DEFAULT 26214400 CHECK(document_byte_limit BETWEEN 1 AND 26214400)
);
CREATE TABLE app.advances (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), party_id uuid NOT NULL,
 direction varchar(10) NOT NULL CHECK(direction IN ('customer','vendor')), accounting_date date NOT NULL,
 deposit_account_id uuid NOT NULL, bank_account_id uuid NOT NULL,
 amount numeric(24,6) NOT NULL CHECK(amount>0), journal_id uuid NOT NULL,
 UNIQUE(business_id,id), UNIQUE(business_id,journal_id),
 FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id),
 FOREIGN KEY(business_id,deposit_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,bank_account_id) REFERENCES app.accounts(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE TABLE app.advance_applications (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), advance_id uuid NOT NULL,
 document_id uuid NOT NULL, accounting_date date NOT NULL, amount numeric(24,6) NOT NULL CHECK(amount>0), journal_id uuid NOT NULL,
 FOREIGN KEY(business_id,advance_id) REFERENCES app.advances(business_id,id),
 FOREIGN KEY(business_id,document_id) REFERENCES app.trade_documents(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE INDEX advance_asof ON app.advance_applications(business_id,advance_id,accounting_date);
CREATE INDEX report_history ON app.report_runs(business_id,created_at,id);
CREATE INDEX notifications_inbox ON app.notifications(business_id,recipient_id,id);
CREATE INDEX document_search ON app.trade_documents USING gin(to_tsvector('simple',number));
CREATE INDEX party_search ON app.parties USING gin(to_tsvector('simple',name));

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['report_runs','evidence','evidence_notes','evidence_links','notifications',
 'notification_preferences','close_tasks','consents','privacy_requests','trial_entitlements','advances','advance_applications'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant ON app.%I USING(business_id=app.scope() AND app.has_access(business_id)) WITH CHECK(business_id=app.scope() AND app.has_access(business_id))',t);
  EXECUTE format('GRANT SELECT ON app.%I TO __RUNTIME__',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['report_runs','evidence','evidence_notes','evidence_links','notifications',
 'notification_preferences','close_tasks','consents','privacy_requests','advances','advance_applications'] LOOP
  EXECUTE format('GRANT INSERT ON app.%I TO __RUNTIME__',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['evidence','evidence_notes','evidence_links','close_tasks'] LOOP
  EXECUTE format('CREATE POLICY writer ON app.%I AS RESTRICTIVE FOR INSERT WITH CHECK(app.has_role(business_id,ARRAY[''owner'',''accountant'',''bookkeeper'',''reviewer'']))',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['advances','advance_applications'] LOOP
  EXECUTE format('CREATE POLICY writer ON app.%I AS RESTRICTIVE FOR INSERT WITH CHECK(app.can_post(business_id))',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['report_runs','evidence','evidence_notes','evidence_links','consents','advances','advance_applications'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.immutable()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['notifications','notification_preferences','close_tasks'] LOOP
  EXECUTE format('GRANT UPDATE ON app.%I TO __RUNTIME__',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['notification_preferences','consents','privacy_requests'] LOOP
  EXECUTE format('CREATE POLICY self ON app.%I AS RESTRICTIVE USING(user_id=app.actor()) WITH CHECK(user_id=app.actor())',t);
 END LOOP;
END $$;
CREATE POLICY self ON app.notifications AS RESTRICTIVE USING(recipient_id=app.actor()) WITH CHECK(recipient_id=app.actor());
CREATE POLICY close_update ON app.close_tasks AS RESTRICTIVE FOR UPDATE
 USING(app.has_role(business_id,ARRAY['owner','accountant','bookkeeper','reviewer']));
CREATE FUNCTION app.close_gate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.locked AND NOT OLD.locked AND EXISTS(SELECT 1 FROM app.close_tasks
 WHERE business_id=NEW.business_id AND period_id=NEW.id AND required AND state<>'approved') THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Mandatory close tasks need independent approval';
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER close_gate BEFORE UPDATE ON app.periods FOR EACH ROW EXECUTE FUNCTION app.close_gate();
