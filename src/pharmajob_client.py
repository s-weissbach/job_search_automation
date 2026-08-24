import difflib
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

from src.text_utils import clean_description

_LEGAL_SUFFIXES = (
    # Deliberately excludes bare "co"/"co." — pharma names legitimately use
    # "& Co." as part of their distinguishing short name (e.g. "Merck & Co."
    # vs "Merck KGaA"), stripping it collided two different real companies
    # down to the same normalized string.
    r"\b(ag|se|plc|n\.?v\.?|gmbh|inc\.?|corp\.?|ltd\.?|llc|s\.?a\.?|kgaa)\b"
)

# Bridges spellings normalization alone can't reconcile — abbreviations and
# subsidiary/brand names. Seeded from config.yaml's company_portals entries
# and pharmajob.io's scraper/companies.py registry; expected to be
# incomplete on day one. See results/.pharmajob_near_misses.csv for
# candidates to add here.
_COMPANY_ALIASES: dict[str, str] = {
    "glaxosmithkline": "gsk",
    "msd": "merck co",
    "merck sharp dohme": "merck co",
    "jj": "johnson johnson",
    "j j": "johnson johnson",
    "janssen": "johnson johnson",
    "genentech": "roche",
    "f hoffmann la roche": "roche",
    "bms": "bristol myers squibb",
}

_NEAR_MISS_THRESHOLD = 0.82


def _normalize_company(name: str | None) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(_LEGAL_SUFFIXES, "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _COMPANY_ALIASES.get(text, text)


def _row(title, company, location, url, description, date_posted=None) -> dict:
    return {
        "title": title or "",
        "company": company or "",
        "location": location or "",
        "job_url": url or "",
        "description": clean_description(description),
        "site": "pharmajob.io",
        "date_posted": date_posted,
        "is_remote": None,
        "job_type": None,
        "min_amount": None,
        "max_amount": None,
        "currency": None,
    }


def _query_one(base_url: str, keyword: str, location: str, page_size: int, max_pages: int, timeout: int) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                f"{base_url.rstrip('/')}/jobs",
                params={"q": keyword, "location": location, "status": "active", "page": page, "page_size": page_size},
                timeout=timeout,
            )
            resp.raise_for_status()
            batch = resp.json()
        except Exception as e:
            print(f"failed ({e})")
            break

        for job in batch:
            company = (job.get("company") or {}).get("name")
            rows.append(_row(
                title=job.get("title"),
                company=company,
                location=job.get("location"),
                url=job.get("job_url"),
                description=job.get("description"),
                date_posted=job.get("date_posted"),
            ))

        if len(batch) < page_size:
            break

    return rows


def _is_fresh(marker_path: Path) -> bool:
    try:
        mtime = datetime.fromtimestamp(marker_path.stat().st_mtime)
        return mtime.date() == date.today()
    except FileNotFoundError:
        return False


def _wait_for_freshness(pj_cfg: dict) -> bool:
    """If a freshness_marker is configured, block (with a bounded poll) until
    it's from today — i.e. pharmajob.io's nightly scrape has actually
    finished updating the DB — before we query it. This is what makes the
    two independently-scheduled jobs safe to run close together: rather than
    just picking a clock time and hoping the scrape is done by then, this
    checks the real completion signal and only proceeds once it's true.

    Returns True if it's safe to query pharmajob.io now (marker fresh, or no
    marker configured at all — an opt-in safety net, not a hard requirement).
    Returns False if the wait timed out — caller should skip pharmajob.io for
    this run rather than block indefinitely or read a stale/mid-update DB."""
    marker = pj_cfg.get("freshness_marker")
    if not marker:
        return True

    marker_path = Path(marker)
    if _is_fresh(marker_path):
        return True

    max_wait = pj_cfg.get("freshness_max_wait_minutes", 30) * 60
    poll_every = pj_cfg.get("freshness_poll_seconds", 120)
    print(f"  pharmajob.io freshness marker not from today yet ({marker}) — "
          f"waiting up to {max_wait // 60} min for tonight's scrape to finish...")

    waited = 0
    while waited < max_wait:
        time.sleep(poll_every)
        waited += poll_every
        if _is_fresh(marker_path):
            print(f"  pharmajob.io scrape confirmed fresh after a {waited}s wait.")
            return True
        print(f"    ...{waited}s waited, still not fresh")

    print(f"  Gave up waiting for pharmajob.io freshness after {max_wait // 60} min "
          f"— continuing without it for this run.")
    return False


def fetch_pharmajob_jobs(config: dict) -> pd.DataFrame:
    """Query a local pharmajob.io instance for the configured search keywords
    and locations. Returns an empty DataFrame if pharmajob_io.enabled is
    false, if the freshness wait times out, or if every request fails —
    never raises."""
    pj_cfg = config.get("pharmajob_io") or {}
    if not pj_cfg.get("enabled"):
        return pd.DataFrame()

    if not _wait_for_freshness(pj_cfg):
        return pd.DataFrame()

    base_url = pj_cfg.get("base_url", "http://localhost:8000")
    page_size = pj_cfg.get("page_size", 100)
    max_pages = pj_cfg.get("max_pages", 5)
    timeout = pj_cfg.get("timeout_seconds", 15)

    keywords = config["search"]["keywords"]
    locations = config["search"].get("locations", [])

    rows: list[dict] = []
    for keyword in keywords:
        for location in locations:
            print(f"  '{keyword}' in '{location}'...", end=" ", flush=True)
            found = _query_one(base_url, keyword, location, page_size, max_pages, timeout)
            rows.extend(found)
            print(f"{len(found)} jobs")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["job_url"], keep="first")
    df = df.drop_duplicates(subset=["title", "company"], keep="first")
    return df


def covered_companies(pharmajob_df: pd.DataFrame) -> set[str]:
    """Normalized company names actually returned by pharmajob.io this run —
    dynamic, not a hardcoded list, so it stays in sync as pharmajob.io's own
    registry grows without any manual bookkeeping here."""
    if pharmajob_df.empty or "company" not in pharmajob_df.columns:
        return set()
    return {
        norm for c in pharmajob_df["company"].dropna().unique()
        if (norm := _normalize_company(c))
    }


def exclude_covered_companies(df: pd.DataFrame, covered: set[str], near_miss_log: str | None = None) -> pd.DataFrame:
    """Drop rows whose normalized company exactly matches something in
    `covered`. Deliberately exact-match only, not substring containment —
    see src/pharmajob_client.py module design notes / the implementation
    plan for why. Non-exact rows that come close are appended to
    near_miss_log for manual review, not excluded."""
    if df.empty or not covered:
        return df

    normalized = df["company"].map(_normalize_company)
    keep_mask = ~normalized.isin(covered)

    if near_miss_log:
        near_misses = []
        for orig, norm, is_kept in zip(df["company"], normalized, keep_mask):
            if not norm or not is_kept:
                continue
            match = difflib.get_close_matches(norm, covered, n=1, cutoff=_NEAR_MISS_THRESHOLD)
            if match:
                ratio = difflib.SequenceMatcher(None, norm, match[0]).ratio()
                near_misses.append({"company": orig, "normalized": norm, "closest_covered": match[0], "ratio": round(ratio, 3)})
        if near_misses:
            log_path = Path(near_miss_log)
            pd.DataFrame(near_misses).to_csv(log_path, mode="a", header=not log_path.exists(), index=False)

    return df[keep_mask]
