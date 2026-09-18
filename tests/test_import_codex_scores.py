import unittest

from scripts.import_codex_scores import _validate


def assessment(job_id):
    return {
        "job_id": job_id,
        "score": 80,
        "job_sector": "industry",
        "seniority_match": "match",
        "matching_skills": [],
        "concerns": [],
    }


class ImportCodexScoresTests(unittest.TestCase):
    def test_unique_truncated_job_id_is_repaired(self):
        expected = "job-6bd36ae572f41ae9"
        result = _validate([{"job_id": expected}], {"assessments": [assessment(expected[:-1])]})
        self.assertIn(expected, result)

    def test_ambiguous_prefix_is_rejected(self):
        queue = [{"job_id": "job-123456789012a"}, {"job_id": "job-123456789012b"}]
        with self.assertRaises(ValueError):
            _validate(queue, {"assessments": [assessment("job-123456789012")]})


if __name__ == "__main__":
    unittest.main()
