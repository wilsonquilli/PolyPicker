#!/bin/bash
# Runs once when the Postgres volume is first created.
# Creates a SELECT-only role for the public API so that, even if an API bug
# allowed a write query, the database would refuse it (FR-11.2, FR-11.4).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE ROLE polypicker_api LOGIN PASSWORD '${API_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO polypicker_api;
  GRANT USAGE ON SCHEMA public TO polypicker_api;
  -- Tables created later by the owner role are readable (but never writable) by the API role.
  ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public GRANT SELECT ON TABLES TO polypicker_api;
  CREATE DATABASE polypicker_test OWNER ${POSTGRES_USER};
EOSQL
