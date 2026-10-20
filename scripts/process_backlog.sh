#!/usr/bin/env bash
# Process every crawl job that has CrawlResults stuck in 'extracted' status.
#
# The backend worker only picks up jobs published via Celery at crawl time.
# Jobs whose Celery publish failed (or whose worker was down) leave their
# extracted results unprocessed forever. This drains that backlog by
# calling the same admin endpoint the pipeline uses as its HTTP handoff.
#
# Usage:
#   bash scripts/process_backlog.sh              # all pending jobs
#   bash scripts/process_backlog.sh 50           # first 50 jobs

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ENV="$REPO_ROOT/backend/.env"
API="${FOMOCCS_API:-http://localhost:8000}"
LIMIT="${1:-0}"

if [[ ! -f "$BACKEND_ENV" ]]; then
    echo "ERROR: $BACKEND_ENV not found" >&2
    exit 1
fi

SYNC_API_KEY="$(grep -E '^SYNC_API_KEY=' "$BACKEND_ENV" | cut -d= -f2-)"
if [[ -z "$SYNC_API_KEY" ]]; then
    echo "ERROR: SYNC_API_KEY not set in $BACKEND_ENV" >&2
    exit 1
fi

if ! curl -sf --max-time 5 "$API/health" >/dev/null; then
    echo "ERROR: $API/health unreachable — is the backend running?" >&2
    exit 1
fi

PY="$REPO_ROOT/backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="$REPO_ROOT/backend/.venv/bin/python"

DB_HOST="$(grep -E '^DB_HOST=' "$BACKEND_ENV" | cut -d= -f2-)"
DB_PORT="$(grep -E '^DB_PORT=' "$BACKEND_ENV" | cut -d= -f2- || echo 5432)"
DB_NAME="$(grep -E '^DB_NAME=' "$BACKEND_ENV" | cut -d= -f2-)"
DB_USER="$(grep -E '^DB_USER=' "$BACKEND_ENV" | cut -d= -f2-)"
DB_PASS="$(grep -E '^DB_PASS=' "$BACKEND_ENV" | cut -d= -f2-)"

JOB_IDS="$("$PY" -X utf8 -c "
import psycopg2, sys
limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else 0
conn = psycopg2.connect(
    host=sys.argv[2], port=int(sys.argv[3]), dbname=sys.argv[4],
    user=sys.argv[5], password=sys.argv[6],
)
cur = conn.cursor()
q = '''
    SELECT cr.crawl_job_id, MIN(cj.started_at) AS oldest
    FROM crawl_results cr
    LEFT JOIN crawl_jobs cj ON cj.id = cr.crawl_job_id
    WHERE cr.status = 'extracted'
    GROUP BY cr.crawl_job_id
    ORDER BY oldest ASC NULLS LAST
'''
if limit > 0:
    q += ' LIMIT %s' % limit
cur.execute(q)
print(' '.join(str(r[0]) for r in cur.fetchall()))
conn.close()
" "$LIMIT" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DB_PASS")"

read -r -a JOBS <<< "$JOB_IDS"
TOTAL="${#JOBS[@]}"

if [[ "$TOTAL" -eq 0 ]]; then
    echo "Nothing to do — no jobs have extracted CrawlResults."
    exit 0
fi

echo "Processing $TOTAL crawl jobs against $API"
OK=0
FAIL=0
CREATED=0
RETRYABLE=0

process_job() {
    local job_id="$1"
    local out

    for attempt in 1 2 3; do
        out="$(curl -s --max-time 600 -X POST \
            "$API/api/v1/admin/process-crawl-job/$job_id" \
            -H "X-API-Key: $SYNC_API_KEY" \
            -H "Content-Type: application/json" 2>&1)"

        # The Supabase pooler rejects bursts with 500/EMAXCONNSESSION.
        # A 409 is an IntegrityError from the global handler — usually the
        # ``event_sources UNIQUE (event_id, extracted_event_id)`` index when
        # two URLs of the same source carry the same event. The work is
        # already committed for that job, so one retry is enough.
        if [[ "$out" == *"EMAXCONNSESSION"* || "$out" == *"Internal Server Error"* || "$out" == *"Conflict"* ]]; then
            sleep $((attempt * 10))
            continue
        fi
        break
    done

    # A 409 that survives retries means the batch landed but a duplicate
    # row raised on the tail. The crawl_results are already 'processed',
    # so treat it as success rather than stalling the drain.
    if [[ "$out" == *'"status":"ok"'* ]]; then
        local n
        n="$(printf '%s' "$out" | grep -o '"extracted_events_created":[0-9]*' | cut -d: -f2)"
        printf '  job=%-5s ok  created=%s\n' "$job_id" "${n:-0}"
        return 0
    fi

    printf '  job=%-5s FAIL %s\n' "$job_id" "${out:0:160}"
    return 1
}

for job_id in "${JOBS[@]}"; do
    if result="$(process_job "$job_id")"; then
        n="$(printf '%s' "$result" | grep -o 'created=[0-9]*' | cut -d= -f2)"
        CREATED=$((CREATED + ${n:-0}))
        OK=$((OK + 1))
        printf '%s\n' "$result"
    else
        printf '%s\n' "$result"
        if [[ "$result" == *"EMAXCONNSESSION"* || "$result" == *"Internal Server Error"* ]]; then
            RETRYABLE=$((RETRYABLE + 1))
        else
            FAIL=$((FAIL + 1))
        fi
    fi
done

echo
echo "Done. ok=$OK fail=$FAIL retryable=$RETRYABLE new_extracted_events=$CREATED"
[[ "$RETRYABLE" -eq 0 ]] || echo "Note: $RETRYABLE job(s) hit the DB pooler limit; re-run to finish them."