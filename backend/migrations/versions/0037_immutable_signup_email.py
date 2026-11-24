"""Keep the signup email fixed for the lifetime of an identity."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION app.reject_email_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ERR_EMAIL_IMMUTABLE';
        END $$;
        CREATE TRIGGER immutable_signup_email BEFORE UPDATE ON app.users
        FOR EACH ROW WHEN (OLD.email IS DISTINCT FROM NEW.email)
        EXECUTE FUNCTION app.reject_email_change();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_signup_email ON app.users")
    op.execute("DROP FUNCTION app.reject_email_change()")
