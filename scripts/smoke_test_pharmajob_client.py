#!/usr/bin/env python3
"""Offline check for src/pharmajob_client.py's company-matching logic.
No network, no Anthropic tokens — run any time while tuning normalization
or the alias table.

  python scripts/smoke_test_pharmajob_client.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.pharmajob_client import _normalize_company, covered_companies, exclude_covered_companies

covered_df = pd.DataFrame([
    {"company": "Novartis"},
    {"company": "Roche"},
    {"company": "GSK"},
    {"company": "Merck & Co."},
])
covered = covered_companies(covered_df)
print(f"covered set: {covered}\n")

jobs = pd.DataFrame([
    {"title": "Senior Scientist", "company": "Novartis AG", "job_url": "https://x/1"},
    {"title": "Bioinformatician", "company": "F. Hoffmann-La Roche AG", "job_url": "https://x/2"},
    {"title": "Data Scientist", "company": "GlaxoSmithKline", "job_url": "https://x/3"},
    {"title": "Research Associate", "company": "Merck Sharp & Dohme", "job_url": "https://x/4"},
    {"title": "Computational Biologist", "company": "Random Biotech Startup Inc", "job_url": "https://x/5"},
    {"title": "ML Engineer", "company": "Novartis Foundation", "job_url": "https://x/6"},
])

result = exclude_covered_companies(jobs, covered, near_miss_log="/tmp/pharmajob_smoke_near_misses.csv")
kept = set(result["company"])

expected_dropped = {"Novartis AG", "F. Hoffmann-La Roche AG", "GlaxoSmithKline", "Merck Sharp & Dohme"}
expected_kept = {"Random Biotech Startup Inc", "Novartis Foundation"}

print("kept:", kept)
print()

ok = True
for name in expected_dropped:
    if name in kept:
        print(f"FAIL: expected '{name}' to be excluded, but it was kept")
        ok = False
for name in expected_kept:
    if name not in kept:
        print(f"FAIL: expected '{name}' to be kept, but it was excluded")
        ok = False

# "Novartis Foundation" should show up as a near-miss (close but not exact) —
# confirms it wasn't silently dropped, and that the near-miss log actually caught it.
near_miss_path = Path("/tmp/pharmajob_smoke_near_misses.csv")
if near_miss_path.exists():
    near = pd.read_csv(near_miss_path)
    print("\nnear-miss log:")
    print(near.to_string())
    near_miss_path.unlink()

print("\nnormalize() spot checks:")
for name in ["Novartis AG", "F. Hoffmann-La Roche AG", "GlaxoSmithKline", "Merck Sharp & Dohme", "Johnson & Johnson", "J&J"]:
    print(f"  {name!r:35} -> {_normalize_company(name)!r}")

print("\nPASS" if ok else "\nFAIL")
sys.exit(0 if ok else 1)
