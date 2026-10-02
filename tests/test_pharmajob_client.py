from unittest.mock import patch

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
