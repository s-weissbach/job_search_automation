import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DailyProfilesTests(unittest.TestCase):
    def run_batch(self, *, julia_installed=True, julia_status=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy(Path(__file__).resolve().parents[1] / "scripts/run_daily_profiles.sh", scripts)
            (scripts / "run_perry_local.sh").write_text(
                'echo "$JOB_SEARCH_PROFILE" >> "$TEST_PROFILE_ORDER"\n'
                'if [ "$JOB_SEARCH_PROFILE" = "julia" ]; then exit "$TEST_JULIA_STATUS"; fi\n'
            )
            if julia_installed:
                profile = root / "profiles/julia"
                profile.mkdir(parents=True)
                for name in ("config.yaml", "cv_compressed.yaml"):
                    (profile / name).write_text("installed")
            order = root / "order"
            env = {**os.environ, "TEST_PROFILE_ORDER": str(order), "TEST_JULIA_STATUS": str(julia_status)}
            result = subprocess.run(["/bin/bash", str(scripts / "run_daily_profiles.sh")], env=env, capture_output=True)
            self.assertFalse((root / "results/.daily_perry_batch_lock").exists())
            return result.returncode, order.read_text().splitlines()

    def test_julia_runs_before_owner(self):
        self.assertEqual(self.run_batch(), (0, ["julia", "owner"]))

    def test_owner_still_runs_after_julia_fails_and_batch_reports_failure(self):
        self.assertEqual(self.run_batch(julia_status=7), (7, ["julia", "owner"]))

    def test_missing_private_profile_does_not_block_owner(self):
        self.assertEqual(self.run_batch(julia_installed=False), (0, ["owner"]))
