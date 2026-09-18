#!/bin/bash
# Daily local job search: scrape and prefilter deterministically, then use the
# lightweight Codex model through the Mac's saved ChatGPT login for scoring.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$HOME/miniforge3/envs/job_search/bin/python"
CODEX_BIN="/Applications/ChatGPT.app/Contents/Resources/codex"
LOG=""
SCORING_DIR=""
LOCK_DIR="$REPO/results/.daily_codex_lock"
TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"

notify_failure() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"job_search_automation FAILED\" sound name \"Basso\"" >/dev/null 2>&1
}

cleanup() {
    rc=$?
    if [ -n "$SCORING_DIR" ] && [[ "$SCORING_DIR" == "$TEMP_BASE/jobsearch-codex."* ]]; then
        /bin/rm -rf -- "$SCORING_DIR"
    fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    if [ $rc -ne 0 ]; then
        notify_failure "exit $rc${LOG:+ - $(basename "$LOG")}"
    fi
}
trap cleanup EXIT

cd "$REPO" || exit 1
mkdir -p "$REPO/results" "$REPO/logs"

if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    old_pid="$(/bin/cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && /bin/kill -0 "$old_pid" 2>/dev/null; then
        echo "Another daily Codex run is active (PID $old_pid)."
        exit 75
    fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    /bin/mkdir "$LOCK_DIR" || exit 75
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"

LOG="$REPO/logs/codex-run-$(date +%Y%m%d-%H%M%S).log"
exec > >(/usr/bin/tee -a "$LOG") 2>&1

echo "=== job_search_automation Codex run $(date) ==="

if [ ! -x "$PYTHON" ]; then
    echo "FATAL: Python environment not found at $PYTHON"
    exit 1
fi
if [ ! -x "$CODEX_BIN" ]; then
    echo "FATAL: Codex CLI not found at $CODEX_BIN"
    exit 1
fi

set -a
source .env
set +a

run_with_retry() {
    label="$1"
    shift
    attempt=1
    while ! "$@"; do
        if [ $attempt -ge 5 ]; then
            echo "FATAL: $label failed after $attempt attempts"
            return 1
        fi
        delay=$((attempt * 5))
        echo "$label failed (attempt $attempt/5); retrying in ${delay}s..."
        /bin/sleep "$delay"
        attempt=$((attempt + 1))
    done
}

# Finish any small upload left by an earlier interrupted run before refreshing
# the local score store from Supabase.
if [ -s results/.pending_upload.csv ]; then
    run_with_retry "pending Supabase upload" "$PYTHON" src/supabase_uploader.py results/.pending_upload.csv || exit 1
    /bin/rm -f -- results/.pending_upload.csv
fi

run_with_retry "Supabase download" "$PYTHON" src/supabase_downloader.py results/.score_store.csv || exit 1

# Refresh a bounded batch of the highest-value stale listings on every run.
# The checker recognizes LinkedIn's HTTP-200 expired redirects and uploads
# only the three status fields, keeping this much lighter than a full-store
# upsert.
ACTIVE_STATUS_UPLOAD="results/.pending_active_status_upload.csv"
/bin/rm -f -- "$ACTIVE_STATUS_UPLOAD"
"$PYTHON" src/active_checker.py results/.score_store.csv \
    --output "$ACTIVE_STATUS_UPLOAD" \
    --max-jobs "${JOB_SEARCH_ACTIVE_CHECK_LIMIT:-250}" \
    --min-score "${JOB_SEARCH_ACTIVE_MIN_SCORE:-60}" \
    --low-score-cutoff "${JOB_SEARCH_LOW_SCORE_CUTOFF:-60}" \
    --low-score-expiry-days "${JOB_SEARCH_LOW_SCORE_EXPIRY_DAYS:-14}" \
    --workers "${JOB_SEARCH_ACTIVE_CHECK_WORKERS:-2}" || echo "WARNING: active-status refresh failed; continuing with scoring"
if [ -s "$ACTIVE_STATUS_UPLOAD" ]; then
    run_with_retry "active-status upload" "$PYTHON" src/supabase_uploader.py "$ACTIVE_STATUS_UPLOAD" || exit 1
    /bin/rm -f -- "$ACTIVE_STATUS_UPLOAD"
fi

search_args=(--dry-run)
if [ "${JOB_SEARCH_RESUME:-0}" = "1" ] && [ -s results/.scrape_cache.csv ]; then
    search_args+=(--resume)
fi
"$PYTHON" run_search.py "${search_args[@]}" || exit 1
"$PYTHON" scripts/prepare_codex_queue.py || exit 1

selected="$("$PYTHON" -c 'import json; print(json.load(open("results/codex_queue_summary.json"))["selected"])')"
if [ "$selected" -gt 0 ]; then
    SCORING_DIR="$(/usr/bin/mktemp -d "$TEMP_BASE/jobsearch-codex.XXXXXX")" || exit 1
    /bin/cp cv/cv_compressed.yaml "$SCORING_DIR/cv.yaml"
    /bin/cp results/codex_queue.jsonl "$SCORING_DIR/jobs.jsonl"
    /bin/cp scripts/codex_job_scores.schema.json "$SCORING_DIR/schema.json"
    /usr/bin/git -C "$SCORING_DIR" init -q || exit 1

    echo "Scoring $selected prefiltered jobs with GPT-5.6 Luna..."
    /usr/bin/env -u ANTHROPIC_API_KEY -u SUPABASE_URL -u SUPABASE_SERVICE_ROLE_KEY \
        "$CODEX_BIN" exec --ephemeral --color never \
        --model gpt-5.6-luna -c 'model_reasoning_effort="low"' \
        --sandbox read-only --cd "$SCORING_DIR" \
        --output-schema "$SCORING_DIR/schema.json" \
        --output-last-message "$SCORING_DIR/scores.json" \
        - < scripts/codex_job_scoring_prompt.md || exit 1

    /bin/cp "$SCORING_DIR/scores.json" results/codex_scores.json
    "$PYTHON" scripts/import_codex_scores.py || exit 1

    if [ -s results/.pending_upload.csv ]; then
        run_with_retry "Supabase upload" "$PYTHON" src/supabase_uploader.py results/.pending_upload.csv || exit 1
        /bin/rm -f -- results/.pending_upload.csv
    fi
else
    echo "No new jobs passed the deterministic relevance gate; skipping model scoring."
fi

/bin/rm -f -- results/.scrape_cache.csv
echo "=== complete $(date) ==="
