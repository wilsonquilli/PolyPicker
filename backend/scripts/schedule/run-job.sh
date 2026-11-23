#!/bin/zsh
# Runs one scheduled PolyPicker job: run-all | refresh | scores.
# Used by launchd on this Mac today; on a server the same `polypicker <job>` commands become cron jobs.
set -u
JOB="${1:?usage: run-job.sh run-all|refresh|scores}"
BACKEND="${0:A:h:h:h}"
LOG_DIR="$HOME/Library/Logs/PolyPicker"
LOG="$LOG_DIR/$JOB.log"
mkdir -p "$LOG_DIR"

# Keep each log under ~5 MB (one old copy).
if [[ -f "$LOG" && $(stat -f%z "$LOG") -gt 5000000 ]]; then mv -f "$LOG" "$LOG.1"; fi

# run-all and refresh both publish forecasts, so they share a lock; scores has its own.
LOCK_NAME=$([[ "$JOB" == scores ]] && echo scores || echo forecast)
LOCK="${TMPDIR:-/tmp}/polypicker-$LOCK_NAME.pid"
if [[ -f "$LOCK" ]] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  [[ "$JOB" != scores ]] && echo "$(date '+%F %T') $JOB skipped: previous $LOCK_NAME job still running" >> "$LOG"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# The database runs in Docker locally; start the container if Docker is up but the DB isn't.
DOCKER=/usr/local/bin/docker
if [[ -x "$DOCKER" ]] && "$DOCKER" info >/dev/null 2>&1; then
  if [[ "$("$DOCKER" inspect -f '{{.State.Running}}' polypicker-db 2>/dev/null)" != "true" ]]; then
    "$DOCKER" start polypicker-db >/dev/null 2>&1 && sleep 5
  fi
fi

cd "$BACKEND" || exit 1
if [[ "$JOB" == scores ]]; then
  # quiet unless something was synced or failed (it runs every few minutes)
  OUT="$("$BACKEND/.venv/bin/polypicker" scores 2>&1)"; RC=$?
  if [[ $RC -ne 0 || "$OUT" != *'"synced": []'* ]]; then
    { echo "=== $(date '+%F %T') scores (exit $RC)"; echo "$OUT"; } >> "$LOG"
  fi
  exit $RC
fi
echo "=== $(date '+%F %T') $JOB start" >> "$LOG"
"$BACKEND/.venv/bin/polypicker" "$JOB" >> "$LOG" 2>&1; RC=$?
echo "=== $(date '+%F %T') $JOB end (exit $RC)" >> "$LOG"
exit $RC
