#!/bin/bash
# Daily local job search owned by Perry: scrape, prefilter, score locally, publish.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LMS="$HOME/.lmstudio/bin/lms"
LMS_MODEL="qwen3.8-27b-mlx"
LMS_IDENTIFIER="qwen3.8-27b-local"
LMS_STARTED_BY_JOB=0
LMS_SERVICE_PID=""
PROFILE="${JOB_SEARCH_PROFILE:-owner}"
SENIORITY_POLICY=""
REPLACE_EXISTING=0
if [ "$PROFILE" = "owner" ]; then
    CONFIG="$REPO/config.yaml"
    CV="$REPO/cv/cv_compressed.yaml"
    SCORING_PROMPT="$REPO/scripts/codex_job_scoring_prompt.md"
    SCORING_SCHEMA="$REPO/scripts/codex_job_scores.schema.json"
    RESULTS="$REPO/results"
elif [ "$PROFILE" = "julia" ]; then
    CONFIG="$REPO/profiles/julia/config.yaml"
    CV="$REPO/profiles/julia/cv_compressed.yaml"
    SCORING_PROMPT="$REPO/profiles/julia/scoring_prompt.md"
    SCORING_SCHEMA="$REPO/profiles/julia/scoring_schema.json"
    SENIORITY_POLICY="julia-phd"
    REPLACE_EXISTING=1
    NEW_ACTIVE_MIN_SCORE=0
    RESULTS="$REPO/results/julia"
else
    echo "FATAL: unsupported JOB_SEARCH_PROFILE=$PROFILE"
    exit 2
fi
if [ "$PROFILE" = "owner" ]; then
    NEW_ACTIVE_MIN_SCORE="${JOB_SEARCH_ACTIVE_MIN_SCORE:-60}"
fi
LOG=""
LOCK_DIR="$RESULTS/.daily_perry_lock"

notify_failure() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Perry job search failed\" sound name \"Basso\"" >/dev/null 2>&1
}

model_is_loaded() {
    /usr/bin/curl -fsS --max-time 10 http://127.0.0.1:1234/v1/models | "$PYTHON" -c '
import json
import sys

payload = json.load(sys.stdin)
sys.exit(0 if any(model.get("id") == "qwen3.8-27b-local" for model in payload.get("data", [])) else 1)
' >/dev/null 2>&1
}

ensure_model_loaded() {
    if model_is_loaded; then return 0; fi
    if [ ! -x "$LMS" ]; then echo "FATAL: LM Studio headless CLI not found at $LMS"; return 1; fi
    echo "Loading Perry's local model..."
    "$LMS" load "$LMS_MODEL" --identifier "$LMS_IDENTIFIER" --context-length 32768 --yes
}

cleanup() {
    rc=$?
    if [ "$LMS_STARTED_BY_JOB" = "1" ]; then
        "$LMS" unload "$LMS_IDENTIFIER" >/dev/null 2>&1 || true
        "$LMS" server stop >/dev/null 2>&1 || true
        if [ -n "$LMS_SERVICE_PID" ] && /bin/kill -0 "$LMS_SERVICE_PID" 2>/dev/null; then
            /bin/kill -TERM "$LMS_SERVICE_PID" 2>/dev/null || true
        fi
    fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    if [ $rc -ne 0 ]; then notify_failure "exit $rc${LOG:+ - $(basename "$LOG")}"; fi
}
trap cleanup EXIT

cd "$REPO" || exit 1
mkdir -p "$RESULTS" "$REPO/logs"
if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    old_pid="$(/bin/cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && /bin/kill -0 "$old_pid" 2>/dev/null; then exit 75; fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    /bin/mkdir "$LOCK_DIR" || exit 75
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"

LOG="$REPO/logs/perry-${PROFILE}-run-$(date +%Y%m%d-%H%M%S).log"
exec > >(/usr/bin/tee -a "$LOG") 2>&1
echo "=== Perry job search: $PROFILE ($(date)) ==="

if [ ! -x "$PYTHON" ]; then echo "FATAL: Python environment not found at $PYTHON"; exit 1; fi
if ! /usr/bin/curl -fsS --max-time 5 http://127.0.0.1:1234/v1/models >/dev/null; then
    if [ ! -x "$LMS" ]; then echo "FATAL: LM Studio headless CLI not found at $LMS"; exit 1; fi
    echo "Starting headless local model service (no LM Studio window)..."
    lms_start_output="$("$LMS" daemon up --json)" || exit 1
    printf '%s\n' "$lms_start_output"
    LMS_SERVICE_PID="$(printf '%s\n' "$lms_start_output" | /usr/bin/sed -n 's/.*"pid":\([0-9][0-9]*\).*/\1/p' | /usr/bin/tail -1)"
    if [ -z "$LMS_SERVICE_PID" ]; then echo "FATAL: could not determine headless model service PID"; exit 1; fi
    LMS_STARTED_BY_JOB=1
    "$LMS" server start --port 1234 --bind 127.0.0.1 || exit 1
fi
if ! /usr/bin/curl -fsS --max-time 10 http://127.0.0.1:1234/v1/models >/dev/null; then
    echo "FATAL: Perry's headless local model endpoint is unavailable"; exit 1
fi
ensure_model_loaded || exit 1

retry() {
    label="$1"; shift; attempt=1
    while ! "$@"; do
        if [ $attempt -ge 5 ]; then echo "FATAL: $label failed after $attempt attempts"; return 1; fi
        /bin/sleep $((attempt * 5)); attempt=$((attempt + 1))
    done
}

if [ ! -s "$CONFIG" ]; then echo "FATAL: profile config missing at $CONFIG"; exit 1; fi
if [ ! -s "$CV" ]; then echo "FATAL: profile CV missing at $CV"; exit 1; fi

# Job titles, locations, sources, posting age and min score are editable on the
# website's Job Search page. Overlay them onto the local config for this run;
# an unreachable website falls back to the local config unchanged.
EFFECTIVE_CONFIG="$RESULTS/.effective_config.yaml"
"$PYTHON" scripts/website_search_settings.py "$CONFIG" "$EFFECTIVE_CONFIG" --profile "$PROFILE" || exit 1
CONFIG="$EFFECTIVE_CONFIG"

if [ -s "$RESULTS/.pending_upload.csv" ]; then
    retry "pending website upload" "$PYTHON" scripts/website_job_sync.py upload "$RESULTS/.pending_upload.csv" --profile "$PROFILE" || exit 1
    /bin/rm -f -- "$RESULTS/.pending_upload.csv"
fi
retry "website score-store download" "$PYTHON" scripts/website_job_sync.py download "$RESULTS/.score_store.csv" --profile "$PROFILE" || exit 1

ACTIVE_UPLOAD="$RESULTS/.pending_active_status_upload.csv"
/bin/rm -f -- "$ACTIVE_UPLOAD"
"$PYTHON" src/active_checker.py "$RESULTS/.score_store.csv" --output "$ACTIVE_UPLOAD" \
    --max-jobs "${JOB_SEARCH_ACTIVE_CHECK_LIMIT:-250}" --min-score "${JOB_SEARCH_ACTIVE_MIN_SCORE:-60}" \
    --stale-days "${JOB_SEARCH_ACTIVE_STALE_DAYS:-0}" \
    --low-score-cutoff "${JOB_SEARCH_LOW_SCORE_CUTOFF:-60}" \
    --low-score-expiry-days "${JOB_SEARCH_LOW_SCORE_EXPIRY_DAYS:-14}" \
    --workers "${JOB_SEARCH_ACTIVE_CHECK_WORKERS:-2}" || echo "WARNING: active-status refresh failed"
if [ -s "$ACTIVE_UPLOAD" ]; then
    retry "active-status website upload" "$PYTHON" scripts/website_job_sync.py upload "$ACTIVE_UPLOAD" --profile "$PROFILE" || exit 1
    /bin/rm -f -- "$ACTIVE_UPLOAD"
fi

search_args=(--dry-run)
if [ "${JOB_SEARCH_RESUME:-0}" = "1" ] && [ -s "$RESULTS/.scrape_cache.csv" ]; then search_args+=(--resume); fi
"$PYTHON" run_search.py --config "$CONFIG" --cv "$CV" "${search_args[@]}" || exit 1
"$PYTHON" scripts/prepare_codex_queue.py \
    --config "$CONFIG" --scrape "$RESULTS/.scrape_cache.csv" --store "$RESULTS/.score_store.csv" \
    --queue "$RESULTS/codex_queue.jsonl" --audit "$RESULTS/prefilter_audit_latest.csv" \
    --summary "$RESULTS/codex_queue_summary.json" || exit 1
selected="$("$PYTHON" -c "import json; print(json.load(open('$RESULTS/codex_queue_summary.json'))['selected'])")"
if [ "$selected" -gt 0 ]; then
    # Scraping can take longer than a model's prior idle timeout. Re-check at
    # the point of use so a profile never starts scoring against an empty server.
    ensure_model_loaded || exit 1
    /bin/rm -f -- "$RESULTS/codex_scores.json"
    score_args=(scripts/perry_score.py --queue "$RESULTS/codex_queue.jsonl" --cv "$CV" \
        --prompt "$SCORING_PROMPT" --schema "$SCORING_SCHEMA" --output "$RESULTS/codex_scores.json")
    import_args=(scripts/import_codex_scores.py --config "$CONFIG" \
        --queue "$RESULTS/codex_queue.jsonl" --scores "$RESULTS/codex_scores.json" \
        --scrape "$RESULTS/.scrape_cache.csv" --store "$RESULTS/.score_store.csv" \
        --pending "$RESULTS/.pending_upload.csv")
    if [ -n "$SENIORITY_POLICY" ]; then
        score_args+=(--seniority-policy "$SENIORITY_POLICY")
        import_args+=(--seniority-policy "$SENIORITY_POLICY")
    fi
    if [ "$REPLACE_EXISTING" = "1" ]; then import_args+=(--replace-existing); fi
    retry "Perry scoring" "$PYTHON" "${score_args[@]}" || exit 1
    "$PYTHON" "${import_args[@]}" || exit 1
    if [ -s "$RESULTS/.pending_upload.csv" ]; then
        retry "website upload" "$PYTHON" scripts/website_job_sync.py upload "$RESULTS/.pending_upload.csv" --profile "$PROFILE" || exit 1
        /bin/rm -f -- "$RESULTS/.pending_upload.csv"
    fi
    # Newly imported jobs start unverified. Check every recommendation now so
    # the website and digest never present an unchecked URL as open.
    NEW_ACTIVE_UPLOAD="$RESULTS/.pending_new_active_status_upload.csv"
    /bin/rm -f -- "$NEW_ACTIVE_UPLOAD"
    "$PYTHON" src/active_checker.py "$RESULTS/.score_store.csv" --output "$NEW_ACTIVE_UPLOAD" \
        --max-jobs "${JOB_SEARCH_ACTIVE_CHECK_LIMIT:-250}" --min-score "$NEW_ACTIVE_MIN_SCORE" \
        --stale-days 0 --low-score-cutoff "${JOB_SEARCH_LOW_SCORE_CUTOFF:-60}" \
        --low-score-expiry-days "${JOB_SEARCH_LOW_SCORE_EXPIRY_DAYS:-14}" \
        --workers "${JOB_SEARCH_ACTIVE_CHECK_WORKERS:-2}" || echo "WARNING: new-job active-status check failed"
    if [ -s "$NEW_ACTIVE_UPLOAD" ]; then
        retry "new-job active-status website upload" "$PYTHON" scripts/website_job_sync.py upload "$NEW_ACTIVE_UPLOAD" --profile "$PROFILE" || exit 1
        /bin/rm -f -- "$NEW_ACTIVE_UPLOAD"
    fi
else
    echo "No new jobs passed the relevance gate; skipping Perry scoring."
fi

/bin/rm -f -- "$RESULTS/.scrape_cache.csv"
echo "=== $PROFILE complete $(date) ==="

# The existing launch agent keeps calling this script once. After the owner's
# run, start Julia's isolated profile when its two private files are installed.
if [ "$PROFILE" = "owner" ] && [ -s "$REPO/profiles/julia/config.yaml" ] && [ -s "$REPO/profiles/julia/cv_compressed.yaml" ]; then
    JOB_SEARCH_PROFILE=julia "$0"
fi
