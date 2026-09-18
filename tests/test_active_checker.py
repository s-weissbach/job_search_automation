import unittest
from datetime import date

import pandas as pd

from src.active_checker import _classify_response, _expire_low_score_jobs


class ActiveCheckerTests(unittest.TestCase):
    def test_linkedin_expired_redirect_is_closed_despite_http_200(self):
        self.assertEqual(
            _classify_response(
                "https://www.linkedin.com/jobs/view/123",
                200,
                "https://www.linkedin.com/jobs/data-scientist-jobs?trk=expired_jd_redirect",
                "This job is no longer available",
            ),
            "expired",
        )

    def test_live_linkedin_job_stays_active(self):
        self.assertEqual(
            _classify_response(
                "https://www.linkedin.com/jobs/view/123",
                200,
                "https://www.linkedin.com/jobs/view/123",
                "Current listing",
            ),
            "active",
        )

    def test_rate_limit_is_unknown_not_expired(self):
        self.assertEqual(
            _classify_response("https://example.com/job", 429, "https://example.com/job", ""),
            "unknown",
        )

    def test_low_score_jobs_expire_at_two_weeks_without_url_checks(self):
        df = pd.DataFrame([
            {"fit_score": 59, "date_posted": "2026-09-04", "assessed_at": "2026-09-04", "is_active": "active", "last_active_check": ""},
            {"fit_score": 59, "date_posted": "2026-09-05", "assessed_at": "2026-09-05", "is_active": "active", "last_active_check": ""},
            {"fit_score": 60, "date_posted": "2026-08-01", "assessed_at": "2026-08-01", "is_active": "active", "last_active_check": ""},
            {"fit_score": 42, "date_posted": None, "assessed_at": "2026-08-20", "is_active": "unknown", "last_active_check": ""},
            {"fit_score": 10, "date_posted": "2026-07-01", "assessed_at": "2026-07-01", "is_active": "expired", "last_active_check": "2026-08-01"},
        ])

        changed = _expire_low_score_jobs(df, date(2026, 9, 18))

        self.assertEqual(changed, [0, 3])
        self.assertEqual(df.loc[0, "is_active"], "expired")
        self.assertEqual(df.loc[0, "last_active_check"], "2026-09-18")
        self.assertEqual(df.loc[1, "is_active"], "active")
        self.assertEqual(df.loc[2, "is_active"], "active")
        self.assertEqual(df.loc[4, "last_active_check"], "2026-08-01")


if __name__ == "__main__":
    unittest.main()
