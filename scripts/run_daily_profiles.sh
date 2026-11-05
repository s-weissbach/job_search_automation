#!/bin/bash
# The scheduled batch gives Julia first access to scraping and local scoring.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_DIR="$REPO/results/.daily_perry_batch_lock"
mkdir -p "$REPO/results"
if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    old_pid="$(/bin/cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && /bin/kill -0 "$old_pid" 2>/dev/null; then exit 75; fi
    /bin/rm -f -- "$LOCK_DIR/pid" 2>/dev/null || true
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    /bin/mkdir "$LOCK_DIR" || exit 75
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"
cleanup_batch() {
    /bin/rm -f -- "$LOCK_DIR/pid"
    /bin/rmdir "$LOCK_DIR"
}
trap cleanup_batch EXIT

status=0
for profile in julia owner; do
    if [ "$profile" = "julia" ] && { [ ! -s "$REPO/profiles/julia/config.yaml" ] || [ ! -s "$REPO/profiles/julia/cv_compressed.yaml" ]; }; then
        echo "Skipping Julia: private profile files are not installed."
        continue
    fi
    JOB_SEARCH_PROFILE="$profile" /bin/bash "$REPO/scripts/run_perry_local.sh" || status=$?
done
exit "$status"
