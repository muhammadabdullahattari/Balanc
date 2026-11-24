"""Team directory: return member email and allow read-only access for every active member.

Why a DROP/CREATE: PostgreSQL cannot change the OUT-row type of an existing
function with CREATE OR REPLACE.

Why a new app.users policy: app.users has FORCE ROW LEVEL SECURITY, so even the
SECURITY DEFINER owner only sees the caller's own row. The added policy mirrors
the existing ``memberships.scoped_admin`` pattern: it only applies to the
migration-owner role (never the runtime role) and only to users who hold a
membership in the verified transaction business scope.
"""

from alembic import op
from sqlalchemy import text

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def _names() -> tuple[str, str]:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    runtime = quote(op.get_context().config.attributes["runtime_role"])
    owner = connection.execute(text("SELECT current_user")).scalar_one()
    return runtime, "'" + owner.replace("'", "''") + "'"


def upgrade() -> None:
    runtime, owner = _names()
    op.execute(
        text(f"""
        CREATE POLICY users_scoped_directory ON app.users FOR SELECT USING (
            current_user = {owner} AND EXISTS (
                SELECT 1 FROM app.memberships m
                WHERE m.user_id = app.users.id AND m.business_id = app.scope()
            )
        );
        DROP FUNCTION app.list_members(uuid);
        CREATE FUNCTION app.list_members(p_b uuid) RETURNS TABLE(
            user_id uuid, email text, role text, active boolean,
            can_post boolean, can_invite boolean, version integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
            IF p_b <> app.scope() OR NOT app.has_access(p_b) THEN
                RAISE insufficient_privilege USING MESSAGE='Member directory access denied';
            END IF;
            RETURN QUERY SELECT m.user_id, u.email::text, m.role::text, m.active,
                m.can_post, m.can_invite, m.version
            FROM app.memberships m
            LEFT JOIN app.users u ON u.id = m.user_id
            WHERE m.business_id = p_b
            ORDER BY m.role <> 'owner', u.email;
        END $$;
        REVOKE ALL ON FUNCTION app.list_members(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.list_members(uuid) TO {runtime};
        """)
    )


def downgrade() -> None:
    runtime, _ = _names()
    op.execute(
        text(f"""
        DROP POLICY IF EXISTS users_scoped_directory ON app.users;
        DROP FUNCTION app.list_members(uuid);
        CREATE FUNCTION app.list_members(p_b uuid) RETURNS TABLE(
            user_id uuid, role text, active boolean, can_post boolean,
            can_invite boolean, version integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
            IF p_b <> app.scope() OR NOT app.has_role(p_b, ARRAY['owner','accountant']) THEN
                RAISE insufficient_privilege USING MESSAGE='Member administration denied';
            END IF;
            RETURN QUERY SELECT m.user_id, m.role::text, m.active, m.can_post,
                m.can_invite, m.version
            FROM app.memberships m WHERE m.business_id = p_b;
        END $$;
        REVOKE ALL ON FUNCTION app.list_members(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.list_members(uuid) TO {runtime};
        """)
    )
