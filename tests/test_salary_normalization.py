import unittest

from scripts.salary_normalization import disclosed_amounts, normalize_salary_fields


class SalaryNormalizationTests(unittest.TestCase):
    def test_austrian_decimal_cents_are_not_hundreds_of_thousands(self):
        row = {"salary_min": 776847, "salary_max": 803476,
               "salary_text": "7.768,47 Euro brutto monatlich / 8.034,76 Euro brutto monatlich"}
        fixed = normalize_salary_fields(row)
        self.assertEqual((fixed["salary_min"], fixed["salary_max"]), (7768.47, 8034.76))
        self.assertEqual(row["salary_min"], 776847)
        self.assertEqual(normalize_salary_fields({"salary_min": 346306, "salary_text": "ab EUR 3.463,06"})["salary_min"], 3463.06)

    def test_hourly_amount_is_not_expanded_without_k(self):
        row = {"salary_min": 15000, "salary_currency": "EUR", "salary_period": "hour", "salary_text": "15 € Stundenlohn"}
        fixed = normalize_salary_fields(row)
        self.assertEqual(fixed["salary_min"], 15)
        self.assertEqual(fixed["salary_period"], "hour")

    def test_real_thousands_and_k_disclosures_stay_unchanged(self):
        for amount, text in [(931700, "931,700.00 DKK"), (120000, "EUR 120k annually"), (15000, "EUR 15.000 monthly")]:
            row = {"salary_min": amount, "salary_text": text}
            self.assertEqual(normalize_salary_fields(row), row)
        self.assertEqual(disclosed_amounts("CHF 120'000–150’000"), [120000, 150000])

    def test_explicit_fourteen_monthly_payments_are_annualized_once(self):
        row = {"salary_min": 673020, "salary_max": None, "salary_period": "year",
               "salary_text": "6.730,20 Euro brutto monatlich, 14 mal jährlich"}
        fixed = normalize_salary_fields(row)
        self.assertEqual(fixed["salary_min"], 94222.80)
        self.assertEqual(fixed["salary_period"], "year")
        self.assertEqual(normalize_salary_fields(fixed), fixed)

    def test_missing_or_ambiguous_source_does_not_invent_a_correction(self):
        for text in (None, "Competitive salary", "15 or 150 EUR"):
            row = {"salary_min": 15000, "salary_text": text}
            self.assertEqual(normalize_salary_fields(row), row)
