ALTER TABLE app.memberships ADD COLUMN version integer NOT NULL DEFAULT 0;
-- Only the already-restricted migration owner, while inside a narrow definer
-- function, can see other memberships in the verified transaction scope.
CREATE POLICY scoped_admin ON app.memberships USING(
 current_user=__OWNER_LITERAL__ AND business_id=app.scope()
) WITH CHECK(current_user=__OWNER_LITERAL__ AND business_id=app.scope());
CREATE FUNCTION app.list_members(p_b uuid) RETURNS TABLE(
 user_id uuid,role text,active boolean,can_post boolean,can_invite boolean,version integer
) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF p_b<>app.scope() OR NOT app.has_role(p_b,ARRAY['owner','accountant']) THEN
  RAISE insufficient_privilege USING MESSAGE='Member administration denied';
 END IF;
 RETURN QUERY SELECT m.user_id,m.role::text,m.active,m.can_post,m.can_invite,m.version
 FROM app.memberships m WHERE m.business_id=p_b;
END $$;
CREATE FUNCTION app.member_active(p_b uuid,p_u uuid) RETURNS boolean LANGUAGE sql
SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_b=app.scope() AND app.has_access(p_b) AND EXISTS(
  SELECT 1 FROM app.memberships WHERE business_id=p_b AND user_id=p_u AND active)
$$;
CREATE FUNCTION app.update_member(p_b uuid,p_u uuid,p_role text,p_active boolean,p_post boolean,
 p_invite boolean,p_version integer) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$ DECLARE m app.memberships; BEGIN
 PERFORM 1 FROM app.businesses WHERE id=p_b AND id=app.scope() FOR UPDATE;
 IF NOT FOUND OR NOT app.has_role(p_b,ARRAY['owner']) THEN
  RAISE insufficient_privilege USING MESSAGE='Owner authorization required';
 END IF;
 SELECT * INTO m FROM app.memberships WHERE business_id=p_b AND user_id=p_u FOR UPDATE;
 IF NOT FOUND OR m.version<>p_version THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='ERR_VERSION';
 END IF;
 IF p_role NOT IN ('owner','accountant','bookkeeper','reviewer','auditor') THEN
  RAISE check_violation USING MESSAGE='Unsupported membership role';
 END IF;
 IF m.role='owner' AND m.active AND (NOT p_active OR p_role<>'owner') AND NOT EXISTS(
  SELECT 1 FROM app.memberships WHERE business_id=p_b AND active AND role='owner' AND user_id<>p_u) THEN
  RAISE check_violation USING MESSAGE='The final active owner cannot be removed';
 END IF;
 UPDATE app.memberships SET role=p_role,active=p_active,can_post=p_post,can_invite=p_invite,
 version=version+1 WHERE business_id=p_b AND user_id=p_u;
 INSERT INTO app.audit_events(id,user_id,business_id,action,resource_id)
 VALUES(gen_random_uuid(),app.actor(),p_b,'membership.updated',p_u);
 RETURN p_version+1;
END $$;
REVOKE ALL ON FUNCTION app.list_members(uuid),app.member_active(uuid,uuid),
 app.update_member(uuid,uuid,text,boolean,boolean,boolean,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.list_members(uuid),app.member_active(uuid,uuid),
 app.update_member(uuid,uuid,text,boolean,boolean,boolean,integer) TO __RUNTIME__;

CREATE FUNCTION app.source_link_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j app.journals; o app.operations; BEGIN
 SELECT * INTO j FROM app.journals WHERE business_id=NEW.business_id AND id=NEW.journal_id;
 SELECT * INTO o FROM app.operations WHERE business_id=NEW.business_id AND journal_id=NEW.journal_id;
 IF j.id IS NULL OR j.status<>'posted' OR j.source_kind<>'business_operation' OR o.id IS NULL
 OR (o.result->>'id') IS DISTINCT FROM NEW.id::text THEN
  RAISE check_violation USING MESSAGE='Financial source must be linked to its own posting operation';
 END IF;
 IF TG_TABLE_NAME='trade_documents' AND o.action<>'trade.issued' OR
    TG_TABLE_NAME='payments' AND o.action<>'payment.posted' OR
    TG_TABLE_NAME='advances' AND o.action<>'advance.received' OR
    TG_TABLE_NAME='loan_movements' AND o.action<>'loan.'||(to_jsonb(NEW)->>'loan_id')||'.movement' THEN
  RAISE check_violation USING MESSAGE='Incompatible source operation';
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['trade_documents','payments','loan_movements','advances'] LOOP
  EXECUTE format('CREATE CONSTRAINT TRIGGER source_link AFTER INSERT ON app.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.source_link_guard()',t);
 END LOOP;
END $$;
CREATE FUNCTION app.close_task_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NOT app.member_active(NEW.business_id,NEW.assigned_to) OR EXISTS(SELECT 1 FROM app.periods
 WHERE business_id=NEW.business_id AND id=NEW.period_id AND locked) THEN
  RAISE check_violation USING MESSAGE='Close task needs an active assignee and open period';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'open' OR NEW.prepared_by IS NOT NULL OR NEW.reviewed_by IS NOT NULL THEN
   RAISE check_violation USING MESSAGE='Tasks begin unapproved';
  END IF;
 ELSE
  IF NEW.business_id<>OLD.business_id OR NEW.id<>OLD.id OR NEW.period_id<>OLD.period_id
   OR NEW.assigned_to<>OLD.assigned_to OR NEW.required<>OLD.required OR NEW.name<>OLD.name
   OR NEW.version<>OLD.version+1 THEN
   RAISE check_violation USING MESSAGE='Close task identity cannot change';
  END IF;
  IF NEW.state='prepared' AND (NEW.prepared_by IS DISTINCT FROM app.actor()
    OR NEW.assigned_to<>app.actor() OR NEW.reviewed_by IS NOT NULL) THEN
   RAISE insufficient_privilege USING MESSAGE='Only the assigned preparer can prepare';
  ELSIF NEW.state='approved' AND (OLD.state<>'prepared' OR NEW.prepared_by IS DISTINCT FROM OLD.prepared_by
    OR NEW.reviewed_by IS DISTINCT FROM app.actor() OR NEW.reviewed_by=NEW.prepared_by
    OR NOT app.has_role(NEW.business_id,ARRAY['owner','accountant','reviewer'])) THEN
   RAISE insufficient_privilege USING MESSAGE='Independent reviewer approval required';
  ELSIF NEW.state='open' AND (NOT app.has_role(NEW.business_id,ARRAY['owner','accountant'])
    OR NEW.prepared_by IS NOT NULL OR NEW.reviewed_by IS NOT NULL) THEN
   RAISE insufficient_privilege USING MESSAGE='Only an administrator can reopen';
  END IF;
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER close_task_guard BEFORE INSERT OR UPDATE ON app.close_tasks
FOR EACH ROW EXECUTE FUNCTION app.close_task_guard();
