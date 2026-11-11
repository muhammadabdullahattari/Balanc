"""Add opt-in workspace email and the durable local delivery outbox."""

from alembic import op
from sqlalchemy import text

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text("""
    ALTER TABLE app.notification_preferences
      ADD COLUMN email_reminders boolean NOT NULL DEFAULT false;
    CREATE TABLE app.business_email_jobs (
      id uuid PRIMARY KEY,
      business_id uuid NOT NULL REFERENCES app.businesses(id),
      recipient_id uuid NOT NULL REFERENCES app.users(id),
      kind varchar(32) NOT NULL CHECK (kind IN ('overdue_invoices','missing_evidence')),
      review_date date NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','sending','sent','suppressed','failed','uncertain')),
      attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
      available_at timestamptz NOT NULL DEFAULT now(),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      outcome_code varchar(50),
      UNIQUE (business_id,recipient_id,kind,review_date)
    );
    CREATE INDEX business_email_jobs_work
      ON app.business_email_jobs(business_id,recipient_id,state,available_at,id)
      WHERE state IN ('queued','sending');
    ALTER TABLE app.business_email_jobs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.business_email_jobs FORCE ROW LEVEL SECURITY;
    CREATE POLICY member_email_jobs ON app.business_email_jobs
      USING(business_id=app.scope() AND recipient_id=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']))
      WITH CHECK(business_id=app.scope() AND recipient_id=app.actor()
        AND app.has_role(business_id,ARRAY['owner','accountant','bookkeeper']));
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(text(f"GRANT SELECT,INSERT ON app.business_email_jobs TO {role}"))
    op.execute(
        text(
            "GRANT UPDATE(state,attempts,available_at,updated_at,outcome_code) "
            f"ON app.business_email_jobs TO {role}"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Preserve delivery evidence; use a forward repair")
