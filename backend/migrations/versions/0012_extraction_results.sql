REVOKE EXECUTE ON FUNCTION app.finish_evidence_job(uuid,uuid,text,text,text) FROM __RUNTIME__;
CREATE FUNCTION app.complete_evidence_job(p_id uuid,p_token uuid,p_scanner text,p_result jsonb,
 p_status text,p_error text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$ DECLARE j app.evidence_jobs; BEGIN
 SELECT * INTO j FROM app.evidence_jobs WHERE id=p_id AND business_id=app.scope()
 AND created_by=app.actor() FOR UPDATE;
 IF NOT FOUND OR NOT app.has_role(j.business_id,ARRAY['owner','accountant','bookkeeper','reviewer'])
 OR j.state<>'running' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=now()
 THEN RETURN false; END IF;
 IF p_status IN ('extracted','unsupported','rejected') THEN
  IF p_status<>'rejected' AND (p_scanner IS NULL OR p_scanner NOT LIKE 'ClamAV%') THEN
   RAISE check_violation USING MESSAGE='Clean scanning must precede extraction';
  END IF;
  IF p_result IS NULL OR jsonb_typeof(p_result)<>'object' OR p_result->>'status' IS DISTINCT FROM p_status
   OR jsonb_typeof(p_result->'fields') IS DISTINCT FROM 'array'
   OR jsonb_array_length(p_result->'fields')>20000
   OR octet_length(p_result::text)>8388608 OR length(coalesce(p_result->>'raw_text',''))>200000
   OR (p_status='extracted' AND nullif(p_result->>'model_version','') IS NULL)
   OR p_result->>'requires_review' IS DISTINCT FROM 'true' THEN
   RAISE check_violation USING MESSAGE='Invalid reviewed extraction result';
  END IF;
  UPDATE app.evidence SET status=p_status,scan_version=p_scanner,
   raw_text=p_result->>'raw_text',extraction=p_result-'raw_text'
   WHERE id=j.evidence_id AND business_id=j.business_id;
  UPDATE app.evidence_jobs SET state='succeeded',lease_token=NULL,lease_until=NULL,
   error_code=CASE WHEN p_error='ERR_OCR_UNAVAILABLE' THEN p_error ELSE NULL END WHERE id=j.id;
 ELSE
  UPDATE app.evidence_jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,
   error_code=CASE WHEN p_error IN ('ERR_SCAN_UNAVAILABLE','ERR_STORAGE_UNAVAILABLE','ERR_INTEGRITY')
   THEN p_error ELSE 'ERR_WORKER' END,available_at=now()+(power(2,attempts)*5)::int*interval '1 second',
   lease_until=NULL,lease_token=NULL WHERE id=j.id;
 END IF;
 INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id)
 VALUES(gen_random_uuid(),app.actor(),j.business_id,'evidence.job_completed',j.evidence_id);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION app.complete_evidence_job(uuid,uuid,text,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.complete_evidence_job(uuid,uuid,text,jsonb,text,text) TO __RUNTIME__;
