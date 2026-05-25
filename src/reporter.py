from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_BASEL_TZ = ZoneInfo("Europe/Zurich")

import pandas as pd


def print_summary(df: pd.DataFrame, top_n: int = 15) -> None:
    if df.empty:
        print("No jobs matched the minimum score threshold.")
        return

    top = df.nlargest(top_n, "fit_score").copy()
    top["title"] = top["title"].fillna("").str[:45]
    top["company"] = top["company"].fillna("").str[:28]
    top["location"] = top["location"].fillna("").str[:22]

    rows = top[["fit_score", "title", "company", "location", "job_url"]].values.tolist()

    print(f"\n{'='*72}")
    print(f"  TOP {min(top_n, len(df))} JOBS  (of {len(df)} above minimum score)")
    print(f"{'='*72}")

    try:
        from tabulate import tabulate
        print(tabulate(
            [[r[0], r[1], r[2], r[3]] for r in rows],
            headers=["Score", "Title", "Company", "Location"],
            tablefmt="simple",
        ))
    except ImportError:
        fmt = "{:>5}  {:<45}  {:<28}  {:<22}"
        print(fmt.format("Score", "Title", "Company", "Location"))
        print("-" * 72)
        for r in rows:
            print(fmt.format(r[0], r[1], r[2], r[3]))

    print()
    for r in rows[:5]:
        print(f"  [{r[0]}/10] {r[1]} — {r[4]}")


def save_results(df: pd.DataFrame, config: dict) -> str:
    results_dir = Path(config["output"]["results_dir"])
    results_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(_BASEL_TZ).strftime("%Y%m%d_%H%M%S")
    output_path = results_dir / f"jobs_{timestamp}.csv"

    keep_cols = [
        "fit_score", "fit_reasoning", "matching_skills", "concerns",
        "title", "company", "location", "job_url", "date_posted",
        "job_type", "is_remote", "min_amount", "max_amount", "currency",
        "site", "description",
    ]
    cols = [c for c in keep_cols if c in df.columns]
    df[cols].to_csv(output_path, index=False)
    return str(output_path)
