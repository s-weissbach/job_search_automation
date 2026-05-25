"""Push score store CSV to Supabase job_results table."""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

_DATE_COLUMNS = {"date_posted", "assessed_at"}


def _to_date_or_none(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == "":
        return None
    try:
        date.fromisoformat(str(val))
        return str(val)
    except ValueError:
        return None


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
        print(f"Score store not found: {score_store_path} — nothing to upload.")
        return 0

    df = pd.read_csv(p)
    if df.empty:
        print("Score store is empty — nothing to upload.")
        return 0

    # to_json serialises NaN → null; json.loads converts null → None, giving
    # JSON-safe dicts without any float('nan') leaking through.
    records = json.loads(df.to_json(orient="records"))
    for rec in records:
        for col in _DATE_COLUMNS:
            if col in rec:
                rec[col] = _to_date_or_none(rec[col])

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
