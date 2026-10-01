#!/bin/bash
# Daily local job search owned by Perry: scrape, prefilter, score locally, publish.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG=""
LOCK_DIR="$REPO/results/.daily_perry_lock"

notify_failure() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Perry job search failed\" sound name \"Basso\"" >/dev/null 2>&1
}

cleanup() {
    rc=$?
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    if [ $rc -ne 0 ]; then notify_failure "exit $rc${LOG:+ - $(basename "$LOG")}"; fi
}
trap cleanup EXIT

cd "$REPO" || exit 1
mkdir -p "$REPO/results" "$REPO/logs"
if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    old_pid="$(/bin/cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && /bin/kill -0 "$old_pid" 2>/dev/null; then exit 75; fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    /bin/mkdir "$LOCK_DIR" || exit 75
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"

LOG="$REPO/logs/perry-run-$(date +%Y%m%d-%H%M%S).log"
exec > >(/usr/bin/tee -a "$LOG") 2>&1
echo "=== Perry job search $(date) ==="

if [ ! -x "$PYTHON" ]; then echo "FATAL: Python environment not found at $PYTHON"; exit 1; fi
if ! /usr/bin/curl -fsS --max-time 10 http://127.0.0.1:1234/v1/models >/dev/null; then
    echo "FATAL: Perry's local LM Studio model endpoint is unavailable"; exit 1
fi

retry() {
    label="$1"; shift; attempt=1
    while ! "$@"; do
        if [ $attempt -ge 5 ]; then echo "FATAL: $label failed after $attempt attempts"; return 1; fi
        /bin/sleep $((attempt * 5)); attempt=$((attempt + 1))
    done
}

if [ -s results/.pending_upload.csv ]; then
    retry "pending website upload" "$PYTHON" scripts/website_job_sync.py upload results/.pending_upload.csv || exit 1
    /bin/rm -f -- results/.pending_upload.csv
fi
retry "website score-store download" "$PYTHON" scripts/website_job_sync.py download results/.score_store.csv || exit 1

ACTIVE_UPLOAD="results/.pending_active_status_upload.csv"
/bin/rm -f -- "$ACTIVE_UPLOAD"
"$PYTHON" src/active_checker.py results/.score_store.csv --output "$ACTIVE_UPLOAD" \
    --max-jobs "${JOB_SEARCH_ACTIVE_CHECK_LIMIT:-250}" --min-score "${JOB_SEARCH_ACTIVE_MIN_SCORE:-60}" \
    --low-score-cutoff "${JOB_SEARCH_LOW_SCORE_CUTOFF:-60}" \
    --low-score-expiry-days "${JOB_SEARCH_LOW_SCORE_EXPIRY_DAYS:-14}" \
    --workers "${JOB_SEARCH_ACTIVE_CHECK_WORKERS:-2}" || echo "WARNING: active-status refresh failed"
if [ -s "$ACTIVE_UPLOAD" ]; then
    retry "active-status website upload" "$PYTHON" scripts/website_job_sync.py upload "$ACTIVE_UPLOAD" || exit 1
    /bin/rm -f -- "$ACTIVE_UPLOAD"
fi

search_args=(--dry-run)
if [ "${JOB_SEARCH_RESUME:-0}" = "1" ] && [ -s results/.scrape_cache.csv ]; then search_args+=(--resume); fi
"$PYTHON" run_search.py "${search_args[@]}" || exit 1
"$PYTHON" scripts/prepare_codex_queue.py || exit 1
selected="$("$PYTHON" -c 'import json; print(json.load(open("results/codex_queue_summary.json"))["selected"])')"
if [ "$selected" -gt 0 ]; then
    "$PYTHON" scripts/perry_score.py || exit 1
    "$PYTHON" scripts/import_codex_scores.py || exit 1
    if [ -s results/.pending_upload.csv ]; then
        retry "website upload" "$PYTHON" scripts/website_job_sync.py upload results/.pending_upload.csv || exit 1
        /bin/rm -f -- results/.pending_upload.csv
    fi
else
    echo "No new jobs passed the relevance gate; skipping Perry scoring."
fi

/bin/rm -f -- results/.scrape_cache.csv
echo "=== complete $(date) ==="
