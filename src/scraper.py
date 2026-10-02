import pandas as pd

from src.text_utils import clean_description


def scrape_jobs(config: dict) -> pd.DataFrame:
    search_cfg = config["search"]
    sites = search_cfg.get("sites", ["linkedin", "indeed"])
    results = []

    for keyword in search_cfg["keywords"]:
        for location in search_cfg["locations"]:
            print(f"  '{keyword}' in '{location}'...", end=" ", flush=True)
            try:
                df = _scrape_one(keyword, location, sites, search_cfg)
                if df is not None and not df.empty:
                    results.append(df)
                    print(f"{len(df)} jobs")
                else:
                    print("0 jobs")
            except Exception as e:
                print(f"failed ({e})")

    if not results:
        return pd.DataFrame()

    combined = pd.concat(results, ignore_index=True)
    combined = combined.drop_duplicates(subset=["job_url"], keep="first")
    combined = combined.drop_duplicates(subset=["title", "company"], keep="first")
    if "description" in combined.columns:
        combined["description"] = combined["description"].apply(clean_description)
    return combined


def _scrape_one(keyword: str, location: str, sites: list, cfg: dict) -> pd.DataFrame:
    from jobspy import scrape_jobs

    frames = []
    for site in sites:
        kwargs = {
            "site_name": site,
            "search_term": keyword,
            "location": location,
            "results_wanted": _site_results_limit(site, cfg),
            "verbose": 0,
        }
        if cfg.get("hours_old"):
            kwargs["hours_old"] = cfg["hours_old"]
        country_indeed = _indeed_country(location, cfg)
        if country_indeed:
            kwargs["country_indeed"] = country_indeed
        if cfg.get("linkedin_fetch_description"):
            kwargs["linkedin_fetch_description"] = True

        frame = scrape_jobs(**kwargs)
        if frame is not None and not frame.empty:
            frames.append(frame)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _site_results_limit(site: str, cfg: dict) -> int:
    """Return a site-specific result cap, falling back to the shared limit."""
    site_limits = cfg.get("results_per_site_by_site", {})
    normalized_site = site.strip().casefold()
    for configured_site, limit in site_limits.items():
        if str(configured_site).strip().casefold() == normalized_site:
            return int(limit)
    return int(cfg.get("results_per_site", 15))


def _indeed_country(location: str, cfg: dict) -> str | None:
    """Return the Indeed market for a search location, with legacy fallback."""
    country_map = cfg.get("country_indeed_by_location", {})
    normalized_location = location.strip().casefold()
    for configured_location, country in country_map.items():
        if str(configured_location).strip().casefold() == normalized_location:
            return str(country)
    return cfg.get("country_indeed")
