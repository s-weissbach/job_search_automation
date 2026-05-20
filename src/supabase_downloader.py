"""Download job_results from Supabase and write to score store CSV."""
import os
import sys
from pathlib import Path


def download_score_store(score_store_path: str) -> int:
    try:
           from supabase import create_client
    except ImportError:
        print("Error: supabase package not installed.")
        sys.exit(1)

    import pandas as pd

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)

    client = create_client(url, key)

    # Fetch all records in pages (Supabase default limit is 1000)
    all_records = []
    page_size = 1000
    offset = 0
    while True:
        resp = client.table("job_results").select("*").range(offset, offset + page_size - 1).execute()
        batch = resp.data or []
        all_records.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not all_records:
        print("No existing records in Supabase — starting fresh.")
        return 0

    p = Path(score_store_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(all_records)
    # Drop Supabase-only columns not used by the scorer
    df = df.drop(columns=["created_at"], errors="ignore")
    df.to_csv(p, index=False)

    print(f"Downloaded {len(df)} records from Supabase to {score_store_path}")
    return len(df)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download score store from Supabase")
    parser.add_argument("score_store", help="Path to write .score_store.csv")
    args = parser.parse_args()
    download_score_store(args.score_store)
