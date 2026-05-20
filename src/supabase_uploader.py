"""Push score store CSV to Supabase job_results table."""
import os
import sys
from pathlib import Path

import pandas as pd


def upload_score_store(score_store_path: str) -> int:
    try:
        from supabase import create_client
    except ImportError:
        print("Error: supabase package not installed. Run: pip install supabase")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)

    p = Path(score_store_path)
    if not p.exists():
        print(f"Score store not found: {score_store_path}")
        return 0

    df = pd.read_csv(p)
    if df.empty:
        print("Score store is empty — nothing to upload.")
        return 0

    # Replace NaN with None for JSON serialization
    records = df.where(pd.notna(df), None).to_dict("records")

    client = create_client(url, key)
    client.table("job_results").upsert(records, on_conflict="job_url").execute()

    print(f"Uploaded {len(records)} job records to Supabase.")
    return len(records)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Upload score store to Supabase")
    parser.add_argument("score_store", help="Path to .score_store.csv")
    args = parser.parse_args()
    upload_score_store(args.score_store)
