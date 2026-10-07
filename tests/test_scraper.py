from unittest.mock import patch

import pandas as pd

from src.scraper import _indeed_country, _site_results_limit, scrape_jobs


def test_indeed_country_uses_location_mapping_case_insensitively():
    cfg = {
        "country_indeed": "Germany",
        "country_indeed_by_location": {
            "Switzerland": "Switzerland",
            "Denmark": "Denmark",
        },
    }

    assert _indeed_country(" denmark ", cfg) == "Denmark"


def test_indeed_country_falls_back_to_legacy_default():
    cfg = {"country_indeed": "Germany"}

    assert _indeed_country("Austria", cfg) == "Germany"


def test_indeed_country_can_be_unset():
    assert _indeed_country("Switzerland", {}) is None


def test_site_results_limit_uses_site_override_case_insensitively():
    cfg = {
        "results_per_site": 20,
        "results_per_site_by_site": {"linkedin": 1000},
    }

    assert _site_results_limit("LinkedIn", cfg) == 1000
    assert _site_results_limit("indeed", cfg) == 20


@patch("src.scraper._sleep")
@patch("src.scraper._scrape_one")
def test_scrape_jobs_paces_searches_and_retries_empty_slice(scrape_one, sleep):
    scrape_one.side_effect = [
        pd.DataFrame(),
        pd.DataFrame([{"job_url": "one", "title": "Scientist", "company": "A"}]),
        pd.DataFrame([{"job_url": "two", "title": "Scientist II", "company": "B"}]),
    ]
    config = {
        "search": {
            "keywords": ["scientist"],
            "locations": ["Switzerland", "Germany"],
            "sites": ["linkedin"],
            "search_delay_seconds": 5,
            "empty_retry_attempts": 1,
            "empty_retry_delay_seconds": 45,
        }
    }

    result = scrape_jobs(config)

    assert len(result) == 2
    assert scrape_one.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [45, 5]
