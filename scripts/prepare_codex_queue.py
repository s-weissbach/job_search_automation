#!/usr/bin/env python3
"""Build a small, ranked, auditable queue for Codex job scoring."""

from __future__ import annotations

import argparse
import hashlib
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

from src.job_prefilter import canonical_job_url, evaluate_job, normalized_identity


BASEL_TZ = ZoneInfo("Europe/Zurich")


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def _job_id(url: str, ordinal: int) -> str:
    seed = url or f"missing-url-{ordinal}"
    return "job-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--scrape", default="results/.scrape_cache.csv")
    parser.add_argument("--store", default="results/.score_store.csv")
    parser.add_argument("--queue", default="results/codex_queue.jsonl")
    parser.add_argument("--audit", default="results/prefilter_audit_latest.csv")
    parser.add_argument("--summary", default="results/codex_queue_summary.json")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text()) or {}
    prefilter_config = config.get("prefilter") or {}
    jobs = pd.read_csv(args.scrape).fillna("")
    store = pd.read_csv(args.store).fillna("") if Path(args.store).exists() else pd.DataFrame()

    cached_urls: set[str] = set()
    cached_identities: set[str] = set()
    company_sectors: dict[str, str] = {}
    if not store.empty:
        for _, row in store.iterrows():
            cached_urls.add(canonical_job_url(row.get("job_url")))
            cached_identities.add(normalized_identity(row.get("title"), row.get("company"), row.get("location")))
            company = str(row.get("company") or "").strip().casefold()
            sector = str(row.get("job_sector") or "").strip().casefold()
            if company and sector:
                company_sectors[company] = sector

    seen_urls: set[str] = set()
    seen_identities: set[str] = set()
    candidates: list[dict] = []
    audit: list[dict] = []
    skipped_cached = 0
    skipped_duplicate = 0

    for ordinal, (_, row) in enumerate(jobs.iterrows(), start=1):
        raw = {key: _clean(value) for key, value in row.to_dict().items()}
        url = canonical_job_url(raw.get("job_url"))
        identity = normalized_identity(raw.get("title"), raw.get("company"), raw.get("location"))

        if (url and url in cached_urls) or (identity and identity in cached_identities):
            skipped_cached += 1
            continue
        if (url and url in seen_urls) or (identity and identity in seen_identities):
            skipped_duplicate += 1
            continue
        if url:
            seen_urls.add(url)
        if identity:
            seen_identities.add(identity)

        company = str(raw.get("company") or "").strip().casefold()
        decision = evaluate_job(raw, company_sectors.get(company), prefilter_config)
        audit_row = {
            "job_url": raw.get("job_url"),
            "title": raw.get("title"),
            "company": raw.get("company"),
            "location": raw.get("location"),
            "date_posted": raw.get("date_posted"),
            "prefilter_score": decision.score,
            "prefilter_decision": "candidate" if decision.accepted else "rejected",
            "prefilter_reason": decision.reason,
            "prefilter_signals": "; ".join(decision.signals),
        }
        audit.append(audit_row)
        if decision.accepted:
            raw["job_id"] = _job_id(url, ordinal)
            raw["prefilter_score"] = decision.score
            raw["prefilter_signals"] = list(decision.signals)
            candidates.append(raw)

    candidates.sort(
        key=lambda job: (int(job.get("prefilter_score") or 0), str(job.get("date_posted") or "")),
        reverse=True,
    )
    max_jobs = int(prefilter_config.get("max_llm_jobs", 80))
    selected = candidates[:max_jobs]
    selected_ids = {job["job_id"] for job in selected}
    candidate_by_url = {job.get("job_url"): job for job in candidates}

    for row in audit:
        matching = candidate_by_url.get(row["job_url"])
        if matching and matching["job_id"] not in selected_ids:
            row["prefilter_decision"] = "deferred"
            row["prefilter_reason"] = "daily_capacity"

    queue_path = Path(args.queue)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    max_description_chars = int(prefilter_config.get("llm_description_chars", 5000))
    with queue_path.open("w", encoding="utf-8") as handle:
        for job in selected:
            payload = {
                "job_id": job["job_id"],
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "date_posted": job.get("date_posted"),
                "job_url": job.get("job_url"),
                "description": str(job.get("description") or "")[:max_description_chars],
                "prefilter_score": job.get("prefilter_score"),
                "prefilter_signals": job.get("prefilter_signals"),
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    audit_path = Path(args.audit)
    pd.DataFrame(audit).to_csv(audit_path, index=False)
    dated_audit = audit_path.with_name(f"prefilter_audit_{datetime.now(BASEL_TZ):%Y%m%d}.csv")
    pd.DataFrame(audit).to_csv(dated_audit, index=False)

    reason_counts = (
        pd.Series([row["prefilter_reason"] for row in audit]).value_counts().to_dict()
        if audit else {}
    )
    summary = {
        "generated_at": datetime.now(BASEL_TZ).isoformat(),
        "scraped": len(jobs),
        "already_scored": skipped_cached,
        "duplicates": skipped_duplicate,
        "unscored_considered": len(audit),
        "selected": len(selected),
        "deferred": max(0, len(candidates) - len(selected)),
        "rejected": sum(row["prefilter_decision"] == "rejected" for row in audit),
        "reason_counts": reason_counts,
    }
    Path(args.summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
