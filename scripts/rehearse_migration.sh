#!/usr/bin/env bash
#
# Rehearses `alembic upgrade head` against a realistic copy of production
# data, on a real Postgres server, under simulated lock contention.
#
# CI's "build from scratch" gate (.github/workflows/ci.yml) proves the
# migration chain matches the models when run against an EMPTY database.
# It says nothing about restore time, lock duration against populated
# tables, or behavior under concurrent traffic. This script fills that gap
# before a migration touches production for real.
#
# This script only ever READS from PROD_SOURCE_URL (pg_dump). It only ever
# writes to the scratch database named by SCRATCH_DB_NAME on the server
# addressed by SCRATCH_ADMIN_URL, which it drops and recreates on every run.
# Point SCRATCH_ADMIN_URL at a disposable rehearsal server, never at
# production or a shared staging environment other people depend on.
#
# Required env vars:
#   PROD_SOURCE_URL       Read-only source to dump from, e.g.
#                         postgresql://readonly_user@prod-replica:5432/media
#   SCRATCH_ADMIN_URL     Connection URL to the *postgres* maintenance DB on
#                         the scratch server, e.g.
#                         postgresql://user:pass@scratch-host:5432/postgres
#
# Optional env vars:
#   SCRATCH_DB_NAME       Disposable DB name to drop/recreate (default:
#                         media_api_rehearsal)
#   ALEMBIC_OWNER_ROLE    Passed through so the rehearsal matches prod's
#                         object-ownership setup, if it uses one.
#   LOCK_TIMEOUT_MS       lock_timeout applied to the migration connection
#                         (default: 5000). The migration should fail fast
#                         and visibly rather than queue indefinitely behind
#                         a long-running transaction.
#   CONTENTION_TABLE      Table to hold an ACCESS EXCLUSIVE lock on while the
#                         migration runs, to rehearse queueing behavior.
#                         Must be the table's name as it exists BEFORE the
#                         migrations in this run (default: assets — renamed
#                         from videos by an earlier migration, now the
#                         current head; the pending migration here is a
#                         full-table rewrite of media_transform_requests, so
#                         that table is the one that actually wants
#                         rehearsing under contention -- adjust if a future
#                         migration changes what's pending).
#   CONTENTION_HOLD_SECS  How long the background transaction holds the lock
#                         (default: 8 — longer than LOCK_TIMEOUT_MS so you
#                         can observe the migration time out, then rerun
#                         with SKIP_CONTENTION_TEST=1 for the clean-path
#                         timing)
#   SKIP_CONTENTION_TEST  Set to "1" to skip the lock-contention rehearsal
#                         and just measure clean-path migration time.
#   DOWNGRADE_STEPS       How many `alembic downgrade -1` steps to rehearse
#                         after upgrade (default: 1 — the one pending
#                         migration as of this writing, which converts
#                         media_transform_requests.transform_type from an
#                         enum to text and rewrites its values; downgrading
#                         fails BY DESIGN if any row can't be mapped back
#                         onto the old enum, e.g. a provider-qualified value
#                         created after the migration ran — a red step 7 in
#                         that case is expected, not a rehearsal bug). Set
#                         to 0 to skip the downgrade rehearsal.
#   DUMP_FILE             Where to write the pg_dump custom-format archive
#                         (default: a path under the OS temp dir; kept after
#                         the run so you can inspect or reuse it).
#
# Usage:
#   PROD_SOURCE_URL=postgresql://readonly@prod-replica:5432/media \
#   SCRATCH_ADMIN_URL=postgresql://user:pass@scratch-host:5432/postgres \
#   ./scripts/rehearse_migration.sh

set -euo pipefail

: "${PROD_SOURCE_URL:?Set PROD_SOURCE_URL (read-only source to dump from)}"
: "${SCRATCH_ADMIN_URL:?Set SCRATCH_ADMIN_URL (postgres maintenance DB on the scratch server)}"

SCRATCH_DB_NAME="${SCRATCH_DB_NAME:-media_api_rehearsal}"
LOCK_TIMEOUT_MS="${LOCK_TIMEOUT_MS:-5000}"
CONTENTION_TABLE="${CONTENTION_TABLE:-assets}"
CONTENTION_HOLD_SECS="${CONTENTION_HOLD_SECS:-8}"
SKIP_CONTENTION_TEST="${SKIP_CONTENTION_TEST:-0}"
DOWNGRADE_STEPS="${DOWNGRADE_STEPS:-1}"
DUMP_FILE="${DUMP_FILE:-$(mktemp -t media_api_rehearsal_XXXXXX.dump)}"

if [[ "$PROD_SOURCE_URL" == "$SCRATCH_ADMIN_URL" ]]; then
    echo "PROD_SOURCE_URL and SCRATCH_ADMIN_URL must not be the same server." >&2
    exit 1
fi

SCRATCH_DATABASE_URL="${SCRATCH_ADMIN_URL%/postgres}/${SCRATCH_DB_NAME}"
# psql/pg_dump/pg_restore need the plain "postgresql://" scheme above; Alembic's
# env.py hands its URL straight to SQLAlchemy, which defaults to the psycopg2
# dialect on a bare "postgresql://" -- but this project depends on psycopg (v3),
# not psycopg2. Every `alembic` call below needs this "+psycopg" variant instead.
ALEMBIC_SCRATCH_URL="${SCRATCH_DATABASE_URL/postgresql:/postgresql+psycopg:}"
LOCK_MONITOR_LOG="$(mktemp -t media_api_rehearsal_locks_XXXXXX.log)"
CONTENTION_PID=""
MONITOR_PID=""

cleanup() {
    [[ -n "$MONITOR_PID" ]] && kill "$MONITOR_PID" 2>/dev/null || true
    if [[ -n "$CONTENTION_PID" ]] && kill -0 "$CONTENTION_PID" 2>/dev/null; then
        wait "$CONTENTION_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "== Target: $SCRATCH_DATABASE_URL (dropped/recreated below) =="
echo "Sanity window: 5s to Ctrl-C if that's not the scratch DB you meant."
sleep 5

CLIENT_MAJOR="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
SERVER_MAJOR="$(psql "$PROD_SOURCE_URL" -At -c 'SHOW server_version_num;' | cut -c1-2)"
if [[ "$CLIENT_MAJOR" != "$SERVER_MAJOR" ]]; then
    echo "pg_dump is major version ${CLIENT_MAJOR}, but the source server is ${SERVER_MAJOR}.x — pg_dump refuses to talk to a newer server." >&2
    echo "Run this script's pg_dump/pg_restore steps via a matching-version client instead, e.g.:" >&2
    echo "  docker run --rm postgres:${SERVER_MAJOR}-alpine pg_dump \"\$PROD_SOURCE_URL\" ..." >&2
    exit 1
fi

echo "== 1/7: Dumping $PROD_SOURCE_URL -> $DUMP_FILE =="
time pg_dump "$PROD_SOURCE_URL" -Fc --no-owner --no-acl -f "$DUMP_FILE"

echo "== 2/7: Recreating scratch database $SCRATCH_DB_NAME =="
psql "$SCRATCH_ADMIN_URL" -v ON_ERROR_STOP=1 -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '${SCRATCH_DB_NAME}' AND pid <> pg_backend_pid();"
psql "$SCRATCH_ADMIN_URL" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${SCRATCH_DB_NAME};"
psql "$SCRATCH_ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${SCRATCH_DB_NAME};"

echo "== 3/7: Restoring dump into scratch =="
time pg_restore --no-owner --no-acl -j 4 -d "$SCRATCH_DATABASE_URL" "$DUMP_FILE"

echo "== 4/7: Current alembic revision on the restored snapshot =="
ALEMBIC_DATABASE_URL="$ALEMBIC_SCRATCH_URL" ${ALEMBIC_OWNER_ROLE:+ALEMBIC_OWNER_ROLE="$ALEMBIC_OWNER_ROLE"} \
    uv run alembic current
echo "(Confirm this is an ancestor of head with no drift before continuing.)"

MIGRATION_URL="${ALEMBIC_SCRATCH_URL}?options=-c%20lock_timeout%3D${LOCK_TIMEOUT_MS}ms"

if [[ "$SKIP_CONTENTION_TEST" != "1" ]]; then
    echo "== 5/7: Holding an ACCESS EXCLUSIVE lock on '${CONTENTION_TABLE}' for ${CONTENTION_HOLD_SECS}s =="
    echo "(lock_timeout on the migration connection is ${LOCK_TIMEOUT_MS}ms — expect a fast, visible failure below, not a hang.)"
    psql "$SCRATCH_DATABASE_URL" -v ON_ERROR_STOP=1 <<SQL &
BEGIN;
LOCK TABLE ${CONTENTION_TABLE} IN ACCESS EXCLUSIVE MODE;
SELECT pg_sleep(${CONTENTION_HOLD_SECS});
COMMIT;
SQL
    CONTENTION_PID=$!

    ( for _ in $(seq 1 "$CONTENTION_HOLD_SECS"); do
          date --iso-8601=seconds >> "$LOCK_MONITOR_LOG"
          psql "$SCRATCH_DATABASE_URL" -At -c \
              "SELECT pid, wait_event_type, state, query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();" \
              >> "$LOCK_MONITOR_LOG" 2>/dev/null || true
          sleep 1
      done ) &
    MONITOR_PID=$!
else
    echo "== 5/7: Skipped (SKIP_CONTENTION_TEST=1) =="
fi

echo "== 6/7: Running alembic upgrade head (timed) =="
set +e
START=$(date +%s)
ALEMBIC_DATABASE_URL="$MIGRATION_URL" ${ALEMBIC_OWNER_ROLE:+ALEMBIC_OWNER_ROLE="$ALEMBIC_OWNER_ROLE"} \
    uv run alembic upgrade head
UPGRADE_STATUS=$?
END=$(date +%s)
set -e
echo "alembic upgrade head took $((END - START))s (exit code ${UPGRADE_STATUS})"

if [[ -n "$CONTENTION_PID" ]]; then
    wait "$CONTENTION_PID" || true
    CONTENTION_PID=""
fi
if [[ -n "$MONITOR_PID" ]]; then
    wait "$MONITOR_PID" 2>/dev/null || true
    echo "-- pg_stat_activity samples during the contention window --"
    cat "$LOCK_MONITOR_LOG"
    MONITOR_PID=""
fi

if [[ "$UPGRADE_STATUS" -ne 0 ]]; then
    echo "Migration failed or timed out (see above). If this was the contention" >&2
    echo "rehearsal, that's the lock_timeout doing its job — rerun with" >&2
    echo "SKIP_CONTENTION_TEST=1 for a clean-path timing measurement." >&2
    exit "$UPGRADE_STATUS"
fi

echo "== 7/7: alembic check + downgrade/upgrade round-trip =="
ALEMBIC_DATABASE_URL="$ALEMBIC_SCRATCH_URL" ${ALEMBIC_OWNER_ROLE:+ALEMBIC_OWNER_ROLE="$ALEMBIC_OWNER_ROLE"} \
    uv run alembic check

if [[ "$DOWNGRADE_STEPS" -gt 0 ]]; then
    for i in $(seq 1 "$DOWNGRADE_STEPS"); do
        echo "-- downgrade step $i/${DOWNGRADE_STEPS} --"
        ALEMBIC_DATABASE_URL="$ALEMBIC_SCRATCH_URL" ${ALEMBIC_OWNER_ROLE:+ALEMBIC_OWNER_ROLE="$ALEMBIC_OWNER_ROLE"} \
            uv run alembic downgrade -1
    done
    echo "-- re-upgrading back to head --"
    ALEMBIC_DATABASE_URL="$ALEMBIC_SCRATCH_URL" ${ALEMBIC_OWNER_ROLE:+ALEMBIC_OWNER_ROLE="$ALEMBIC_OWNER_ROLE"} \
        uv run alembic upgrade head
fi

echo "== Done =="
echo "Dump kept at: $DUMP_FILE"
echo "Scratch DB '${SCRATCH_DB_NAME}' left in place on ${SCRATCH_ADMIN_URL%/postgres} for manual inspection."
