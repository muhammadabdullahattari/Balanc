"""Versioned operational sale and bill drafts, separate from the posted ledger."""

from alembic import op
from sqlalchemy import text

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.get_bind().execute(text("""
CREATE TABLE app.source_drafts (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id), key varchar(100) NOT NULL,
 payload jsonb NOT NULL, status varchar(20) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','issued','void')),
 version integer NOT NULL DEFAULT 0, document_id uuid, prepared_by uuid NOT NULL REFERENCES app.users(id),
 UNIQUE(business_id,key), FOREIGN KEY(business_id,document_id) REFERENCES app.trade_documents(business_id,id),
 CHECK((status='issued')=(document_id IS NOT NULL))
);
ALTER TABLE app.source_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.source_drafts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.source_drafts USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.source_drafts AS RESTRICTIVE FOR INSERT
 WITH CHECK(app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']) AND prepared_by=app.actor());
CREATE POLICY editor ON app.source_drafts AS RESTRICTIVE FOR UPDATE
 USING(app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
GRANT SELECT,INSERT,UPDATE ON app.source_drafts TO __RUNTIME__;
CREATE FUNCTION app.source_draft_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF OLD.status<>'draft' OR NEW.version<>OLD.version+1 OR NEW.business_id<>OLD.business_id
 OR NEW.id<>OLD.id OR NEW.key<>OLD.key OR NEW.prepared_by<>OLD.prepared_by THEN
  RAISE check_violation USING MESSAGE='Only the current draft can change';
 END IF;
 IF NEW.status='issued' AND (NOT app.can_post(NEW.business_id) OR NEW.payload<>OLD.payload) THEN
  RAISE insufficient_privilege USING MESSAGE='Posting permission and approved draft content required';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER source_draft_guard BEFORE UPDATE ON app.source_drafts
 FOR EACH ROW EXECUTE FUNCTION app.source_draft_guard();
""".replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve source drafts and approvals through a forward repair")
