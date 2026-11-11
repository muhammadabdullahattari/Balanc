"""Add workspace email feedback and time-limited diagnostics delegation."""

from alembic import op
from sqlalchemy import text

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
    ALTER TABLE app.business_email_jobs ADD UNIQUE (business_id,id,recipient_id);
    CREATE INDEX business_email_jobs_health
      ON app.business_email_jobs(business_id,created_at DESC,id);
    CREATE TABLE app.business_email_feedback (
      id uuid PRIMARY KEY,
      business_id uuid NOT NULL,
      job_id uuid NOT NULL,
      recipient_id uuid NOT NULL,
      kind varchar(16) NOT NULL CHECK (kind IN ('bounced','complained')),
      recorded_by uuid NOT NULL REFERENCES app.users(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (job_id,kind),
      FOREIGN KEY (business_id,job_id,recipient_id)
        REFERENCES app.business_email_jobs(business_id,id,recipient_id)
    );
    CREATE INDEX business_email_feedback_recipient
      ON app.business_email_feedback(business_id,recipient_id);
    ALTER TABLE app.business_email_feedback ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.business_email_feedback FORCE ROW LEVEL SECURITY;
    CREATE POLICY email_feedback_read ON app.business_email_feedback FOR SELECT
      USING (business_id=app.scope() AND
        (app.has_role(business_id,ARRAY['owner','accountant']) OR
         (recipient_id=app.actor() AND
          app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']))));
    CREATE POLICY email_feedback_write ON app.business_email_feedback FOR INSERT
      WITH CHECK (business_id=app.scope() AND recorded_by=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant']));
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.business_email_feedback
      FOR EACH ROW EXECUTE FUNCTION app.immutable();

    CREATE TABLE app.business_email_support_grants (
      id uuid PRIMARY KEY,
      business_id uuid NOT NULL REFERENCES app.businesses(id),
      reviewer_id uuid NOT NULL REFERENCES app.users(id),
      granted_by uuid NOT NULL REFERENCES app.users(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      CHECK (expires_at > created_at AND expires_at <= created_at + interval '24 hours')
    );
    CREATE UNIQUE INDEX business_email_support_active
      ON app.business_email_support_grants(business_id,reviewer_id) WHERE revoked_at IS NULL;
    CREATE INDEX business_email_support_history
      ON app.business_email_support_grants(business_id,created_at DESC,id);
    ALTER TABLE app.business_email_support_grants ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.business_email_support_grants FORCE ROW LEVEL SECURITY;
    CREATE POLICY email_support_read ON app.business_email_support_grants FOR SELECT
      USING (business_id=app.scope() AND
        (app.has_role(business_id,ARRAY['owner','accountant']) OR
         (reviewer_id=app.actor() AND app.has_role(business_id,ARRAY['reviewer']))));
    CREATE POLICY email_support_create ON app.business_email_support_grants FOR INSERT
      WITH CHECK (business_id=app.scope() AND granted_by=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant']));
    CREATE POLICY email_support_revoke ON app.business_email_support_grants FOR UPDATE
      USING (business_id=app.scope() AND app.has_role(business_id,ARRAY['owner','accountant']))
      WITH CHECK (business_id=app.scope() AND revoked_at IS NOT NULL
        AND app.has_role(business_id,ARRAY['owner','accountant']));
    CREATE POLICY email_operations_read ON app.business_email_jobs FOR SELECT
      USING (business_id=app.scope() AND
        (app.has_role(business_id,ARRAY['owner','accountant']) OR
         EXISTS (SELECT 1 FROM app.business_email_support_grants grant_row
           WHERE grant_row.business_id=business_email_jobs.business_id
             AND grant_row.reviewer_id=app.actor() AND grant_row.revoked_at IS NULL
             AND grant_row.expires_at>statement_timestamp()
             AND app.has_role(grant_row.business_id,ARRAY['reviewer']))));
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(text(f"GRANT SELECT, INSERT ON app.business_email_feedback TO {role}"))
    op.execute(text(f"GRANT SELECT, INSERT ON app.business_email_support_grants TO {role}"))
    op.execute(text(f"GRANT UPDATE(revoked_at) ON app.business_email_support_grants TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve email feedback and support audit history; use a forward repair")
