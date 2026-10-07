import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from perry_score import validate  # noqa: E402


class PerryScoreTests(unittest.TestCase):
    def test_repairs_a_unique_model_generated_id_suffix(self):
        expected = "job-3a7192e90d274189"
        result = validate(
            [{"job_id": expected}],
            {"assessments": [{"job_id": "job-3a7192e90550d973", "score": 70}]},
        )

        self.assertEqual(result[0]["job_id"], expected)

    def test_rejects_an_unrelated_id(self):
        with self.assertRaises(ValueError):
            validate(
                [{"job_id": "job-3a7192e90d274189"}],
                {"assessments": [{"job_id": "job-unrelated", "score": 70}]},
            )


if __name__ == "__main__":
    unittest.main()
