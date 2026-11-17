"""Add durable shared background jobs table, concurrency controls and worker functions."""

from alembic import op
from sqlalchemy import text

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    runtime_attr = op.get_context().config.attributes.get("runtime_role")
    runtime = quote(runtime_attr) if runtime_attr else "CURRENT_USER"

    op.execute(
        text(f"""
        CREATE TABLE app.background_jobs (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES app.businesses(id),
            created_by uuid NOT NULL REFERENCES app.users(id),
            queue varchar(64) NOT NULL DEFAULT 'default',
            kind varchar(64) NOT NULL,
            idempotency_key varchar(255),
            payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            state varchar(32) NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'dead_letter')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
            backoff_base_seconds numeric(8,2) NOT NULL DEFAULT 2.0,
            timeout_seconds integer NOT NULL DEFAULT 300 CHECK (timeout_seconds >= 1),
            available_at timestamptz NOT NULL DEFAULT now(),
            lease_token uuid,
            lease_until timestamptz,
            started_at timestamptz,
            finished_at timestamptz,
            result jsonb,
            error_detail jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX background_jobs_work ON app.background_jobs (queue, state, available_at)
            WHERE state IN ('queued', 'running');

        CREATE INDEX background_jobs_business ON app.background_jobs (business_id, created_at);

        CREATE UNIQUE INDEX background_jobs_idempotency
            ON app.background_jobs (business_id, kind, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        ALTER TABLE app.background_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.background_jobs FORCE ROW LEVEL SECURITY;

        CREATE POLICY background_jobs_tenant ON app.background_jobs
            USING (business_id = app.scope() AND app.has_access(business_id))
            WITH CHECK (business_id = app.scope() AND app.has_access(business_id));

        GRANT SELECT, INSERT, UPDATE ON app.background_jobs TO {runtime};

        CREATE OR REPLACE FUNCTION app.claim_background_job(
            p_queue text,
            p_worker_token uuid,
            p_lease_seconds integer,
            p_concurrency_limit integer
        ) RETURNS TABLE(
            id uuid,
            business_id uuid,
            created_by uuid,
            queue varchar(64),
            kind varchar(64),
            payload jsonb,
            attempts integer,
            max_attempts integer,
            timeout_seconds integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            active_count integer;
            chosen app.background_jobs;
        BEGIN
            SELECT count(*) INTO active_count
            FROM app.background_jobs j
            WHERE j.queue = p_queue
              AND j.state = 'running'
              AND j.lease_until > now();

            IF active_count >= p_concurrency_limit THEN
                RETURN;
            END IF;

            UPDATE app.background_jobs j
            SET state = CASE WHEN j.attempts >= j.max_attempts THEN 'dead_letter' ELSE 'queued' END,
                available_at = now() + interval '5 seconds',
                lease_token = NULL,
                lease_until = NULL,
                error_detail = jsonb_build_object('error', 'Worker lease expired / timeout'),
                updated_at = now()
            WHERE j.queue = p_queue
              AND j.state = 'running'
              AND j.lease_until < now();

            SELECT * INTO chosen
            FROM app.background_jobs j
            WHERE j.queue = p_queue
              AND j.state = 'queued'
              AND j.available_at <= now()
            ORDER BY j.available_at, j.id
            FOR UPDATE SKIP LOCKED
            LIMIT 1;

            IF NOT FOUND THEN
                RETURN;
            END IF;

            UPDATE app.background_jobs j
            SET state = 'running',
                attempts = j.attempts + 1,
                started_at = coalesce(j.started_at, now()),
                lease_token = p_worker_token,
                lease_until = now() + (p_lease_seconds || ' seconds')::interval,
                updated_at = now()
            WHERE j.id = chosen.id
            RETURNING j.* INTO chosen;

            RETURN QUERY SELECT
                chosen.id,
                chosen.business_id,
                chosen.created_by,
                chosen.queue,
                chosen.kind,
                chosen.payload,
                chosen.attempts,
                chosen.max_attempts,
                chosen.timeout_seconds;
        END;
        $$;

        CREATE OR REPLACE FUNCTION app.finish_background_job(
            p_id uuid,
            p_worker_token uuid,
            p_result jsonb
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            updated_rows integer;
        BEGIN
            UPDATE app.background_jobs
            SET state = 'succeeded',
                finished_at = now(),
                lease_token = NULL,
                lease_until = NULL,
                result = p_result,
                updated_at = now()
            WHERE id = p_id AND lease_token = p_worker_token AND state = 'running';
            GET DIAGNOSTICS updated_rows = ROW_COUNT;
            RETURN updated_rows > 0;
        END;
        $$;

        CREATE OR REPLACE FUNCTION app.fail_background_job(
            p_id uuid,
            p_worker_token uuid,
            p_error jsonb,
            p_backoff_seconds numeric
        ) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            cur_attempts integer;
            max_att integer;
            target_state text;
        BEGIN
            SELECT attempts, max_attempts INTO cur_attempts, max_att
            FROM app.background_jobs
            WHERE id = p_id AND lease_token = p_worker_token AND state = 'running';

            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;

            IF cur_attempts >= max_att THEN
                target_state := 'dead_letter';
            ELSE
                target_state := 'queued';
            END IF;

            UPDATE app.background_jobs
            SET state = target_state,
                lease_token = NULL,
                lease_until = NULL,
                error_detail = p_error,
                finished_at = CASE WHEN target_state = 'dead_letter' THEN now() ELSE NULL END,
                available_at = CASE WHEN target_state = 'queued'
                    THEN now() + (p_backoff_seconds || ' seconds')::interval
                    ELSE available_at END,
                updated_at = now()
            WHERE id = p_id;

            RETURN target_state;
        END;
        $$;

        CREATE OR REPLACE FUNCTION app.replay_background_job(p_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            updated_rows integer;
        BEGIN
            UPDATE app.background_jobs
            SET state = 'queued',
                attempts = 0,
                available_at = now(),
                lease_token = NULL,
                lease_until = NULL,
                started_at = NULL,
                finished_at = NULL,
                error_detail = NULL,
                updated_at = now()
            WHERE id = p_id AND state IN ('failed', 'dead_letter');
            GET DIAGNOSTICS updated_rows = ROW_COUNT;
            RETURN updated_rows > 0;
        END;
        $$;

        REVOKE ALL ON FUNCTION app.claim_background_job(text, uuid, integer, integer),
            app.finish_background_job(uuid, uuid, jsonb),
            app.fail_background_job(uuid, uuid, jsonb, numeric),
            app.replay_background_job(uuid) FROM PUBLIC;

        GRANT EXECUTE ON FUNCTION app.claim_background_job(text, uuid, integer, integer),
            app.finish_background_job(uuid, uuid, jsonb),
            app.fail_background_job(uuid, uuid, jsonb, numeric),
            app.replay_background_job(uuid) TO {runtime};
    """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DROP FUNCTION IF EXISTS app.replay_background_job(uuid);
        DROP FUNCTION IF EXISTS app.fail_background_job(uuid, uuid, jsonb, numeric);
        DROP FUNCTION IF EXISTS app.finish_background_job(uuid, uuid, jsonb);
        DROP FUNCTION IF EXISTS app.claim_background_job(text, uuid, integer, integer);
        DROP TABLE IF EXISTS app.background_jobs;
    """)
    )
