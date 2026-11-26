-- One-time setup on Supabase (run in the SQL editor, or: psql "$SUPABASE_URL" -v api_password='...' -f this file).
-- Creates the SELECT-only role the public API connects as (FR-11.2): even an API bug can't write.
-- The jobs (GitHub Actions) connect as Supabase's `postgres` role, which owns the tables.

\if :{?api_password}
\else
  \echo 'pass -v api_password=...'
  \quit
\endif

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'polypicker_api') THEN
    CREATE ROLE polypicker_api LOGIN;
  END IF;
END $$;
ALTER ROLE polypicker_api WITH LOGIN PASSWORD :'api_password';
ALTER ROLE polypicker_api SET statement_timeout = '30s';
GRANT CONNECT ON DATABASE postgres TO polypicker_api;
GRANT USAGE ON SCHEMA public TO polypicker_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO polypicker_api;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO polypicker_api;

-- Supabase's auto-generated Data API (PostgREST) would expose every public table to anyone holding the anon key.
-- We don't use it: turn it off in Project Settings -> Data API. As a second lock, revoke the API roles' access.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
