"""One-off maintenance: fill in missing `description` values for already-scored
jobs in the score store, without re-scoring them.

Existing score-store rows are never touched by a normal run once their job_url
is cached (assess_all reuses the cached score and skips re-writing the row), so
turning on linkedin_fetch_description / fetch_workday_descriptions later only
helps *new* postings. This script patches the description field in place for
postings that predate that config change.
"""
import argparse
import re
import time
from pathlib import Path

import pandas as pd
import yaml

from src.text_utils import clean_description

LINKEDIN_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")


def _fetch_linkedin_description(job_id: str) -> str:
    from jobspy.linkedin import LinkedIn
    from jobspy.model import DescriptionFormat

    scraper = LinkedIn()
    scraper.scraper_input = type("_Input", (), {"description_format": DescriptionFormat.MARKDOWN})()
    details = scraper._get_job_details(job_id)
    return clean_description(details.get("description") or "")


def _workday_lookup(config: dict) -> dict[str, tuple[str, str]]:
    """Map base_domain -> (board, base_cxs) for each configured Workday company."""
    from src.portal_scraper import _workday_urls

    lookup = {}
    for entry in (config.get("company_portals", {}).get("workday") or []):
        base_domain, board, base_cxs = _workday_urls(entry["api_url"])
        lookup[base_domain] = (board, base_cxs)
    return lookup


def _fetch_workday_description(job_url: str, workday_lookup: dict[str, tuple[str, str]]) -> str:
    from src.portal_scraper import _fetch_workday_detail

    for base_domain, (board, base_cxs) in workday_lookup.items():
        prefix = f"{base_domain}/{board}"
        if job_url.startswith(prefix):
            ext_path = job_url[len(prefix):]
            description, _locs = _fetch_workday_detail(base_cxs, ext_path)
            return clean_description(description)
    return ""


def backfill(score_store_path: str, config_path: str, min_score: int) -> None:
    p = Path(score_store_path)
    df = pd.read_csv(p)

    missing = df["description"].isna() | (df["description"].astype(str).str.strip() == "")
    target = missing & (df["fit_score"] >= min_score)
    todo = df[target]
    print(f"{len(todo)} rows missing description with fit_score >= {min_score}")

    with open(config_path) as f:
        config = yaml.safe_load(f)
    workday_lookup = _workday_lookup(config)

    filled = 0
    failed = 0
    for idx, row in todo.iterrows():
        url = str(row["job_url"])
        desc = ""
        try:
            m = LINKEDIN_JOB_ID_RE.search(url)
            if m:
                desc = _fetch_linkedin_description(m.group(1))
                time.sleep(1.5)
            elif "myworkdayjobs.com" in url:
                desc = _fetch_workday_description(url, workday_lookup)
                time.sleep(0.5)
            else:
                print(f"  skip (unrecognized source): {url}")
                continue
        except Exception as e:
            print(f"  error on {url}: {e}")

        if desc:
            df.at[idx, "description"] = desc
            filled += 1
            print(f"  [{filled + failed}/{len(todo)}] filled — {row.get('title', '')[:50]} @ {row.get('company', '')}")
        else:
            failed += 1
            print(f"  [{filled + failed}/{len(todo)}] no description found (expired/blocked?) — {url}")

    df.to_csv(p, index=False)
    print(f"\nBackfilled {filled}/{len(todo)} descriptions ({failed} not found — likely expired or blocked).")
    print(f"Saved to {score_store_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing descriptions in the score store")
    parser.add_argument("score_store", help="Path to .score_store.csv")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--min-score", type=int, default=60)
    args = parser.parse_args()
    backfill(args.score_store, args.config, args.min_score)
