import unittest

from src.job_prefilter import canonical_job_url, evaluate_job


CONFIG = {
    "enabled": True,
    "min_relevance_score": 7,
    "exclude_known_nonindustry": True,
    "exclude_obvious_nonindustry": True,
    "exclude_junior_roles": True,
    "exclude_management_roles": True,
}


class JobPrefilterTests(unittest.TestCase):
    def test_accepts_direct_bioinformatics_role(self):
        decision = evaluate_job({
            "title": "Senior Bioinformatics Scientist",
            "company": "Example Biotech",
            "description": "Develop Python pipelines for single-cell RNA-seq and spatial transcriptomics.",
        }, "industry", CONFIG)
        self.assertTrue(decision.accepted)

    def test_rejects_generic_ai_role_without_biology(self):
        decision = evaluate_job({
            "title": "Senior AI Engineer",
            "company": "Example Tech",
            "description": "Build recommender systems and cloud machine-learning services in Python.",
        }, "industry", CONFIG)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "generic_without_domain_evidence")

    def test_rejects_sales_even_with_domain_words(self):
        decision = evaluate_job({
            "title": "Genomics Sales Manager",
            "company": "Example Biotech",
            "description": "Sell single-cell sequencing products.",
        }, "industry", CONFIG)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "excluded_title")

    def test_rejects_junior_and_academic_roles(self):
        junior = evaluate_job({
            "title": "PhD Student in Computational Genomics",
            "company": "Example University",
            "description": "Analyze RNA-seq data.",
        }, None, CONFIG)
        self.assertFalse(junior.accepted)

        academic = evaluate_job({
            "title": "Bioinformatics Scientist",
            "company": "Example University",
            "description": "Analyze genomics data.",
        }, None, CONFIG)
        self.assertFalse(academic.accepted)
        self.assertEqual(academic.reason, "obvious_nonindustry_employer")

    def test_rejects_staff_role_with_explicit_people_leadership_gap(self):
        decision = evaluate_job({
            "title": "Staff AI/ML Engineer - Controllable Biology",
            "company": "GSK",
            "description": (
                "Apply machine learning to genomics and single-cell data. "
                "Requires 7+ years of deep learning and 5+ years as an engineering "
                "manager with direct reports."
            ),
        }, "industry", CONFIG)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "too_senior_requirements")

    def test_url_normalization_preserves_job_identifiers(self):
        url = "https://example.com/jobs/view?jk=abc&utm_source=mail&ref=feed"
        self.assertEqual(canonical_job_url(url), "https://example.com/jobs/view?jk=abc")


if __name__ == "__main__":
    unittest.main()
