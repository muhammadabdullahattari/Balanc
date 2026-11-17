"""Scope Stripe webhook access and index the provider customer lookup."""

from alembic import op
from sqlalchemy import text

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    runtime = quote(op.get_context().config.attributes["runtime_role"])
    owner = quote(conn.execute(text("SELECT current_user")).scalar_one())
    op.execute(
        text(f"""
        CREATE INDEX stripe_customer_lookup ON app.provider_connections
          ((details->>'customer')) WHERE provider='stripe';
        CREATE POLICY stripe_webhook_definer ON app.provider_connections TO {owner}
          USING (provider='stripe') WITH CHECK (provider='stripe');
        CREATE FUNCTION app.stripe_claim_event(event_id text, kind text) RETURNS boolean
          LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          WITH inserted AS (
            INSERT INTO app.stripe_event_inbox(id, event_type)
              VALUES(event_id, kind) ON CONFLICT(id) DO NOTHING RETURNING id
          ) SELECT EXISTS(SELECT 1 FROM inserted)
        $$;
        CREATE FUNCTION app.stripe_webhook_customer(cust text) RETURNS uuid
          LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT business_id FROM app.provider_connections
          WHERE provider='stripe' AND details->>'customer'=cust
        $$;
        CREATE FUNCTION app.stripe_webhook_load(bid uuid) RETURNS jsonb
          LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT details FROM app.provider_connections
          WHERE business_id=bid AND provider='stripe' FOR UPDATE
        $$;
        CREATE FUNCTION app.stripe_webhook_save(bid uuid, payload jsonb) RETURNS void
          LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          UPDATE app.provider_connections SET details=payload
            WHERE business_id=bid AND provider='stripe'
        $$;
        CREATE FUNCTION app.stripe_entitlement(bid uuid) RETURNS jsonb
          LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT details->'snapshot' FROM app.provider_connections
          WHERE business_id=bid AND provider='stripe' AND bid=app.scope()
            AND app.has_access(bid)
        $$;
        REVOKE ALL ON FUNCTION app.stripe_claim_event(text,text), app.stripe_webhook_customer(text),
          app.stripe_webhook_load(uuid), app.stripe_webhook_save(uuid,jsonb),
          app.stripe_entitlement(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.stripe_claim_event(text,text),
          app.stripe_webhook_customer(text),
          app.stripe_webhook_load(uuid), app.stripe_webhook_save(uuid,jsonb),
          app.stripe_entitlement(uuid) TO {runtime};
    """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DROP FUNCTION app.stripe_claim_event(text,text), app.stripe_entitlement(uuid),
          app.stripe_webhook_save(uuid,jsonb),
          app.stripe_webhook_load(uuid), app.stripe_webhook_customer(text);
        DROP POLICY stripe_webhook_definer ON app.provider_connections;
        DROP INDEX app.stripe_customer_lookup;
    """)
    )
