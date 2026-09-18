import unittest

from src.active_checker import _classify_response


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


if __name__ == "__main__":
    unittest.main()
