"""Scoped policies and durable disposal evidence; no destructive defaults."""

from alembic import op
from sqlalchemy import text

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
    ALTER TABLE app.legal_holds ADD COLUMN kind text NOT NULL DEFAULT 'legal'
      CHECK(kind IN ('legal','regulatory','exception'));
    CREATE TABLE app.cleanup_policies (
      id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
      record_type text NOT NULL, location text NOT NULL,
      retention_days integer NOT NULL CHECK(retention_days BETWEEN 1 AND 36525),
      approval_role text NOT NULL CHECK(approval_role IN ('owner','accountant')),
      reference text NOT NULL, created_by uuid NOT NULL REFERENCES app.users(id),
      approved_by uuid REFERENCES app.users(id), approved_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(business_id,id),
      CHECK((approved_by IS NULL) = (approved_at IS NULL)),
      CHECK(approved_by IS NULL OR approved_by <> created_by)
    );
    CREATE TABLE app.cleanup_requests (
      id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
      policy_id uuid NOT NULL, resource_id uuid NOT NULL, record_date date NOT NULL,
      source_expires_at timestamptz,
      reference text NOT NULL, created_by uuid NOT NULL REFERENCES app.users(id),
      approved_by uuid REFERENCES app.users(id), approved_at timestamptz,
      status text NOT NULL DEFAULT 'preview' CHECK(status IN
        ('preview','approved','failed','awaiting_confirmation','verified','confirmed')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0), outcome text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(business_id,id),
      UNIQUE(business_id,policy_id,resource_id),
      FOREIGN KEY(business_id,policy_id) REFERENCES app.cleanup_policies(business_id,id),
      CHECK((approved_by IS NULL) = (approved_at IS NULL)),
      CHECK(approved_by IS NULL OR approved_by <> created_by)
    );
    CREATE TABLE app.cleanup_events (
      id uuid PRIMARY KEY, business_id uuid NOT NULL REFERENCES app.businesses(id),
      request_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES app.users(id),
      action text NOT NULL, detail jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(business_id,request_id) REFERENCES app.cleanup_requests(business_id,id)
    );
    CREATE INDEX cleanup_policy_scope ON app.cleanup_policies(business_id,created_at,id);
    CREATE INDEX cleanup_request_scope ON app.cleanup_requests(business_id,created_at,id);
    CREATE INDEX cleanup_event_scope ON app.cleanup_events(business_id,request_id,created_at,id);
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.cleanup_events
      FOR EACH ROW EXECUTE FUNCTION app.immutable();
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    for table in ("cleanup_policies", "cleanup_requests", "cleanup_events"):
        op.execute(
            text(f"""
        ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.{table} USING
          (business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']))
          WITH CHECK (business_id=app.scope()
            AND app.has_role(business_id,ARRAY['owner','accountant']));
        GRANT SELECT,INSERT ON app.{table} TO {role};
        """)
        )
    op.execute(
        text(f"""
      GRANT UPDATE(approved_by,approved_at) ON app.cleanup_policies TO {role};
      GRANT UPDATE(approved_by,approved_at,status,attempts,outcome)
        ON app.cleanup_requests TO {role};
    """)
    )


def downgrade() -> None:
    raise RuntimeError("Preserve disposal approval and audit evidence; use a forward repair")
