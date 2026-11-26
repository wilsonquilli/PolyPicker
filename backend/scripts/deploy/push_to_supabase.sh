#!/usr/bin/env bash
# Copy the local PolyPicker database to Supabase (first deploy, or to start over from your Mac's copy).
#
#   backend/scripts/deploy/push_to_supabase.sh
#
# Prompts (hidden) for the Supabase *session pooler* connection string (Connect -> Session pooler, with your
# database password filled in) and for a new password for the read-only API role. Nothing is echoed or saved.
#
# What it does: compacts old forecast revisions, dumps the local database WITHOUT the rows of
# nba_lineup_possessions (338 MB, only used to train player ratings locally; the empty table is kept),
# restores it into Supabase (replacing what's there), creates the read-only polypicker_api role, and
# compares row counts. Uses the Postgres 17 tools inside the local Docker container.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
  read -rsp "Supabase session pooler URL (postgresql://postgres.<ref>:<password>@...pooler.supabase.com:5432/postgres): " SUPABASE_DB_URL; echo
fi
if [[ -z "${API_DB_PASSWORD:-}" ]]; then
  read -rsp "New password for the read-only polypicker_api role: " API_DB_PASSWORD; echo
fi
[[ "$SUPABASE_DB_URL" == postgresql://* || "$SUPABASE_DB_URL" == postgres://* ]] || { echo "That doesn't look like a postgres URL"; exit 1; }

docker compose ps --status running db >/dev/null 2>&1 || { echo "Local Postgres isn't running: open -a Docker && docker compose up -d"; exit 1; }

# After the first deploy Supabase is the source of truth (the jobs write there). Refuse to overwrite forecasts that
# only exist in Supabase unless FORCE=1.
LATEST_SQL="SELECT coalesce(max(published_at), 'epoch') FROM predictions"
REMOTE_LATEST=$(docker compose exec -T -e PGURL="$SUPABASE_DB_URL" db sh -c \
  "psql \"\$PGURL\" -Atc \"SELECT CASE WHEN to_regclass('public.predictions') IS NULL THEN 'epoch' ELSE ($LATEST_SQL)::text END\"")
LOCAL_LATEST=$(docker compose exec -T db psql -U polypicker -d polypicker -Atc "$LATEST_SQL")
if [[ "$REMOTE_LATEST" != "epoch" && "${FORCE:-0}" != "1" ]]; then
  NEWER=$(docker compose exec -T db psql -U polypicker -d polypicker -Atc "SELECT '$REMOTE_LATEST'::timestamptz > '$LOCAL_LATEST'::timestamptz")
  if [[ "$NEWER" == "t" ]]; then
    echo "Supabase has forecasts newer than your local database ($REMOTE_LATEST > $LOCAL_LATEST)."
    echo "Pushing would erase them. Re-run with FORCE=1 only if you really want to replace Supabase with your local copy."
    exit 1
  fi
fi

echo "1/5 compacting superseded forecast revisions..."
.venv/bin/polypicker compact >/dev/null

DUMP="$(mktemp -t polypicker-dump).pgdump"
trap 'rm -f "$DUMP"' EXIT
echo "2/5 dumping local database (without nba_lineup_possessions rows)..."
docker compose exec -T db pg_dump -U polypicker -d polypicker -Fc --no-owner --no-privileges \
  --exclude-table-data=nba_lineup_possessions > "$DUMP"
echo "    dump: $(du -h "$DUMP" | cut -f1)"

echo "3/5 restoring into Supabase (this replaces existing PolyPicker tables there; a few minutes)..."
docker compose exec -T -e PGURL="$SUPABASE_DB_URL" db sh -c \
  'pg_restore --no-owner --no-privileges --clean --if-exists --single-transaction -d "$PGURL"' < "$DUMP"

echo "4/5 creating the read-only API role and locking Supabase's Data API roles out..."
docker compose exec -T -e PGURL="$SUPABASE_DB_URL" -e APIPW="$API_DB_PASSWORD" db sh -c \
  'psql "$PGURL" -q -v ON_ERROR_STOP=1 -v api_password="$APIPW" -f -' < scripts/deploy/supabase_setup.sql

echo "5/5 comparing row counts..."
COUNT_SQL="SELECT string_agg(t || '=' || n, ' ' ORDER BY t) FROM (
  SELECT 'games' t, count(*) n FROM games UNION ALL SELECT 'predictions', count(*) FROM predictions
  UNION ALL SELECT 'prediction_outcomes', count(*) FROM prediction_outcomes UNION ALL SELECT 'nfl_predictions', count(*) FROM nfl_predictions
  UNION ALL SELECT 'soccer_predictions', count(*) FROM soccer_predictions UNION ALL SELECT 'models', count(*) FROM models) x"
LOCAL=$(docker compose exec -T db psql -U polypicker -d polypicker -Atc "$COUNT_SQL")
REMOTE=$(docker compose exec -T -e PGURL="$SUPABASE_DB_URL" db sh -c "psql \"\$PGURL\" -Atc \"$COUNT_SQL\"")
SIZE=$(docker compose exec -T -e PGURL="$SUPABASE_DB_URL" db sh -c \
  "psql \"\$PGURL\" -Atc \"SELECT pg_size_pretty(pg_database_size(current_database()))\"")
echo "    local : $LOCAL"
echo "    remote: $REMOTE"
echo "    Supabase database size: $SIZE (free limit 500 MB)"
if [[ "$LOCAL" == "$REMOTE" ]]; then echo "OK: Supabase matches your local database."; else echo "MISMATCH: check the output above."; exit 1; fi
