CREATE FUNCTION app.initialize_trial(p_b uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$ DECLARE created uuid; BEGIN
 IF p_b<>app.scope() OR NOT app.has_access(p_b) THEN
  RAISE insufficient_privilege USING MESSAGE='Business authorization required';
 END IF;
 INSERT INTO app.trial_entitlements(id,business_id) VALUES(gen_random_uuid(),p_b)
 ON CONFLICT(business_id) DO NOTHING RETURNING id INTO created;
 IF created IS NOT NULL THEN
  INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id)
  VALUES(gen_random_uuid(),app.actor(),p_b,'trial.initialized',created);
 END IF;
END $$;
CREATE FUNCTION app.replay_evidence_job(p_b uuid,p_id uuid,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j app.evidence_jobs; BEGIN
 IF p_b<>app.scope() OR NOT app.has_role(p_b,ARRAY['owner','accountant'])
 OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 1000 THEN
  RAISE insufficient_privilege USING MESSAGE='Authorized reasoned replay required';
 END IF;
 SELECT * INTO j FROM app.evidence_jobs WHERE business_id=p_b AND id=p_id FOR UPDATE;
 IF NOT FOUND OR j.state<>'failed' OR NOT EXISTS(SELECT 1 FROM app.evidence e
 WHERE e.business_id=p_b AND e.id=j.evidence_id AND e.status='quarantined') THEN RETURN false; END IF;
 IF NOT app.member_active(p_b,j.created_by) THEN RETURN false; END IF;
 UPDATE app.evidence_jobs SET state='queued',attempts=0,available_at=now(),error_code=NULL,
 lease_token=NULL,lease_until=NULL WHERE id=j.id;
 INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id,details)
 VALUES(gen_random_uuid(),app.actor(),p_b,'evidence.job_replayed',p_id,jsonb_build_object('reason',p_reason));
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION app.initialize_trial(uuid),app.replay_evidence_job(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.initialize_trial(uuid),app.replay_evidence_job(uuid,uuid,text) TO __RUNTIME__;
CREATE TABLE app.beneficiary_changes (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),party_id uuid NOT NULL,
 key varchar(100) NOT NULL,request_hash varchar(64) NOT NULL,encrypted_details text NOT NULL,
 masked_reference varchar(20) NOT NULL,reason varchar(1000) NOT NULL,
 status varchar(20) NOT NULL DEFAULT 'held' CHECK(status='held'),created_by uuid NOT NULL REFERENCES app.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(business_id,key),
 FOREIGN KEY(business_id,party_id) REFERENCES app.parties(business_id,id)
);
ALTER TABLE app.beneficiary_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.beneficiary_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.beneficiary_changes USING(business_id=app.scope() AND app.has_access(business_id)
 AND (created_by=app.actor() OR app.has_role(business_id,ARRAY['owner','accountant'])))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.beneficiary_changes AS RESTRICTIVE FOR INSERT WITH CHECK(created_by=app.actor()
 AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
GRANT SELECT,INSERT ON app.beneficiary_changes TO __RUNTIME__;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.beneficiary_changes
 FOR EACH ROW EXECUTE FUNCTION app.immutable();
CREATE INDEX payment_party_dates ON app.payments(business_id,party_id,accounting_date,id);
CREATE INDEX advance_party_dates ON app.advances(business_id,party_id,accounting_date,id);
CREATE INDEX close_period_state ON app.close_tasks(business_id,period_id,state);
CREATE INDEX beneficiary_party_dates ON app.beneficiary_changes(business_id,party_id,created_at,id);
