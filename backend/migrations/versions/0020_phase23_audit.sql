-- Keep historical posted rows intact; enforce chronology for new postings only.
CREATE FUNCTION app.reversal_date_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.status='posted' AND NEW.reversal_of IS NOT NULL AND EXISTS(
  SELECT 1 FROM app.journals j WHERE j.business_id=NEW.business_id
  AND j.id=NEW.reversal_of AND j.accounting_date>NEW.accounting_date
 ) THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='ERR_REVERSAL_DATE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reversal_date_guard BEFORE INSERT OR UPDATE ON app.journals
FOR EACH ROW EXECUTE FUNCTION app.reversal_date_guard();
REVOKE ALL ON FUNCTION app.reversal_date_guard() FROM PUBLIC;

-- A login may verify its password before a concurrent reset commits, then insert
-- its session after revoke_all has run. Retain the generation actually verified.
ALTER TABLE app.users ADD COLUMN password_version bigint NOT NULL DEFAULT 0;
ALTER TABLE app.sessions ADD COLUMN password_version bigint NOT NULL DEFAULT 0;
CREATE FUNCTION app.password_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 NEW.password_version := OLD.password_version +
  CASE WHEN NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN 1 ELSE 0 END;
 RETURN NEW;
END $$;
CREATE TRIGGER password_version_guard BEFORE UPDATE ON app.users
FOR EACH ROW EXECUTE FUNCTION app.password_version_guard();
REVOKE ALL ON FUNCTION app.password_version_guard() FROM PUBLIC;
