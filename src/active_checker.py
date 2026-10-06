"""Check whether past job URLs are still accessible (not 404/expired)."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
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
_LOW_SCORE_EXPIRY_DAYS = 14
_LOW_SCORE_CUTOFF = 60
_LINKEDIN_EXPIRED_MARKERS = (
    "job is no longer available",
    "job posting is no longer available",
    "no longer accepting applications",
    "this job has expired",
)
_GENERAL_EXPIRED_MARKERS = (
    "position has been filled",
    "position is no longer available",
    "vacancy is no longer available",
    "applications are closed",
    "application period has ended",
)
_CLOSING_DATE = re.compile(
    r"(?:online|open|apply|applications?).{0,50}(?:until|through|closes?|deadline)\s*[:\-]?\s*"
    r"([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{4})",
    re.I,
)


def _past_explicit_closing_date(body: str, today: date) -> bool:
    """Recognize an explicit application closing date on a still-live page."""
    for raw in _CLOSING_DATE.findall(body):
        cleaned = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", raw, flags=re.I)
        parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=False)
        if not pd.isna(parsed) and parsed.date() < today:
            return True
    return False


def _classify_response(
    source_url: str,
    status_code: int,
    final_url: str,
    body: str,
    today: date | None = None,
) -> str:
    """Classify one HTTP response without treating access blocks as closures."""
    if status_code in {404, 410}:
        return "expired"
    if status_code in {401, 403, 429} or status_code >= 500:
        return "unknown"
    if status_code >= 400:
        return "unknown"

    normalized_body = body.casefold()
    if any(marker in normalized_body for marker in _GENERAL_EXPIRED_MARKERS):
        return "expired"
    if _past_explicit_closing_date(body, today or datetime.now(_BASEL_TZ).date()):
        return "expired"

    source_host = urlsplit(source_url).netloc.casefold()
    if "linkedin.com" in source_host:
        final = urlsplit(final_url)
        tracking = parse_qs(final.query).get("trk", [])
        if "expired_jd_redirect" in tracking:
            return "expired"
        if any(marker in normalized_body for marker in _LINKEDIN_EXPIRED_MARKERS):
            return "expired"
        # A live public listing stays on /jobs/view/<id>. LinkedIn redirects
        # expired listings to a broad search page while still returning 200.
        return "active" if "/jobs/view/" in final.path.casefold() else "unknown"

    return "active" if status_code < 400 else "unknown"


def _check_url(url: str, timeout: int = 10, known_description: str = "") -> str:
    """Return 'active', 'expired', or 'unknown'."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout,
                            allow_redirects=True)
        result = _classify_response(url, resp.status_code, resp.url, resp.text)
        # Some JavaScript-driven employer pages omit the closing-date text in
        # the later HTTP response. Retain the explicit deadline captured by
        # the scraper as evidence that a still-accessible page is closed.
        if result == "active" and _past_explicit_closing_date(
            known_description, datetime.now(_BASEL_TZ).date()
        ):
            return "expired"
        return result
    except Exception:
        return "unknown"


def _expire_low_score_jobs(
    df: pd.DataFrame,
    today: date,
    score_cutoff: int = _LOW_SCORE_CUTOFF,
    expiry_days: int = _LOW_SCORE_EXPIRY_DAYS,
) -> list[int]:
    """Expire low-score listings at 14 days old without requesting their URLs."""
    status = df["is_active"].fillna("").astype(str).str.casefold()
    eligible = status.isin({"", "active", "true", "unknown", "nan"})
    scores = pd.to_numeric(
        df.get("fit_score", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    )
    posted = pd.to_datetime(
        df.get("date_posted", pd.Series(index=df.index, dtype="object")),
        errors="coerce",
    )
    if "assessed_at" in df.columns:
        posted = posted.fillna(pd.to_datetime(df["assessed_at"], errors="coerce"))
    cutoff = pd.Timestamp(today - timedelta(days=max(0, expiry_days)))
    mask = eligible & scores.notna() & (scores < score_cutoff) & posted.notna() & (posted <= cutoff)
    indices = df.index[mask].tolist()
    if indices:
        df.loc[indices, "is_active"] = "expired"
        df.loc[indices, "last_active_check"] = today.isoformat()
    return indices


def check_active_jobs(
    score_store_path: str | Path,
    max_jobs: int = 200,
    timeout: int = 10,
    stale_days: int = _STALE_DAYS,
    min_score: int = 60,
    workers: int = 2,
    low_score_cutoff: int = _LOW_SCORE_CUTOFF,
    low_score_expiry_days: int = _LOW_SCORE_EXPIRY_DAYS,
    output_path: str | Path | None = None,
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
    else:
        df["last_active_check"] = df["last_active_check"].astype("object")

    today = datetime.now(_BASEL_TZ).date()
    auto_expired_indices = _expire_low_score_jobs(
        df,
        today,
        score_cutoff=low_score_cutoff,
        expiry_days=low_score_expiry_days,
    )
    print(
        f"  Auto-expired {len(auto_expired_indices)} jobs scoring below "
        f"{low_score_cutoff} at {low_score_expiry_days}+ days old."
    )

    cutoff = today - timedelta(days=stale_days)

    def needs_check(row: pd.Series) -> bool:
        last = str(row.get("last_active_check", "")).strip()
        if not last or last == "nan":
            return True
        try:
            return date.fromisoformat(last) < cutoff
        except ValueError:
            return True

    status = df["is_active"].fillna("").astype(str).str.casefold()
    eligible = status.isin({"", "active", "true", "unknown", "nan"})
    scores = pd.to_numeric(df.get("fit_score", pd.Series(index=df.index, dtype=float)), errors="coerce").fillna(-1)
    candidates = df[df.apply(needs_check, axis=1) & eligible & (scores >= min_score)].copy()
    candidates["_last_check_sort"] = pd.to_datetime(candidates["last_active_check"], errors="coerce")
    candidates["_score_sort"] = scores.loc[candidates.index]
    to_check = candidates.sort_values(
        ["_last_check_sort", "_score_sort"],
        ascending=[True, False],
        na_position="first",
        kind="stable",
    ).head(max_jobs)
    checked = 0
    checked_indices: list[int] = []

    print(f"  Checking {len(to_check)} job URLs (of {len(df)} total)…")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _check_url,
                str(df.at[idx, "job_url"]),
                timeout,
                str(df.at[idx, "description"]) if "description" in df.columns else "",
            ): idx
            for idx in to_check.index
            if str(df.at[idx, "job_url"]) not in {"", "nan"}
        }
        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            # "Open" means positively verified on this check. A block or
            # ambiguous redirect becomes unknown rather than preserving a
            # stale active badge that may now be wrong.
            df.at[idx, "is_active"] = result
            df.at[idx, "last_active_check"] = today.isoformat()
            checked += 1
            checked_indices.append(idx)

            title = str(df.at[idx, "title"] if "title" in df.columns else df.at[idx, "job_url"])[:50]
            symbol = {"active": "✓", "expired": "✕", "unknown": "?"}.get(result, "?")
            print(f"    {symbol} [{result:7}] {title}")

    df.to_csv(p, index=False)
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        upload_indices = [*auto_expired_indices, *checked_indices]
        df.loc[upload_indices, ["job_url", "is_active", "last_active_check"]].to_csv(output, index=False)
    active   = (df["is_active"] == "active").sum()
    expired  = (df["is_active"] == "expired").sum()
    unknown  = len(df) - active - expired
    print(f"  Active: {active}  |  Expired: {expired}  |  Unknown: {unknown}")
    return checked


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Refresh stored job-listing activity status")
    parser.add_argument("score_store")
    parser.add_argument("--output")
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--stale-days", type=int, default=_STALE_DAYS)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--low-score-cutoff", type=int, default=_LOW_SCORE_CUTOFF)
    parser.add_argument("--low-score-expiry-days", type=int, default=_LOW_SCORE_EXPIRY_DAYS)
    args = parser.parse_args()
    check_active_jobs(
        args.score_store,
        max_jobs=args.max_jobs,
        timeout=args.timeout,
        stale_days=args.stale_days,
        min_score=args.min_score,
        workers=args.workers,
        low_score_cutoff=args.low_score_cutoff,
        low_score_expiry_days=args.low_score_expiry_days,
        output_path=args.output,
    )
