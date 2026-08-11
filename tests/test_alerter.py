import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.alerter import (
    exclude_already_alerted,
    filter_high_score_jobs,
    render_alert_email,
    send_high_score_alert,
)


def _jobs_df(rows):
    return pd.DataFrame(rows)


class FilterHighScoreJobsTest(unittest.TestCase):
    def test_keeps_only_strictly_above_threshold_highest_first(self):
        df = _jobs_df([
            {"job_url": "a", "title": "A", "company": "Co", "fit_score": 90},
            {"job_url": "b", "title": "B", "company": "Co", "fit_score": 95},
            {"job_url": "c", "title": "C", "company": "Co", "fit_score": 91},
        ])
        result = filter_high_score_jobs(df, threshold=90)
        self.assertEqual([j["job_url"] for j in result], ["b", "c"])

    def test_empty_dataframe_returns_empty_list(self):
        self.assertEqual(filter_high_score_jobs(_jobs_df([]), threshold=90), [])

    def test_missing_fit_score_column_returns_empty_list(self):
        df = _jobs_df([{"job_url": "a", "title": "A"}])
        self.assertEqual(filter_high_score_jobs(df, threshold=90), [])


class ExcludeAlreadyAlertedTest(unittest.TestCase):
    def test_drops_urls_present_in_already_alerted_set(self):
        jobs = [{"job_url": "a"}, {"job_url": "b"}, {"job_url": "c"}]
        result = exclude_already_alerted(jobs, {"b"})
        self.assertEqual([j["job_url"] for j in result], ["a", "c"])

    def test_no_already_alerted_returns_all_jobs_unchanged(self):
        jobs = [{"job_url": "a"}, {"job_url": "b"}]
        self.assertEqual(exclude_already_alerted(jobs, set()), jobs)


class RenderAlertEmailTest(unittest.TestCase):
    def test_includes_title_company_score_and_link_for_each_job(self):
        jobs = [
            {"job_url": "https://x.test/1", "title": "Scientist", "company": "Acme", "fit_score": 95},
            {"job_url": "https://x.test/2", "title": "Researcher", "company": "Beta", "fit_score": 92},
        ]
        subject, html, text = render_alert_email(jobs)
        self.assertIn("2 job", subject)
        for j in jobs:
            self.assertIn(j["title"], html)
            self.assertIn(j["company"], html)
            self.assertIn(str(j["fit_score"]), html)
            self.assertIn(j["job_url"], html)
            self.assertIn(j["title"], text)
            self.assertIn(j["job_url"], text)

    def test_escapes_html_special_characters(self):
        jobs = [{"job_url": "https://x.test/1", "title": "R&D <Lead>", "company": "A & B", "fit_score": 95}]
        _, html, _ = render_alert_email(jobs)
        self.assertNotIn("<Lead>", html)
        self.assertIn("&lt;Lead&gt;", html)


class SendHighScoreAlertTest(unittest.TestCase):
    def setUp(self):
        self.df = _jobs_df([
            {"job_url": "https://x.test/1", "title": "A", "company": "Co", "fit_score": 95},
            {"job_url": "https://x.test/2", "title": "B", "company": "Co", "fit_score": 60},
        ])

    @patch.dict("os.environ", {}, clear=True)
    def test_skips_without_resend_api_key(self):
        with patch("src.alerter.requests.post") as mock_post:
            sent = send_high_score_alert(self.df)
            self.assertEqual(sent, 0)
            mock_post.assert_not_called()

    @patch.dict("os.environ", {"RESEND_API_KEY": "key"}, clear=True)
    def test_skips_when_no_job_scores_above_threshold(self):
        low_df = _jobs_df([{"job_url": "https://x.test/3", "title": "C", "company": "Co", "fit_score": 40}])
        with patch("src.alerter.requests.post") as mock_post:
            sent = send_high_score_alert(low_df)
            self.assertEqual(sent, 0)
            mock_post.assert_not_called()

    @patch.dict("os.environ", {"RESEND_API_KEY": "key"}, clear=True)
    def test_sends_one_email_for_qualifying_jobs_and_marks_them_alerted(self):
        mock_response = MagicMock(status_code=200)
        supabase_client = MagicMock()
        supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []

        with patch("src.alerter.requests.post", return_value=mock_response) as mock_post:
            sent = send_high_score_alert(self.df, supabase_client=supabase_client)

        self.assertEqual(sent, 1)
        mock_post.assert_called_once()
        supabase_client.table.assert_any_call("job_alerts")
        upsert_call = supabase_client.table.return_value.upsert.call_args
        self.assertEqual([r["job_url"] for r in upsert_call[0][0]], ["https://x.test/1"])

    @patch.dict("os.environ", {"RESEND_API_KEY": "key"}, clear=True)
    def test_does_not_resend_for_already_alerted_jobs(self):
        supabase_client = MagicMock()
        supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"job_url": "https://x.test/1"}
        ]
        with patch("src.alerter.requests.post") as mock_post:
            sent = send_high_score_alert(self.df, supabase_client=supabase_client)

        self.assertEqual(sent, 0)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
