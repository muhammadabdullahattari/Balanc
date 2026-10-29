"""Immutable category adjustments preserve the original bank movement and decision."""

from alembic import op
from sqlalchemy import text

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.get_bind().execute(text("""
CREATE TABLE app.bank_corrections (
 id uuid PRIMARY KEY,business_id uuid NOT NULL REFERENCES app.businesses(id),row_id uuid NOT NULL,
 accounting_date date NOT NULL,journal_id uuid NOT NULL,reason varchar(1000) NOT NULL,
 splits jsonb NOT NULL,created_by uuid NOT NULL REFERENCES app.users(id),UNIQUE(business_id,journal_id),
 FOREIGN KEY(business_id,row_id) REFERENCES app.bank_rows(business_id,id),
 FOREIGN KEY(business_id,journal_id) REFERENCES app.journals(business_id,id)
);
CREATE INDEX bank_correction_history ON app.bank_corrections(business_id,row_id,accounting_date);
ALTER TABLE app.bank_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.bank_corrections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.bank_corrections USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.bank_corrections AS RESTRICTIVE FOR INSERT WITH CHECK(app.can_post(business_id)
 AND created_by=app.actor());
GRANT SELECT,INSERT ON app.bank_corrections TO __RUNTIME__;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.bank_corrections
 FOR EACH ROW EXECUTE FUNCTION app.immutable();
CREATE CONSTRAINT TRIGGER source_link AFTER INSERT ON app.bank_corrections DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app.source_link_guard();
""".replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve corrections and their original source links")
