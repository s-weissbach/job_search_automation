import unittest

from src.job_prefilter import canonical_job_url, evaluate_job


CONFIG = {
    "enabled": True,
    "min_relevance_score": 7,
    "exclude_known_nonindustry": False,
    "exclude_obvious_nonindustry": False,
    "exclude_junior_roles": True,
    "exclude_management_roles": False,
    "exclude_explicit_seniority_mismatches": False,
}


class JobPrefilterTests(unittest.TestCase):
    def test_accepts_direct_bioinformatics_role(self):
        decision = evaluate_job({
            "title": "Senior Bioinformatics Scientist",
            "company": "Example Biotech",
            "description": "Develop Python pipelines for single-cell RNA-seq and spatial transcriptomics.",
        }, "industry", CONFIG)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tier, "strong")

    def test_rejects_generic_ai_role_without_biology(self):
        decision = evaluate_job({
            "title": "Senior AI Engineer",
            "company": "Example Tech",
            "description": "Build recommender systems and cloud machine-learning services in Python.",
        }, "industry", CONFIG)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "generic_without_biological_evidence")

    def test_rejects_sales_even_with_domain_words(self):
        decision = evaluate_job({
            "title": "Genomics Sales Manager",
            "company": "Example Biotech",
            "description": "Sell single-cell sequencing products.",
        }, "industry", CONFIG)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "definite_title_mismatch")

    def test_rejects_junior_but_keeps_relevant_academic_role(self):
        junior = evaluate_job({
            "title": "PhD Student in Computational Genomics",
            "company": "Example University",
            "description": "Analyze RNA-seq data.",
        }, None, CONFIG)
        self.assertFalse(junior.accepted)
        self.assertEqual(junior.reason, "too_junior")

        academic = evaluate_job({
            "title": "Bioinformatics Scientist",
            "company": "Example University",
            "description": "Analyze genomics data.",
        }, "academia", CONFIG)
        self.assertTrue(academic.accepted)
        self.assertEqual(academic.tier, "strong")
        self.assertIn("sector:academia", academic.signals)

        german_postdoc = evaluate_job({
            "title": "Postdoktorandin Neuroimmunologie",
            "company": "Charité",
            "description": "Single-cell transcriptomics and genomics.",
        }, "academia", CONFIG)
        self.assertFalse(german_postdoc.accepted)
        self.assertEqual(german_postdoc.reason, "too_junior")

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
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tier, "borderline")
        self.assertEqual(decision.reason, "borderline_seniority_gap")

    def test_keeps_domain_specific_software_role_as_borderline(self):
        decision = evaluate_job({
            "title": "Software Engineer, Computational Genomics",
            "company": "Example Biotech",
            "description": "Build Python pipelines for RNA-seq analysis.",
        }, "industry", CONFIG)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tier, "borderline")

    def test_keeps_single_domain_signal_for_model_judgment(self):
        decision = evaluate_job({
            "title": "Associate Research Scientist II",
            "company": "Example Diagnostics",
            "description": "Use Python to develop bioinformatics analyses.",
        }, "industry", CONFIG)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tier, "borderline")

    def test_url_normalization_preserves_job_identifiers(self):
        url = "https://example.com/jobs/view?jk=abc&utm_source=mail&ref=feed"
        self.assertEqual(canonical_job_url(url), "https://example.com/jobs/view?jk=abc")


if __name__ == "__main__":
    unittest.main()
