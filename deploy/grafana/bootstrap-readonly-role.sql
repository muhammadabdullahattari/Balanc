-- Provision strictly read-only PostgreSQL role for Grafana reporting.
-- Credentials come exclusively from environment variables (never hardcoded).
\getenv grafana_user GRAFANA_DB_USER
\getenv grafana_password GRAFANA_DB_PASSWORD
\getenv database_name POSTGRES_DB

BEGIN;

SELECT (
    :'grafana_user' ~ '^[a-z][a-z0-9_]{0,62}$'
    AND :'grafana_user' NOT LIKE 'pg_%'
    AND length(:'grafana_password') >= 16
    AND :'grafana_password' NOT LIKE 'replace-%'
) AS grafana_role_valid
\gset

\if :grafana_role_valid
\else
    \echo 'GRAFANA_DB_USER / GRAFANA_DB_PASSWORD missing or < 16 characters; aborting.'
    DO $$ BEGIN RAISE EXCEPTION 'Invalid Grafana read-only DB credentials'; END $$;
\endif

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS',
    :'grafana_user',
    :'grafana_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'grafana_user')
\gexec

ALTER ROLE :"grafana_user" WITH LOGIN PASSWORD :'grafana_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE :"grafana_user" SET default_transaction_read_only = on;
ALTER ROLE :"grafana_user" SET search_path = app, pg_catalog;
ALTER ROLE :"grafana_user" SET statement_timeout = '15s';

GRANT CONNECT ON DATABASE :"database_name" TO :"grafana_user";
GRANT USAGE ON SCHEMA app TO :"grafana_user";

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM :"grafana_user";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM :"grafana_user";
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM :"grafana_user";

GRANT SELECT ON
  app.businesses,
  app.accounts,
  app.journals,
  app.journal_lines,
  app.trade_documents,
  app.payments,
  app.financial_audit_runs
TO :"grafana_user";

GRANT EXECUTE ON FUNCTION
  app.grafana_businesses(),
  app.grafana_kpi_summary(uuid, date, date),
  app.grafana_income_expense_series(uuid, date, date, text),
  app.grafana_category_breakdown(uuid, date, date, text, integer),
  app.grafana_balance_sheet_summary(uuid, date),
  app.grafana_top_items(uuid, date, date, integer),
  app.grafana_audit_series(uuid, date, date, text)
TO :"grafana_user";

COMMIT;
