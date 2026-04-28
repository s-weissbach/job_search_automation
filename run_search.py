#!/usr/bin/env python3
"""Automated job search with Claude AI fit assessment.

Usage:
  python run_search.py                          # auto-detect CV, run full pipeline
  python run_search.py --compress-cv            # compress CV first, then run
  python run_search.py --cv cv/cv.pdf           # explicit CV path
  python run_search.py --dry-run                # scrape only, skip AI scoring
  python run_search.py --min-score 8            # override minimum score from config
"""

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def resolve_cv_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in ["cv/cv_compressed.yaml", "cv/cv.pdf", "cv/cv.txt"]:
        if Path(candidate).exists():
            if candidate == "cv/cv_compressed.yaml":
                print(f"Using compressed CV: {candidate}")
            return candidate
    print(
        "Error: No CV found.\n"
        "Place your CV at cv/cv.pdf (or cv/cv.txt),\n"
        "or run with --compress-cv to generate cv/cv_compressed.yaml."
    )
    sys.exit(1)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Automated job search with Claude AI fit scoring")
    parser.add_argument("--config", default="config.yaml", help="Config file (default: config.yaml)")
    parser.add_argument("--cv", default=None, help="CV path: PDF, .txt, or .yaml (default: auto-detect)")
    parser.add_argument(
        "--compress-cv", action="store_true",
        help="Compress CV to token-efficient YAML using Claude, save to cv/cv_compressed.yaml"
    )
    parser.add_argument("--min-score", type=int, default=None, help="Minimum fit score 1-10")
    parser.add_argument("--dry-run", action="store_true", help="Scrape only, skip AI assessment")
    parser.add_argument("--resume", action="store_true", help="Resume interrupted run from cache")
    parser.add_argument("--clear-score-cache", action="store_true",
                        help="Delete the persistent score store before running (use after updating your CV)")
    args = parser.parse_args()

    config = load_config(args.config)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("Error: ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    import pandas as pd
    import anthropic
    from src.scraper import scrape_jobs
    from src.portal_scraper import scrape_portals
    from src.cv_processor import load_cv, compress_cv, save_compressed_cv
    from src.assessor import JobAssessor
    from src.reporter import print_summary, save_results

    results_dir = Path(config["output"]["results_dir"])
    results_dir.mkdir(exist_ok=True)
    scrape_cache = results_dir / ".scrape_cache.csv"
    score_store  = results_dir / ".score_store.csv"   # persistent across runs

    if args.clear_score_cache and score_store.exists():
        score_store.unlink()
        print("Score store cleared.")

    cv_path = resolve_cv_path(args.cv)

    cv_text = load_cv(cv_path)
    print(f"CV loaded: {len(cv_text.split())} words from {cv_path}")

    if args.compress_cv:
        client = anthropic.Anthropic(api_key=api_key)
        compression_model = config["assessment"].get("compression_model", "claude-sonnet-4-6")
        print(f"\nCompressing CV with {compression_model}...")
        compressed = compress_cv(cv_text, client, model=compression_model)
        save_compressed_cv(compressed)
        cv_text = compressed
        print(f"Saved cv/cv_compressed.yaml ({len(compressed.split())} words)")

    if args.resume and scrape_cache.exists():
        print(f"\nResuming: loading {scrape_cache} from previous scrape...")
        jobs_df = pd.read_csv(scrape_cache)
        print(f"Loaded {len(jobs_df)} jobs from cache")
    else:
        if args.resume:
            print("No cached scrape found — starting fresh.")
        print(f"\nScraping jobs...")
        jobs_df = scrape_jobs(config)

        if jobs_df.empty:
            print("No jobs found. Try broader keywords or more sites.")
            return

        print(f"Found {len(jobs_df)} unique jobs from job boards")

        if config.get("company_portals"):
            print("\nScraping company portals...")
            portal_df = scrape_portals(config)
            if not portal_df.empty:
                jobs_df = pd.concat([jobs_df, portal_df], ignore_index=True)
                jobs_df = jobs_df.drop_duplicates(subset=["job_url"], keep="first")
                jobs_df = jobs_df.drop_duplicates(subset=["title", "company"], keep="first")
                print(f"Total after portals: {len(jobs_df)} unique jobs")

        jobs_df.to_csv(scrape_cache, index=False)

    if args.dry_run:
        output_path = save_results(jobs_df, config)
        print(f"Saved scraped jobs (unscored) to {output_path}")
        return

    client = anthropic.Anthropic(api_key=api_key)
    assessor = JobAssessor(client, cv_text, config["assessment"])
    model = config["assessment"].get("model", "claude-haiku-4-5")

    print(f"\nAssessing {len(jobs_df)} jobs with {model}...")
    scored_df = assessor.assess_all(jobs_df, cache_path=str(score_store))

    min_score = args.min_score if args.min_score is not None else config["assessment"].get("min_score", 6)
    filtered = scored_df[scored_df["fit_score"] >= min_score]

    print_summary(filtered)

    output_path = save_results(scored_df, config)
    print(f"Full results ({len(scored_df)} jobs) saved to {output_path}")

    scrape_cache.unlink(missing_ok=True)
    # score_store is intentionally kept — reused across future runs


if __name__ == "__main__":
    main()
