"""Check whether past job URLs are still accessible (not 404/expired)."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_BASEL_TZ = ZoneInfo("Europe/Zurich")

import pandas as pd
import requests


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_STALE_DAYS = 7   # re-check URLs older than this


def _check_url(url: str, timeout: int = 10) -> str:
    """Return 'active', 'expired', or 'unknown'."""
    try:
        resp = requests.head(url, headers=_HEADERS, timeout=timeout,
                             allow_redirects=True)
        if resp.status_code == 404:
            return "expired"
        if resp.status_code < 400:
            return "active"
        # Some sites block HEAD — fall back to GET
        resp = requests.get(url, headers=_HEADERS, timeout=timeout,
                            allow_redirects=True, stream=True)
        resp.close()
        if resp.status_code == 404:
            return "expired"
        if resp.status_code < 400:
            return "active"
        return "expired"
    except Exception:
        return "unknown"


def check_active_jobs(
    score_store_path: str | Path,
    max_jobs: int = 200,
    timeout: int = 10,
    stale_days: int = _STALE_DAYS,
) -> int:
    """Check active status for jobs in the score store that haven't been checked recently.

    Updates is_active and last_active_check columns in place.

    Returns:
        Number of jobs actually checked (not loaded from cache).
    """
    p = Path(score_store_path)
    if not p.exists():
        print("Score store not found — nothing to check.")
        return 0

    df = pd.read_csv(p)
    if df.empty:
        return 0

    # Add columns if missing
    if "is_active" not in df.columns:
        df["is_active"] = ""
    if "last_active_check" not in df.columns:
        df["last_active_check"] = ""

    cutoff = (datetime.now(_BASEL_TZ) - timedelta(days=stale_days)).date()

    def needs_check(row: pd.Series) -> bool:
        last = str(row.get("last_active_check", "")).strip()
        if not last or last == "nan":
            return True
        try:
            return date.fromisoformat(last) < cutoff
        except ValueError:
            return True

    to_check = df[df.apply(needs_check, axis=1)].head(max_jobs)
    checked = 0

    print(f"  Checking {len(to_check)} job URLs (of {len(df)} total)…")

    for idx in to_check.index:
        url = str(df.at[idx, "job_url"])
        if not url or url == "nan":
            continue

        status = _check_url(url, timeout=timeout)
        df.at[idx, "is_active"] = status
        df.at[idx, "last_active_check"] = date.today().isoformat()
        checked += 1

        title = str(df.at[idx, "title"] if "title" in df.columns else url)[:50]
        symbol = {"active": "✓", "expired": "✕", "unknown": "?"}.get(status, "?")
        print(f"    {symbol} [{status:7}] {title}")

    df.to_csv(p, index=False)
    active   = (df["is_active"] == "active").sum()
    expired  = (df["is_active"] == "expired").sum()
    unknown  = len(df) - active - expired
    print(f"  Active: {active}  |  Expired: {expired}  |  Unknown: {unknown}")
    return checked
