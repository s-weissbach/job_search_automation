import pandas as pd


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
    return combined


def _scrape_one(keyword: str, location: str, sites: list, cfg: dict) -> pd.DataFrame:
    from jobspy import scrape_jobs

    kwargs = {
        "site_name": sites,
        "search_term": keyword,
        "location": location,
        "results_wanted": cfg.get("results_per_site", 15),
        "verbose": 0,
    }
    if cfg.get("hours_old"):
        kwargs["hours_old"] = cfg["hours_old"]
    if cfg.get("country_indeed"):
        kwargs["country_indeed"] = cfg["country_indeed"]
    if cfg.get("linkedin_fetch_description"):
        kwargs["linkedin_fetch_description"] = True

    return scrape_jobs(**kwargs)
