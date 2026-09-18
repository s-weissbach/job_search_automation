#!/usr/bin/env python3
"""Validate Codex assessments, append them to the score store, and report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.html_reporter import generate_html_report
from src.job_dates import posting_date_or_scrape_date


BASEL_TZ = ZoneInfo("Europe/Zurich")
VALID_SECTORS = {"industry", "academia", "government", "nonprofit", "other"}
VALID_SENIORITY = {"too_junior", "match", "too_senior", "unclear"}


def _read_queue(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _validate(queue: list[dict], payload: dict) -> dict[str, dict]:
    assessments = payload.get("assessments")
    if not isinstance(assessments, list):
        raise ValueError("Codex output must contain an assessments array")

    expected = {str(job["job_id"]) for job in queue}
    by_id: dict[str, dict] = {}
    for result in assessments:
        raw_job_id = str(result.get("job_id") or "")
        job_id = raw_job_id
        if job_id not in expected and len(job_id) >= 12:
            prefix_matches = [candidate for candidate in expected if candidate.startswith(job_id) or job_id.startswith(candidate)]
            if len(prefix_matches) == 1:
                job_id = prefix_matches[0]
                print(f"  Corrected truncated model job_id {raw_job_id!r} -> {job_id!r}")
        if not job_id or job_id in by_id:
            raise ValueError(f"Missing or duplicate job_id: {job_id!r}")
        score = result.get("score")
        if not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError(f"Invalid score for {job_id}: {score!r}")
        if result.get("job_sector") not in VALID_SECTORS:
            raise ValueError(f"Invalid job_sector for {job_id}")
        if result.get("seniority_match") not in VALID_SENIORITY:
            raise ValueError(f"Invalid seniority_match for {job_id}")
        if not isinstance(result.get("matching_skills"), list) or not isinstance(result.get("concerns"), list):
            raise ValueError(f"Skills and concerns must be arrays for {job_id}")
        by_id[job_id] = {**result, "job_id": job_id}

    actual = set(by_id)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"Assessment IDs do not match queue; missing={missing}, extra={extra}")
    return by_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--queue", default="results/codex_queue.jsonl")
    parser.add_argument("--scores", default="results/codex_scores.json")
    parser.add_argument("--scrape", default="results/.scrape_cache.csv")
    parser.add_argument("--store", default="results/.score_store.csv")
    parser.add_argument("--pending", default="results/.pending_upload.csv")
    args = parser.parse_args()

    queue = _read_queue(Path(args.queue))
    payload = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    by_id = _validate(queue, payload)
    config = yaml.safe_load(Path(args.config).read_text()) or {}
    industry_malus = int((config.get("assessment") or {}).get("industry_malus", 15))

    scrape = pd.read_csv(args.scrape).fillna("")
    scrape_by_url = {str(row.get("job_url")): row for _, row in scrape.iterrows()}
    store_path = Path(args.store)
    if store_path.exists():
        existing = pd.read_csv(store_path)
        columns = existing.columns.tolist()
        existing_urls = set(existing.get("job_url", pd.Series(dtype=str)).dropna().astype(str))
    else:
        columns = [
            "job_url", "title", "company", "location", "site", "date_posted",
            "fit_score", "job_sector", "seniority_match", "fit_reasoning",
            "matching_skills", "concerns", "assessed_at", "is_active",
            "last_active_check", "description",
        ]
        existing_urls = set()

    assessed_at = datetime.now(BASEL_TZ).date().isoformat()
    rows: list[dict] = []
    for queued in queue:
        source = scrape_by_url.get(str(queued.get("job_url")), queued)
        result = by_id[str(queued["job_id"])]
        url = str(source.get("job_url") or queued.get("job_url") or "")
        if url in existing_urls:
            continue
        raw_score = int(result["score"])
        sector = str(result["job_sector"])
        fit_score = raw_score if sector == "industry" else max(0, raw_score - industry_malus)
        row = {
            "job_url": url,
            "title": source.get("title", ""),
            "company": source.get("company", ""),
            "location": source.get("location", ""),
            "site": source.get("site", ""),
            "date_posted": posting_date_or_scrape_date(source.get("date_posted"), assessed_at),
            "fit_score": fit_score,
            "job_sector": sector,
            "seniority_match": result["seniority_match"],
            "fit_reasoning": result["reasoning"],
            "matching_skills": "; ".join(str(value) for value in result["matching_skills"]),
            "concerns": "; ".join(str(value) for value in result["concerns"]),
            "assessed_at": assessed_at,
            "is_active": "active",
            "last_active_check": "",
            "description": source.get("description", ""),
        }
        rows.append(row)
        existing_urls.add(url)

    new_df = pd.DataFrame(rows).reindex(columns=columns)
    if not new_df.empty:
        new_df.to_csv(store_path, mode="a", header=not store_path.exists(), index=False)
    new_df.to_csv(args.pending, index=False)

    report_path = Path((config.get("output") or {}).get("results_dir", "results")) / "report.html"
    new_urls = set(new_df.get("job_url", pd.Series(dtype=str)).dropna().astype(str))
    generate_html_report(
        store_path,
        report_path,
        new_urls=new_urls,
        min_score=int((config.get("assessment") or {}).get("min_score", 60)),
    )

    high_fit = int((pd.to_numeric(new_df.get("fit_score"), errors="coerce") >= 60).sum()) if not new_df.empty else 0
    print(f"Imported {len(new_df)} Codex assessments; {high_fit} scored 60+")


if __name__ == "__main__":
    main()
