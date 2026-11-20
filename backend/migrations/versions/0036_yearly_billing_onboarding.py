"""Add onboarding yearly subscriptions table and scoped Stripe billing email lookups."""

from alembic import op
from sqlalchemy import text

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def _names() -> tuple[str, str]:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    runtime = quote(op.get_context().config.attributes["runtime_role"])
    owner_raw = connection.execute(text("SELECT current_user")).scalar_one()
    return runtime, quote(owner_raw)


def upgrade() -> None:
    runtime, owner = _names()
    op.execute(
        text(f"""
        CREATE TABLE app.onboarding_subscriptions (
            checkout_session_id text PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES app.users(id),
            plan text NOT NULL CHECK (plan IN ('starter', 'growth', 'enterprise')),
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'paid', 'claimed', 'canceled', 'expired')),
            stripe_customer_id text,
            stripe_subscription_id text,
            business_id uuid REFERENCES app.businesses(id),
            amount_paid bigint,
            currency text,
            invoice_id text,
            invoice_url text,
            invoice_pdf text,
            email_sent_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_onboarding_subscriptions_user
            ON app.onboarding_subscriptions (user_id, created_at DESC);
        CREATE INDEX ix_onboarding_subscriptions_customer
            ON app.onboarding_subscriptions (stripe_customer_id)
            WHERE stripe_customer_id IS NOT NULL;

        ALTER TABLE app.onboarding_subscriptions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.onboarding_subscriptions FORCE ROW LEVEL SECURITY;

        CREATE POLICY onboarding_self ON app.onboarding_subscriptions
            USING (user_id = app.actor())
            WITH CHECK (user_id = app.actor());
        CREATE POLICY onboarding_webhook_definer ON app.onboarding_subscriptions TO {owner}
            USING (true) WITH CHECK (true);

        GRANT SELECT, INSERT, UPDATE ON app.onboarding_subscriptions TO {runtime};

        CREATE POLICY users_stripe_owner_lookup ON app.users FOR SELECT TO {owner}
            USING (
                EXISTS (
                    SELECT 1 FROM app.memberships m
                    WHERE m.user_id = app.users.id AND m.role = 'owner' AND m.active
                ) OR EXISTS (
                    SELECT 1 FROM app.onboarding_subscriptions o
                    WHERE o.user_id = app.users.id
                )
            );

        CREATE FUNCTION app.stripe_business_owner_email(bid uuid) RETURNS text
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT u.email::text
            FROM app.memberships m
            JOIN app.users u ON u.id = m.user_id
            WHERE m.business_id = bid AND m.role = 'owner' AND m.active
            ORDER BY u.created_at ASC
            LIMIT 1
        $$;

        CREATE FUNCTION app.stripe_onboarding_mark_paid(
            p_session_id text,
            p_customer_id text,
            p_subscription_id text,
            p_amount_paid bigint,
            p_currency text,
            p_invoice_id text,
            p_invoice_url text,
            p_invoice_pdf text
        ) RETURNS TABLE(user_id uuid, email text, plan text, already_emailed boolean)
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE
            rec app.onboarding_subscriptions;
        BEGIN
            SELECT * INTO rec
            FROM app.onboarding_subscriptions
            WHERE checkout_session_id = p_session_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN;
            END IF;
            UPDATE app.onboarding_subscriptions
            SET status = CASE WHEN status = 'claimed' THEN 'claimed' ELSE 'paid' END,
                stripe_customer_id = COALESCE(p_customer_id, stripe_customer_id),
                stripe_subscription_id = COALESCE(p_subscription_id, stripe_subscription_id),
                amount_paid = COALESCE(p_amount_paid, amount_paid),
                currency = COALESCE(p_currency, currency),
                invoice_id = COALESCE(p_invoice_id, invoice_id),
                invoice_url = COALESCE(p_invoice_url, invoice_url),
                invoice_pdf = COALESCE(p_invoice_pdf, invoice_pdf),
                updated_at = now()
            WHERE checkout_session_id = p_session_id;
            RETURN QUERY
            SELECT rec.user_id, u.email::text, rec.plan, (rec.email_sent_at IS NOT NULL)
            FROM app.users u
            WHERE u.id = rec.user_id;
        END $$;

        CREATE FUNCTION app.stripe_onboarding_mark_emailed(p_session_id text) RETURNS void
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
            UPDATE app.onboarding_subscriptions
            SET email_sent_at = COALESCE(email_sent_at, now()),
                updated_at = now()
            WHERE checkout_session_id = p_session_id
        $$;

        CREATE FUNCTION app.stripe_onboarding_by_customer(p_customer_id text)
            RETURNS TABLE(
                checkout_session_id text,
                user_id uuid,
                email text,
                plan text,
                business_id uuid,
                already_emailed boolean
            )
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT o.checkout_session_id,
                   o.user_id,
                   u.email::text,
                   o.plan,
                   o.business_id,
                   (o.email_sent_at IS NOT NULL)
            FROM app.onboarding_subscriptions o
            JOIN app.users u ON u.id = o.user_id
            WHERE o.stripe_customer_id = p_customer_id
            ORDER BY o.created_at DESC
            LIMIT 1
        $$;

        REVOKE ALL ON FUNCTION
            app.stripe_business_owner_email(uuid),
            app.stripe_onboarding_mark_paid(text,text,text,bigint,text,text,text,text),
            app.stripe_onboarding_mark_emailed(text),
            app.stripe_onboarding_by_customer(text)
        FROM PUBLIC;

        GRANT EXECUTE ON FUNCTION
            app.stripe_business_owner_email(uuid),
            app.stripe_onboarding_mark_paid(text,text,text,bigint,text,text,text,text),
            app.stripe_onboarding_mark_emailed(text),
            app.stripe_onboarding_by_customer(text)
        TO {runtime};
        """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DROP FUNCTION IF EXISTS app.stripe_onboarding_by_customer(text);
        DROP FUNCTION IF EXISTS app.stripe_onboarding_mark_emailed(text);
        DROP FUNCTION IF EXISTS app.stripe_onboarding_mark_paid(text,text,text,bigint,text,text,text,text);
        DROP FUNCTION IF EXISTS app.stripe_business_owner_email(uuid);
        DROP POLICY IF EXISTS users_stripe_owner_lookup ON app.users;
        DROP TABLE IF EXISTS app.onboarding_subscriptions;
        """)
    )
