from scripts.perry_salary_backfill import has_salary_signal


def test_salary_signal_recognizes_currency_and_multilingual_terms():
    assert has_salary_signal({"description": "Annual base salary: CHF 120,000–145,000"})
    assert has_salary_signal({"description": "Die Vergütung richtet sich nach Erfahrung."})
    assert has_salary_signal({"description": "€80.000 brutto pro Jahr"})


def test_salary_signal_rejects_unrelated_job_copy():
    assert not has_salary_signal({"description": "Develop organoid assays in a collaborative team."})
