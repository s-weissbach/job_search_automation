import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "website_job_sync.py"
SPEC = importlib.util.spec_from_file_location("website_job_sync", MODULE_PATH)
assert SPEC and SPEC.loader
website_job_sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(website_job_sync)


def test_with_query_preserves_existing_query_and_adds_profile():
    url = website_job_sync.with_query(
        "https://example.test/api/jobs?existing=1",
        sync=1,
        page=2,
        profile="julia",
    )
    assert url == "https://example.test/api/jobs?existing=1&sync=1&page=2&profile=julia"
