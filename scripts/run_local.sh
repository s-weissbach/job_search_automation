#!/bin/bash
# Weekly local job search run: replaces the GitHub Actions schedule.
# Mirrors the old workflow's steps: restore score cache from Supabase, run the
# search, then push results back to Supabase so the website reflects this run.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$HOME/miniforge3/envs/job_search/bin/python"
LOG=""

# Runs on every exit path (including `cd`/`source` failures below). Without
# this, a failed run only shows up in logs/ nobody is watching for it.
notify_failure() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"job_search_automation FAILED\" sound name \"Basso\"" >/dev/null 2>&1
}
trap 'rc=$?; if [ $rc -ne 0 ]; then notify_failure "exit $rc${LOG:+ - $(basename "$LOG")}"; fi' EXIT

cd "$REPO" || exit 1

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"

exec > >(tee -a "$LOG") 2>&1

echo "=== job_search_automation weekly run $(date) ==="

set -a
source .env
set +a

"$PYTHON" src/supabase_downloader.py results/.score_store.csv
rc=$?
if [ $rc -ne 0 ]; then
    echo "FATAL: supabase_downloader.py exited with $rc"
    exit $rc
fi

"$PYTHON" run_search.py --html-report
rc=$?
if [ $rc -ne 0 ]; then
    echo "FATAL: run_search.py exited with $rc"
    exit $rc
fi

if [ -s results/.score_store.csv ]; then
    "$PYTHON" src/supabase_uploader.py results/.score_store.csv
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "FATAL: supabase_uploader.py exited with $rc"
        exit $rc
    fi
fi

echo "=== complete $(date) ==="
