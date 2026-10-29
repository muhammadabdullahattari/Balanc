"""Immutable reviewed CSV import history and supplied parallel-run baselines."""

from alembic import op
from sqlalchemy import text

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.get_bind().execute(text("""
CREATE TABLE app.import_batches (
 id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),file_hash varchar(64) NOT NULL,
 mode varchar(20) NOT NULL CHECK(mode IN ('chart','journal','trade')),cutover_date date NOT NULL,
 source text NOT NULL,results jsonb NOT NULL,expected_trial jsonb NOT NULL,
 created_by uuid NOT NULL REFERENCES app.users(id)
);
CREATE INDEX import_history ON app.import_batches(business_id,id);
ALTER TABLE app.import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.import_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.import_batches USING(business_id=app.scope() AND app.has_access(business_id))
 WITH CHECK(business_id=app.scope() AND app.has_access(business_id));
CREATE POLICY writer ON app.import_batches AS RESTRICTIVE FOR INSERT
 WITH CHECK(app.can_post(business_id) AND created_by=app.actor());
GRANT SELECT,INSERT ON app.import_batches TO __RUNTIME__;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.import_batches
 FOR EACH ROW EXECUTE FUNCTION app.immutable();
""".replace("__RUNTIME__", role)))


def downgrade() -> None:
    raise RuntimeError("Preserve source imports and baselines; correct posted history with journals")
