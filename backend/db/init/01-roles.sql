-- Bootstrap separate local roles before any application schema is created.
-- This script runs only when the PostgreSQL data directory is initialized.
\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\getenv migration_user MIGRATION_DB_USER
\getenv migration_password MIGRATION_DB_PASSWORD
\getenv database_name POSTGRES_DB
\getenv admin_user POSTGRES_USER
\getenv admin_password POSTGRES_PASSWORD

BEGIN;

-- Compose can invoke this script directly, bypassing the Python setup checker.
-- Reject collisions and unsafe existing roles before altering any credentials.
SELECT (
    current_user = :'admin_user' AND current_database() = :'database_name'
    AND :'app_user' ~ '^[a-z][a-z0-9_]{0,62}$'
    AND :'migration_user' ~ '^[a-z][a-z0-9_]{0,62}$'
    AND :'app_user' NOT LIKE 'pg_%' AND :'migration_user' NOT LIKE 'pg_%'
    AND :'app_user' <> :'migration_user'
    AND :'app_user' <> :'admin_user' AND :'migration_user' <> :'admin_user'
    AND length(:'app_password') >= 16 AND length(:'migration_password') >= 16
    AND :'app_password' NOT LIKE 'replace-%'
    AND :'migration_password' NOT LIKE 'replace-%'
    AND :'app_password' <> :'migration_password'
    AND :'app_password' <> :'admin_password'
    AND :'migration_password' <> :'admin_password'
    AND NOT EXISTS (
        SELECT 1 FROM pg_roles r
        WHERE r.rolname IN (:'app_user', :'migration_user')
          AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
               OR r.rolreplication OR r.rolbypassrls
               OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid))
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
        WHERE n.nspname = 'app' AND r.rolname <> :'migration_user'
    )
) AS bootstrap_safe
\gset
\if :bootstrap_safe
\else
    \echo 'Unsafe bootstrap configuration or existing role/schema ownership; no changes applied.'
    DO $$ BEGIN RAISE EXCEPTION 'Bootstrap validation failed'; END $$;
\endif

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS',
    :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS',
    :'migration_user', :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user')
\gexec

ALTER ROLE :"app_user" WITH LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE :"migration_user" WITH LOGIN PASSWORD :'migration_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO :"app_user", :"migration_user";

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION :"migration_user";
ALTER SCHEMA app OWNER TO :"migration_user";
GRANT USAGE ON SCHEMA app TO :"app_user";

ALTER ROLE :"app_user" SET search_path = app, pg_catalog;
ALTER ROLE :"migration_user" SET search_path = app, pg_catalog;

ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";

-- Function EXECUTE is public by default; future migrations grant it explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

COMMIT;
