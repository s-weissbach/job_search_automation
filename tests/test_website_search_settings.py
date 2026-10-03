import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "website_search_settings.py"
SPEC = importlib.util.spec_from_file_location("website_search_settings", MODULE_PATH)
assert SPEC and SPEC.loader
website_search_settings = importlib.util.module_from_spec(SPEC)
sys.modules["website_search_settings"] = website_search_settings
SPEC.loader.exec_module(website_search_settings)


CONFIG = {
    "search": {
        "keywords": ["data scientist"],
        "locations": ["Basel"],
        "sites": ["linkedin", "zip_recruiter"],
        "hours_old": 48,
        "location_city_map": {"Basel": "Switzerland"},
    },
    "assessment": {"min_score": 65, "industry_malus": 15},
    "prefilter": {"enabled": True},
}


def test_apply_settings_overrides_only_editable_fields():
    merged = website_search_settings.apply_settings(CONFIG, {
        "keywords": ["translational scientist"],
        "locations": ["Switzerland", "Germany"],
        # Stale values saved by older website versions must be ignored.
        "sites": ["indeed"],
        "hours_old": 24,
        "min_score": 80,
    })
    assert merged["search"]["keywords"] == ["translational scientist"]
    assert merged["search"]["locations"] == ["Switzerland", "Germany"]
    assert merged["search"]["sites"] == ["linkedin", "zip_recruiter"]
    assert merged["search"]["hours_old"] == 48
    assert merged["search"]["location_city_map"] == {"Basel": "Switzerland"}
    assert merged["assessment"] == {"min_score": 65, "industry_malus": 15}
    assert merged["prefilter"] == {"enabled": True}
    assert CONFIG["search"]["keywords"] == ["data scientist"]


def test_local_settings_uploads_only_titles_and_locations():
    assert website_search_settings.local_settings(CONFIG) == {
        "keywords": ["data scientist"],
        "locations": ["Basel"],
    }


def test_local_settings_skips_unrepresentable_config():
    config = {"search": {"keywords": ["x"], "locations": [], "sites": ["linkedin"]}}
    assert website_search_settings.local_settings(config) is None
