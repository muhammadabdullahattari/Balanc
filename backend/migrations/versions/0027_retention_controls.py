"""Versioned retention policy and durable legal holds."""

from alembic import op
from sqlalchemy import text

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
      CREATE TABLE app.retention_policies (
        id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
        category text NOT NULL CHECK (category IN
          ('journals','trade_documents','evidence','bank_records','audit_records')),
        jurisdiction varchar(80) NOT NULL CHECK (length(trim(jurisdiction)) >= 2),
        years integer NOT NULL CHECK (years BETWEEN 1 AND 100),
        version integer NOT NULL CHECK (version > 0),
        reference varchar(200) NOT NULL CHECK (length(trim(reference)) > 0),
        approved_by uuid NOT NULL REFERENCES app.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (business_id,category,jurisdiction,version)
      );
      CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.retention_policies
        FOR EACH ROW EXECUTE FUNCTION app.immutable();
      CREATE TABLE app.legal_holds (
        id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
        category text NOT NULL CHECK (category IN
          ('all','journals','trade_documents','evidence','bank_records','audit_records')),
        reason varchar(1000) NOT NULL CHECK (length(trim(reason)) > 0),
        placed_by uuid NOT NULL REFERENCES app.users(id),
        created_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz,
        released_by uuid REFERENCES app.users(id), release_reason varchar(1000),
        CHECK ((released_at IS NULL AND released_by IS NULL AND release_reason IS NULL)
          OR (released_at >= created_at AND released_by IS NOT NULL
              AND length(trim(release_reason)) > 0))
      );
      CREATE INDEX legal_holds_active ON app.legal_holds(business_id,category)
        WHERE released_at IS NULL;
      CREATE INDEX legal_holds_history ON app.legal_holds(business_id,created_at DESC,id);
      CREATE FUNCTION app.protect_hold_release() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog AS $$
      BEGIN
        IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL THEN
          RAISE EXCEPTION 'A hold can only be released once';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER release_once BEFORE UPDATE ON app.legal_holds
        FOR EACH ROW EXECUTE FUNCTION app.protect_hold_release();
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    for table, actor in (("retention_policies", "approved_by"), ("legal_holds", "placed_by")):
        op.execute(
            text(f"""
          ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY retention_read ON app.{table} FOR SELECT USING (
            business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']));
          CREATE POLICY retention_create ON app.{table} FOR INSERT WITH CHECK (
            business_id=app.scope() AND {actor}=app.actor()
            AND app.has_role(business_id,ARRAY['owner','accountant']));
          GRANT SELECT, INSERT ON app.{table} TO {role};
        """)
        )
    op.execute(
        text(f"""
      CREATE POLICY hold_release ON app.legal_holds FOR UPDATE USING (
        business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']))
      WITH CHECK (business_id=app.scope() AND released_by=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant']));
      GRANT UPDATE(released_at,released_by,release_reason) ON app.legal_holds TO {role};
    """)
    )


def downgrade() -> None:
    raise RuntimeError("Preserve retention and hold evidence; use a forward repair")
