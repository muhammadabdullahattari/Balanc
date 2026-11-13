"""Share selected immutable workpapers without granting workspace membership."""

from alembic import op
from sqlalchemy import text

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
      CREATE TABLE app.audit_report_grants (
        id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
        recipient_id uuid NOT NULL REFERENCES app.users(id),
        granted_by uuid NOT NULL REFERENCES app.users(id),
        report_ids uuid[] NOT NULL CHECK(cardinality(report_ids) BETWEEN 1 AND 20),
        reason varchar(1000) NOT NULL CHECK(length(trim(reason)) > 0),
        created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        CHECK(expires_at > created_at AND expires_at <= created_at + interval '30 days')
      );
      CREATE INDEX audit_report_grants_recipient ON app.audit_report_grants(recipient_id,id)
        WHERE revoked_at IS NULL;
      CREATE INDEX audit_report_grants_business ON app.audit_report_grants(business_id,id);
      ALTER TABLE app.audit_report_grants ENABLE ROW LEVEL SECURITY;
      ALTER TABLE app.audit_report_grants FORCE ROW LEVEL SECURITY;
      CREATE POLICY grant_read ON app.audit_report_grants FOR SELECT USING (
        recipient_id=app.actor() OR (business_id=app.scope()
          AND app.has_role(business_id,ARRAY['owner','accountant'])));
      CREATE POLICY grant_create ON app.audit_report_grants FOR INSERT WITH CHECK (
        business_id=app.scope() AND granted_by=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant']));
      CREATE POLICY grant_revoke ON app.audit_report_grants FOR UPDATE USING (
        business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']))
        WITH CHECK (business_id=app.scope() AND revoked_at IS NOT NULL
          AND app.has_role(business_id,ARRAY['owner','accountant']));
      CREATE FUNCTION app.audit_grant_guard() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog AS $$ BEGIN
        IF TG_OP='INSERT' THEN
          IF cardinality(NEW.report_ids) <> (SELECT count(*) FROM app.report_runs r
               WHERE r.business_id=NEW.business_id AND r.id=ANY(NEW.report_ids)) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Choose distinct workspace reports';
          END IF;
        ELSIF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL THEN
          RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='A grant can only be revoked once';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER audit_grant_guard BEFORE INSERT OR UPDATE ON app.audit_report_grants
        FOR EACH ROW EXECUTE FUNCTION app.audit_grant_guard();
      CREATE POLICY scoped_audit_read ON app.report_runs FOR SELECT USING (
        business_id=app.scope() AND EXISTS (SELECT 1 FROM app.audit_report_grants g
          WHERE g.business_id=report_runs.business_id AND g.recipient_id=app.actor()
            AND g.revoked_at IS NULL AND g.expires_at>statement_timestamp()
            AND report_runs.id=ANY(g.report_ids)));
      CREATE POLICY scoped_audit_event ON app.audit_events FOR INSERT WITH CHECK (
        user_id=app.actor() AND business_id=app.scope() AND action='audit_access.report_read'
        AND EXISTS(SELECT 1 FROM app.audit_report_grants g WHERE g.id=resource_id
          AND g.business_id=audit_events.business_id AND g.recipient_id=app.actor()
          AND g.revoked_at IS NULL AND g.expires_at>statement_timestamp()));
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(text(f"GRANT SELECT,INSERT ON app.audit_report_grants TO {role}"))
    op.execute(text(f"GRANT UPDATE(revoked_at) ON app.audit_report_grants TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve audit-sharing history; use a forward repair")
