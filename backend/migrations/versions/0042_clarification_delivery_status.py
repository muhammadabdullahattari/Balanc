"""Add 'sent' and 'delivery_failed' statuses to ai_client_clarifications."""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
          cname text;
        BEGIN
          FOR cname IN
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = 'app'
              AND rel.relname = 'ai_client_clarifications'
              AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) LIKE '%status%'
          LOOP
            EXECUTE format(
              'ALTER TABLE app.ai_client_clarifications DROP CONSTRAINT %I',
              cname
            );
          END LOOP;
        END $$;

        ALTER TABLE app.ai_client_clarifications
          ALTER COLUMN status SET DEFAULT 'sent',
          ADD CONSTRAINT ai_client_clarifications_status_check CHECK (
            status IN (
              'draft',
              'sent',
              'sent_sandbox',
              'delivery_failed',
              'partially_answered',
              'resolved_closed',
              'opted_out',
              'escalated_dispute'
            )
          );
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE app.ai_client_clarifications
        SET status = 'sent_sandbox'
        WHERE status IN ('sent', 'delivery_failed');

        ALTER TABLE app.ai_client_clarifications
          DROP CONSTRAINT IF EXISTS ai_client_clarifications_status_check,
          ALTER COLUMN status SET DEFAULT 'sent_sandbox',
          ADD CONSTRAINT ai_client_clarifications_status_check CHECK (
            status IN (
              'draft',
              'sent_sandbox',
              'partially_answered',
              'resolved_closed',
              'opted_out',
              'escalated_dispute'
            )
          );
    """)
