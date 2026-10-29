ALTER TABLE app.evidence DROP CONSTRAINT evidence_status_check;
ALTER TABLE app.evidence ADD CHECK(status IN ('quarantined','rejected','clean','unsupported','extracted'));
ALTER TABLE app.evidence ALTER COLUMN scan_version DROP NOT NULL;
CREATE TABLE app.evidence_jobs (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), evidence_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES app.users(id),
 state varchar(20) NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3), lease_token uuid, lease_until timestamptz,
 available_at timestamptz NOT NULL DEFAULT now(), error_code varchar(50),
 UNIQUE(business_id,evidence_id), FOREIGN KEY(business_id,evidence_id) REFERENCES app.evidence(business_id,id)
);
CREATE INDEX evidence_queue ON app.evidence_jobs(business_id,created_by,available_at,id)
 WHERE state IN ('queued','running');
ALTER TABLE app.evidence_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.evidence_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.evidence_jobs USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY creator ON app.evidence_jobs AS RESTRICTIVE FOR INSERT WITH CHECK(created_by=app.actor()
 AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper','reviewer']));
GRANT SELECT,INSERT ON app.evidence_jobs TO __RUNTIME__;
CREATE POLICY job_definer ON app.evidence_jobs AS RESTRICTIVE FOR UPDATE
 USING(app.has_role(business_id,ARRAY['owner','accountant','bookkeeper','reviewer']));
DROP TRIGGER immutable ON app.evidence;
CREATE TRIGGER immutable_delete BEFORE DELETE ON app.evidence FOR EACH ROW EXECUTE FUNCTION app.immutable();
CREATE FUNCTION app.evidence_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF (to_jsonb(NEW)-'status'-'scan_version'-'raw_text'-'extraction')<>
    (to_jsonb(OLD)-'status'-'scan_version'-'raw_text'-'extraction') OR OLD.status<>'quarantined' THEN
  RAISE check_violation USING MESSAGE='Evidence original and completed extraction are immutable';
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER evidence_guard BEFORE UPDATE ON app.evidence FOR EACH ROW EXECUTE FUNCTION app.evidence_guard();
CREATE FUNCTION app.claim_evidence_job(p_b uuid) RETURNS TABLE(id uuid,evidence_id uuid,lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE chosen app.evidence_jobs; BEGIN
 IF p_b<>app.scope() OR NOT app.has_role(p_b,ARRAY['owner','accountant','bookkeeper','reviewer']) THEN
  RAISE insufficient_privilege USING MESSAGE='Scoped worker authorization required';
 END IF;
 -- Expired final-attempt leases become visible failed jobs instead of sticking forever.
 UPDATE app.evidence_jobs j SET state='failed',error_code='ERR_LEASE_EXHAUSTED',lease_until=NULL,
 lease_token=NULL WHERE business_id=p_b AND created_by=app.actor() AND state='running'
 AND lease_until<now() AND attempts>=3;
 SELECT * INTO chosen FROM app.evidence_jobs j WHERE j.business_id=p_b AND j.created_by=app.actor()
 AND j.attempts<3 AND ((j.state='queued' AND j.available_at<=now()) OR
 (j.state='running' AND j.lease_until<now())) ORDER BY j.available_at,j.id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE app.evidence_jobs j SET state='running',attempts=j.attempts+1,
 lease_token=gen_random_uuid(),lease_until=now()+interval '120 seconds',error_code=NULL
 WHERE j.id=chosen.id RETURNING j.* INTO chosen;
 RETURN QUERY SELECT chosen.id,chosen.evidence_id,chosen.lease_token;
END $$;
CREATE FUNCTION app.finish_evidence_job(p_id uuid,p_token uuid,p_status text,p_scanner text,p_error text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.evidence_jobs; BEGIN
 SELECT * INTO j FROM app.evidence_jobs WHERE id=p_id AND business_id=app.scope()
 AND created_by=app.actor() FOR UPDATE;
 IF NOT FOUND OR NOT app.has_access(j.business_id) OR j.state<>'running'
 OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=now() THEN RETURN false; END IF;
 IF p_status IN ('unsupported','rejected') THEN
  IF p_status='unsupported' AND (p_scanner IS NULL OR p_scanner NOT LIKE 'ClamAV%') THEN
   RAISE check_violation USING MESSAGE='A verified scanner result is required';
  END IF;
  UPDATE app.evidence SET status=p_status,scan_version=p_scanner,
  extraction=jsonb_build_object('status',p_status,'fields','[]'::jsonb,'model_version',NULL,
    'reason',CASE WHEN p_status='rejected' THEN 'Malware rejected' ELSE 'Manual review required; no approved extractor enabled' END)
  WHERE id=j.evidence_id AND business_id=j.business_id;
  UPDATE app.evidence_jobs SET state='succeeded',lease_token=NULL,lease_until=NULL WHERE id=j.id;
 ELSE
  UPDATE app.evidence_jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,
  error_code=CASE WHEN p_error IN ('ERR_SCAN_UNAVAILABLE','ERR_STORAGE_UNAVAILABLE','ERR_INTEGRITY')
   THEN p_error ELSE 'ERR_WORKER' END,
  available_at=now()+(power(2,attempts)*5)::int*interval '1 second',lease_until=NULL,lease_token=NULL WHERE id=j.id;
 END IF;
 INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id)
 VALUES(gen_random_uuid(),app.actor(),j.business_id,'evidence.job_completed',j.evidence_id);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION app.claim_evidence_job(uuid),app.finish_evidence_job(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.claim_evidence_job(uuid),app.finish_evidence_job(uuid,uuid,text,text,text) TO __RUNTIME__;
