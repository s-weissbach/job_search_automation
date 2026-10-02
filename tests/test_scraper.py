from src.scraper import _indeed_country, _site_results_limit


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
