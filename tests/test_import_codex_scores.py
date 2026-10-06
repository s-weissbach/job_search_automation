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

    def test_score_is_normalized_to_component_sum(self):
        item = assessment("job-1")
        item["score_components"] = {"domain": 30, "methods": 20}
        result = _validate([{"job_id": "job-1"}], {"assessments": [item]})
        self.assertEqual(result["job-1"]["score"], 50)

    def test_score_components_are_accepted_when_they_sum(self):
        item = assessment("job-1")
        item["score_components"] = {"domain": 30, "methods": 50}
        result = _validate([{"job_id": "job-1"}], {"assessments": [item]})
        self.assertEqual(result["job-1"]["score"], 80)

    def test_julia_masters_level_role_is_forced_to_too_junior_and_capped(self):
        item = assessment("job-1")
        item["score"] = 90
        item["score_components"] = {"domain": 25, "methods": 25, "responsibilities": 20, "seniority": 15, "industry": 5}
        queue = [{"job_id": "job-1", "title": "Research Scientist", "description": "Requires a Master's degree and three years of experience."}]
        result = _validate(queue, {"assessments": [item]}, "julia-phd")
        self.assertEqual(result["job-1"]["seniority_match"], "too_junior")
        self.assertEqual(result["job-1"]["score"], 59)

    def test_industry_r_and_d_always_receives_full_industry_component(self):
        item = assessment("job-1")
        item["score_components"] = {
            "scientific_domain": 18,
            "hands_on_methods": 20,
            "role_responsibilities": 15,
            "seniority": 14,
            "industry_fit": 2,
        }
        queue = [{"job_id": "job-1", "title": "Scientist", "description": "PhD required"}]

        result = _validate(queue, {"assessments": [item]}, "julia-phd")

        self.assertEqual(result["job-1"]["score_components"]["industry_fit"], 5)
        self.assertEqual(result["job-1"]["score"], 72)


if __name__ == "__main__":
    unittest.main()
