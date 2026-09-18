import math
import unittest

from src.job_dates import posting_date_or_scrape_date


class JobDatesTests(unittest.TestCase):
    def test_preserves_a_real_posting_date(self):
        self.assertEqual(posting_date_or_scrape_date("2026-08-14", "2026-09-18"), "2026-08-14")

    def test_uses_scrape_date_when_posting_date_is_missing(self):
        for missing in (None, "", "  ", "nan", math.nan):
            with self.subTest(missing=missing):
                self.assertEqual(posting_date_or_scrape_date(missing, "2026-09-18"), "2026-09-18")

    def test_normalizes_an_iso_timestamp_to_a_date(self):
        self.assertEqual(
            posting_date_or_scrape_date("2026-08-14T09:30:00Z", "2026-09-18"),
            "2026-08-14",
        )


if __name__ == "__main__":
    unittest.main()
