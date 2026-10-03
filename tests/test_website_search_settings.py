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
        "sites": ["indeed"],
        "hours_old": 24,
        "min_score": 80,
    })
    assert merged["search"]["keywords"] == ["translational scientist"]
    assert merged["search"]["locations"] == ["Switzerland", "Germany"]
    assert merged["search"]["sites"] == ["indeed"]
    assert merged["search"]["hours_old"] == 24
    assert merged["search"]["location_city_map"] == {"Basel": "Switzerland"}
    assert merged["assessment"] == {"min_score": 80, "industry_malus": 15}
    assert merged["prefilter"] == {"enabled": True}
    assert CONFIG["search"]["keywords"] == ["data scientist"]


def test_local_settings_keeps_only_website_supported_sites():
    assert website_search_settings.local_settings(CONFIG) == {
        "keywords": ["data scientist"],
        "locations": ["Basel"],
        "sites": ["linkedin"],
        "hours_old": 48,
        "min_score": 65,
    }


def test_local_settings_skips_unrepresentable_config():
    config = {"search": {"keywords": ["x"], "locations": ["y"], "sites": ["zip_recruiter"]}}
    assert website_search_settings.local_settings(config) is None
