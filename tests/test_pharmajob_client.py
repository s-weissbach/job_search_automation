from unittest.mock import patch
from datetime import date, timedelta

from src.pharmajob_client import fetch_pharmajob_jobs


@patch("src.pharmajob_client._query_one", return_value=[])
def test_source_specific_terms_override_main_search(mock_query):
    config = {
        "search": {
            "keywords": ["precise linkedin term"],
            "locations": ["Switzerland"],
        },
        "pharmajob_io": {
            "enabled": True,
            "base_url": "http://127.0.0.1:8000",
            "keywords": ["scientist", "immunology"],
            "locations": ["Germany", "Denmark"],
        },
    }

    fetch_pharmajob_jobs(config)

    queried_pairs = {(call.args[1], call.args[2]) for call in mock_query.call_args_list}
    assert queried_pairs == {
        ("scientist", "Germany"),
        ("scientist", "Denmark"),
        ("immunology", "Germany"),
        ("immunology", "Denmark"),
    }


@patch("src.pharmajob_client._query_one")
def test_recent_window_drops_old_and_unknown_posting_dates(mock_query):
    mock_query.return_value = [
        {"job_url": "https://example.com/recent", "title": "Scientist", "company": "A", "date_posted": date.today().isoformat()},
        {"job_url": "https://example.com/old", "title": "Scientist", "company": "B", "date_posted": (date.today() - timedelta(days=15)).isoformat()},
        {"job_url": "https://example.com/unknown", "title": "Scientist", "company": "C", "date_posted": None},
    ]
    config = {
        "search": {"keywords": ["scientist"], "locations": ["Switzerland"]},
        "pharmajob_io": {"enabled": True, "max_age_days": 14},
    }

    result = fetch_pharmajob_jobs(config)

    assert result["job_url"].tolist() == ["https://example.com/recent"]
