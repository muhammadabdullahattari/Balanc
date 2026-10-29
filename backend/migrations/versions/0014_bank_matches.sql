ALTER TABLE app.import_batches ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX import_file_once ON app.import_batches(business_id,mode,file_hash);
ALTER TABLE app.bank_rows ADD UNIQUE(business_id,id,account_id);
CREATE TABLE app.bank_matches (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),row_id uuid NOT NULL,
 account_id uuid NOT NULL,journal_id uuid NOT NULL,
 UNIQUE(business_id,account_id,journal_id),
 FOREIGN KEY(business_id,row_id,account_id) REFERENCES app.bank_rows(business_id,id,account_id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE INDEX bank_match_row ON app.bank_matches(business_id,row_id);
ALTER TABLE app.bank_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.bank_matches FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.bank_matches USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.bank_matches AS RESTRICTIVE FOR INSERT WITH CHECK(app.can_post(business_id));
GRANT SELECT,INSERT ON app.bank_matches TO __RUNTIME__;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.bank_matches FOR EACH ROW EXECUTE FUNCTION app.immutable();
CREATE FUNCTION app.bank_match_total() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r app.bank_rows; net numeric; matches integer; BEGIN
 SELECT * INTO r FROM app.bank_rows WHERE business_id=NEW.business_id
 AND id=CASE WHEN TG_TABLE_NAME='bank_rows' THEN NEW.id ELSE (to_jsonb(NEW)->>'row_id')::uuid END;
 SELECT count(*) INTO matches FROM app.bank_matches WHERE business_id=r.business_id AND row_id=r.id;
 IF r.state IN ('categorized','matched') THEN
  SELECT sum(l.debit-l.credit) INTO net FROM app.bank_matches m JOIN app.journal_lines l
   ON l.business_id=m.business_id AND l.journal_id=m.journal_id AND l.account_id=m.account_id
   JOIN app.journals j ON j.business_id=m.business_id AND j.id=m.journal_id
   WHERE m.business_id=r.business_id AND m.row_id=r.id AND j.status='posted'
   AND j.accounting_date<=r.accounting_date;
  IF matches<1 OR matches>20 OR net IS DISTINCT FROM r.amount THEN
   RAISE check_violation USING MESSAGE='Bank disposition must tie to its consumed journal bundle';
  END IF;
 ELSIF matches<>0 THEN
  RAISE check_violation USING MESSAGE='Unreviewed or excluded bank rows cannot consume journals';
 END IF; RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER bank_match_total AFTER INSERT ON app.bank_matches
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.bank_match_total();
CREATE CONSTRAINT TRIGGER bank_row_total AFTER UPDATE ON app.bank_rows
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.bank_match_total();
