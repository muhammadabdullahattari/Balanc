"""Verification issuance limits and optional Google federation under owned sessions."""

from alembic import op
from sqlalchemy import text

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text("""
    ALTER TABLE app.users ADD COLUMN verification_attempts integer NOT NULL DEFAULT 0
      CHECK(verification_attempts BETWEEN 0 AND 3),
      ADD COLUMN verification_started_at timestamptz,
      ADD COLUMN verification_blocked_until timestamptz;
    CREATE INDEX action_tokens_user_purpose ON app.action_tokens(user_id,purpose);
    CREATE TABLE app.google_identities (
      subject varchar(255) PRIMARY KEY, user_id uuid NOT NULL UNIQUE REFERENCES app.users(id)
    );
    CREATE TABLE app.google_challenges (
      nonce_hash varchar(64) PRIMARY KEY, csrf_hash varchar(64) NOT NULL,
      expires_at timestamptz NOT NULL, used_at timestamptz
    );
    ALTER TABLE app.google_identities ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.google_identities FORCE ROW LEVEL SECURITY;
    CREATE POLICY google_read ON app.google_identities FOR SELECT USING (
      user_id=app.actor() OR subject=nullif(current_setting('app.google_subject',true),'')
    );
    CREATE POLICY google_insert ON app.google_identities FOR INSERT WITH CHECK(user_id=app.actor());
    ALTER TABLE app.google_challenges ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.google_challenges FORCE ROW LEVEL SECURITY;
    CREATE POLICY challenge_scope ON app.google_challenges USING (
      nonce_hash=nullif(current_setting('app.google_nonce_hash',true),'')
    ) WITH CHECK(nonce_hash=nullif(current_setting('app.google_nonce_hash',true),''));
    """)
    )
    role = op.get_bind().dialect.identifier_preparer.quote(
        op.get_context().config.attributes["runtime_role"]
    )
    op.execute(text(f"GRANT SELECT,INSERT ON app.google_identities TO {role}"))
    op.execute(text(f"GRANT SELECT,INSERT,UPDATE ON app.google_challenges TO {role}"))


def downgrade() -> None:
    raise RuntimeError("Preserve identity history; use a forward repair")
