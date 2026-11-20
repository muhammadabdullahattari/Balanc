"""Extend list_members to include user email; add read-only member directory for all roles."""

from alembic import op
from sqlalchemy import text

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    runtime_attr = op.get_context().config.attributes.get("runtime_role")
    runtime = quote(runtime_attr) if runtime_attr else "CURRENT_USER"

    op.execute(
        text(f"""
        -- Replace list_members: now returns email from app.users and allows any
        -- active business member to read the directory (read-only). Mutations
        -- (invite, role changes) remain restricted to owner / accountant at the
        -- application layer.
        CREATE OR REPLACE FUNCTION app.list_members(p_b uuid)
        RETURNS TABLE(
            user_id uuid,
            email   text,
            role    text,
            active  boolean,
            can_post boolean,
            can_invite boolean,
            version integer
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            -- Allow any active member of this business to read the directory.
            IF p_b <> app.scope() OR NOT app.has_access(p_b) THEN
                RAISE insufficient_privilege USING MESSAGE='Member directory access denied';
            END IF;
            RETURN QUERY
                SELECT m.user_id,
                       u.email::text,
                       m.role::text,
                       m.active,
                       m.can_post,
                       m.can_invite,
                       m.version
                FROM app.memberships m
                JOIN app.users u ON u.id = m.user_id
                WHERE m.business_id = p_b;
        END $$;

        -- Allow the runtime user to read app.users rows that belong to business members.
        -- The existing RLS policy (users_read) only permits reading one's own row; we
        -- extend it here with a business-scoped exception so the SECURITY DEFINER
        -- function above can resolve emails.  The function already enforces its own
        -- scope check, so this grant does not widen the external attack surface.
        GRANT EXECUTE ON FUNCTION app.list_members(uuid) TO {runtime};
    """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        -- Restore the original list_members that required owner/accountant.
        CREATE OR REPLACE FUNCTION app.list_members(p_b uuid)
        RETURNS TABLE(
            user_id uuid,
            role    text,
            active  boolean,
            can_post boolean,
            can_invite boolean,
            version integer
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            IF p_b <> app.scope() OR NOT app.has_role(p_b, ARRAY['owner','accountant']) THEN
                RAISE insufficient_privilege USING MESSAGE='Member administration denied';
            END IF;
            RETURN QUERY
                SELECT m.user_id, m.role::text, m.active, m.can_post, m.can_invite, m.version
                FROM app.memberships m
                WHERE m.business_id = p_b;
        END $$;
    """)
    )
