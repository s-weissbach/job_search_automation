import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PrepareCodexQueueTests(unittest.TestCase):
    def test_reserves_capacity_for_rotating_recall_samples(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config = temp / "config.yaml"
            scrape = temp / "scrape.csv"
            queue = temp / "queue.jsonl"
            audit = temp / "audit.csv"
            summary = temp / "summary.json"

            config.write_text(
                """prefilter:
  enabled: true
  min_relevance_score: 7
  max_llm_jobs: 10
  recall_audit_sample_size: 2
  exclude_known_nonindustry: false
  exclude_obvious_nonindustry: false
  exclude_junior_roles: true
  exclude_management_roles: false
  exclude_explicit_seniority_mismatches: false
""",
                encoding="utf-8",
            )

            rows = []
            for index in range(2):
                rows.append({
                    "job_url": f"https://example.com/strong-{index}",
                    "title": f"Bioinformatics Scientist {index}",
                    "company": "Example Biotech",
                    "location": "Basel",
                    "date_posted": "2026-09-12",
                    "description": "Python analysis of single-cell RNA-seq and spatial transcriptomics.",
                })
            for index in range(12):
                rows.append({
                    "job_url": f"https://example.com/borderline-{index}",
                    "title": f"Research Analyst {index}",
                    "company": "Example Diagnostics",
                    "location": "Basel",
                    "date_posted": "2026-09-12",
                    "description": "Use Python for bioinformatics analysis.",
                })
            for index in range(6):
                rows.append({
                    "job_url": f"https://example.com/reject-{index}",
                    "title": f"Operations Coordinator {index}",
                    "company": "Example Services",
                    "location": "Basel",
                    "date_posted": "2026-09-12",
                    "description": "Maintain cloud platforms with Python.",
                })

            with scrape.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/prepare_codex_queue.py"),
                    "--config", str(config),
                    "--scrape", str(scrape),
                    "--store", str(temp / "missing-store.csv"),
                    "--queue", str(queue),
                    "--audit", str(audit),
                    "--summary", str(summary),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)

            counts = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(counts["selected"], 10)
            self.assertEqual(counts["selected_strong"], 2)
            self.assertEqual(counts["selected_borderline"], 6)
            self.assertEqual(counts["recall_samples"], 2)
            self.assertEqual(counts["deferred"], 6)

            queued = [json.loads(line) for line in queue.read_text().splitlines()]
            tiers = [job["prefilter_tier"] for job in queued]
            self.assertEqual(tiers.count("strong"), 2)
            self.assertEqual(tiers.count("borderline"), 6)
            self.assertEqual(tiers.count("recall_sample"), 2)


if __name__ == "__main__":
    unittest.main()
