import time
from datetime import datetime
from zoneinfo import ZoneInfo

_BASEL_TZ = ZoneInfo("Europe/Zurich")

import pandas as pd
import requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}


import re
from functools import lru_cache

import pycountry


@lru_cache(maxsize=256)
def _country_to_iso(name: str) -> str | None:
    """Return the ISO 3166-1 alpha-2 code for a country name, or None.

    Uses pycountry's fuzzy search so any country in the ISO database works
    without a hardcoded lookup table.
    """
    try:
        results = pycountry.countries.search_fuzzy(name)
        return results[0].alpha_2 if results else None
    except LookupError:
        return None

_REMOTE_TERMS = {"remote", "worldwide", "global", "anywhere", "hybrid"}


def _keyword_match(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def _location_match(
    job_location: str,
    configured_locations: list[str],
    city_map: dict[str, str] | None = None,
) -> bool:
    """Return True if job_location matches any configured location.

    Matching order:
    1. Empty location or empty config → pass through
    2. Direct country name substring ("Germany" in "Biberach, Germany, …")
    3. ISO code word-boundary match (\bDE\b in "Mainz, RP, DE, 55131")
    4. "Remote" keywords (remote / worldwide / global / …)
    5. User-supplied city→country map from config (search.location_city_map)

    Note: Workday "N Locations" placeholders are NOT handled here — callers
    must resolve the actual location list before calling this function.
    """
    if not configured_locations:
        return True
    if not job_location:
        return True

    loc_lower = job_location.lower()
    cfg_lowers = [c.lower() for c in configured_locations]

    for cfg in cfg_lowers:
        if cfg in loc_lower:
            return True
        iso = _country_to_iso(cfg)
        # ISO check is case-sensitive against the original location string.
        # Using re.I would match "DE" against "de" in Spanish city names like
        # "Cornellà de Llobregat", causing false positives for Germany.
        # Workday embeds ISO codes in uppercase ("DE", "CH", "GB") so this
        # correctly matches legitimate ISO-coded location strings.
        if iso and re.search(rf"\b{iso}\b", job_location):
            return True
        if cfg == "remote" and any(t in loc_lower for t in _REMOTE_TERMS):
            return True

    # User-configured city overrides (search.location_city_map in config.yaml)
    # Example: {"Basel": "Switzerland", "Schaftenau": "Austria"}
    if city_map:
        for city, country in city_map.items():
            if city.lower() in loc_lower and country.lower() in cfg_lowers:
                return True

    return False


def _row(title, company, location, url, description, site, date_posted=None):
    return {
        "title": title or "",
        "company": company or "",
        "location": location or "",
        "job_url": url or "",
        "description": description or "",
        "site": site,
        "date_posted": date_posted,
        "is_remote": None,
        "job_type": None,
        "min_amount": None,
        "max_amount": None,
        "currency": None,
    }


def _scrape_greenhouse(token: str, name: str, keywords: list[str], locations: list[str], city_map: dict[str, str]) -> list[dict]:
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
            headers=_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except Exception as e:
        print(f"failed ({e})")
        return []

    rows = []
    for job in jobs:
        title = job.get("title", "")
        content = job.get("content", "")
        if not _keyword_match(title + " " + content, keywords):
            continue
        offices = job.get("offices") or []
        location = offices[0].get("name", "") if offices else ""
        if not _location_match(location, locations, city_map):
            continue
        rows.append(_row(
            title=title, company=name, location=location,
            url=job.get("absolute_url", ""),
            description=content, site="greenhouse",
            date_posted=(job.get("updated_at") or "")[:10] or None,
        ))
    return rows


def _scrape_lever(slug: str, name: str, keywords: list[str], locations: list[str], city_map: dict[str, str]) -> list[dict]:
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            headers=_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json()
    except Exception as e:
        print(f"failed ({e})")
        return []

    rows = []
    for job in jobs:
        title = job.get("text", "")
        sections = (job.get("descriptionBody") or {}).get("body") or []
        desc = " ".join(s.get("content", "") for s in sections if isinstance(s, dict))
        if not _keyword_match(title + " " + desc, keywords):
            continue
        location = (job.get("categories") or {}).get("location") or job.get("workplaceType") or ""
        if not _location_match(location, locations, city_map):
            continue
        created = job.get("createdAt")
        date = datetime.fromtimestamp(created / 1000, tz=_BASEL_TZ).strftime("%Y-%m-%d") if created else None
        rows.append(_row(
            title=title, company=name, location=location,
            url=job.get("hostedUrl", ""),
            description=desc, site="lever", date_posted=date,
        ))
    return rows


def _workday_urls(api_url: str) -> tuple[str, str, str]:
    """Return (base_domain, board, base_cxs) derived from the Workday jobs API URL."""
    parts = api_url.split("/wday/cxs/", 1)
    base_domain = parts[0]
    path_parts = parts[1].split("/") if len(parts) > 1 else []
    tenant = path_parts[0] if len(path_parts) > 0 else ""
    board = path_parts[1] if len(path_parts) > 1 else ""
    base_cxs = f"{base_domain}/wday/cxs/{tenant}/{board}"
    return base_domain, board, base_cxs


def _fetch_workday_detail(base_cxs: str, external_path: str) -> tuple[str, list[str]]:
    """Fetch a Workday job detail page.

    Returns (description, locations) where `locations` is a list of location
    strings suitable for `_location_match`.  The primary location is returned
    as "City, Country" (e.g. "Penzberg, Germany") so the country-name and ISO
    checks both fire correctly.  Additional locations (bare city names) are
    appended so city_map overrides can still match them.

    Returns ("", []) on any error.

    Workday CXS detail response structure (jobPostingInfo):
      location           – str, primary city name ("Penzberg")
      additionalLocations – list[str], extra city names (["Rotkreuz"])
      country            – dict with "descriptor" ("Germany") and "alpha2Code" ("DE")
    """
    try:
        resp = requests.get(base_cxs + external_path, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        description = info.get("jobDescription", "") or ""

        locs: list[str] = []

        city = info.get("location") or ""
        country_obj = info.get("country") or {}
        country_name = country_obj.get("descriptor") or ""
        country_code = country_obj.get("alpha2Code") or ""

        # Primary location as "City, Country" so _location_match name/ISO checks work
        if city and country_name:
            locs.append(f"{city}, {country_name}")
        elif city:
            locs.append(city)
        # Bare ISO code as a fallback (matches \bDE\b style check)
        if country_code:
            locs.append(country_code)

        # Additional locations are city-only strings — country unknown.
        # They match via city_map overrides in config or if the city name
        # itself contains a country word (rare but handled).
        for extra in (info.get("additionalLocations") or []):
            if isinstance(extra, str) and extra:
                locs.append(extra)
            elif isinstance(extra, dict):
                name = extra.get("descriptor") or extra.get("name") or ""
                if name:
                    locs.append(name)

        return description, locs
    except Exception:
        return "", []


def _scrape_workday(
    api_url: str,
    name: str,
    keywords: list[str],
    locations: list[str],
    city_map: dict[str, str],
    results_per_kw: int,
    fetch_descriptions: bool,
) -> list[dict]:
    base_domain, board, base_cxs = _workday_urls(api_url)
    seen: set[str] = set()
    rows = []

    for kw in keywords:
        try:
            resp = requests.post(
                api_url,
                json={"appliedFacets": {}, "limit": results_per_kw, "offset": 0, "searchText": kw},
                headers={**_HEADERS, "Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            postings = resp.json().get("jobPostings", [])
        except Exception as e:
            print(f"\n    {name} ({kw}): failed ({e})", end="")
            continue

        for job in postings:
            ext_path = job.get("externalPath", "")
            uid = ext_path or job.get("title", "")
            if uid in seen:
                continue
            seen.add(uid)

            job_url = f"{base_domain}/{board}{ext_path}" if ext_path else base_domain

            location = job.get("locationsText", "")
            description = ""

            is_multi = bool(re.search(r"^\d+\s+locations?$", location.strip(), re.I))

            if is_multi:
                # "N Locations" — the listing API doesn't expose the actual locations.
                # Fetch the detail page to resolve them and filter properly.
                # Without this, every global job (US + Japan + Germany) would slip through.
                if not ext_path:
                    continue  # No detail URL to resolve — skip
                description, actual_locs = _fetch_workday_detail(base_cxs, ext_path)
                time.sleep(0.2)
                if not actual_locs:
                    continue  # Detail page exposed no locations — skip to avoid false positives
                if not any(_location_match(loc, locations, city_map) for loc in actual_locs):
                    continue
                location = "; ".join(actual_locs)
                if not fetch_descriptions:
                    description = ""  # only keep if explicitly requested
            else:
                if not _location_match(location, locations, city_map):
                    continue
                if fetch_descriptions and ext_path:
                    description, _ = _fetch_workday_detail(base_cxs, ext_path)
                    time.sleep(0.2)

            rows.append(_row(
                title=job.get("title", ""),
                company=name,
                location=location,
                url=job_url,
                description=description,
                site="workday",
                date_posted=job.get("postedOn") or None,
            ))
        time.sleep(0.3)

    return rows


def _fetch_successfactors_description(job_url: str) -> str:
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(
            job_url,
            headers={**_HEADERS, "Accept": "text/html,application/xhtml+xml"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()

        # Try common SuccessFactors CSB containers
        for selector in [
            "div.job-description",
            "div.section_jobDetail",
            "[class*='jobDescription']",
            "[class*='job-detail']",
            "[id*='job-description']",
            "main",
            "article",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text

        # Fallback: collect meaningful paragraphs and headings
        parts = [t.get_text(strip=True) for t in soup.find_all(["h2", "h3", "p", "li"])]
        return "\n".join(p for p in parts if len(p) > 20)
    except Exception:
        return ""


def _scrape_successfactors(base_url: str, name: str, keywords: list[str], locations: list[str], city_map: dict[str, str], results_per_kw: int, fetch_descriptions: bool) -> list[dict]:
    from bs4 import BeautifulSoup

    seen: set[str] = set()
    rows = []

    for kw in keywords:
        try:
            resp = requests.get(
                f"{base_url}/search/",
                params={"q": kw, "sortColumn": "referencedate", "sortDirection": "desc",
                        "startrow": 0, "numrows": results_per_kw},
                headers={**_HEADERS, "Accept": "text/html,application/xhtml+xml"},
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"\n    {name} ({kw}): failed ({e})", end="")
            continue

        for link in soup.find_all("a", href=lambda h: h and "/job/" in h):
            title = link.get_text(strip=True)
            if not title:
                continue
            href = link.get("href", "")
            job_url = base_url + href if href.startswith("/") else href
            if job_url in seen:
                continue
            seen.add(job_url)

            location = ""
            date_str = None

            # Layout 1: table row — <tr><td><a/></td><td>location</td><td>date</td></tr>
            td = link.find_parent("td")
            if td:
                cells = td.find_parent("tr").find_all("td") if td.find_parent("tr") else []
                location = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                date_str = cells[2].get_text(strip=True) if len(cells) > 2 else None

            else:
                # Layout 2: oneline divs (Boehringer style)
                # Title oneline → location oneline containing section-label + value div
                oneline = link.find_parent(class_=lambda c: c and "oneline" in c)
                if oneline:
                    loc_line = oneline.find_next_sibling(class_=lambda c: c and "oneline" in c)
                    if loc_line:
                        val = loc_line.find(id=lambda i: i and "location-value" in i)
                        if val:
                            location = val.get_text(strip=True)
                        else:
                            for label in loc_line.find_all(class_=lambda c: c and "section-label" in c):
                                label.decompose()
                            for sr in loc_line.find_all(class_="sr-only"):
                                sr.decompose()
                            location = loc_line.get_text(strip=True)

                # Layout 3: sibling divs — <div>title<a/></div><div>Location label</div><div>value</div>
                if not location:
                    title_div = link.find_parent("div")
                    if title_div:
                        for sib in title_div.find_next_siblings("div"):
                            text = sib.get_text(strip=True)
                            if text.lower() not in ("", "location", "date", "title"):
                                location = text
                                break

            if not _location_match(location, locations, city_map):
                continue

            description = ""
            if fetch_descriptions:
                description = _fetch_successfactors_description(job_url)
                time.sleep(0.2)

            rows.append(_row(
                title=title, company=name, location=location,
                url=job_url, description=description,
                site="successfactors", date_posted=date_str,
            ))

        time.sleep(0.3)

    return rows


def scrape_portals(config: dict) -> pd.DataFrame:
    portals_cfg = config.get("company_portals")
    if not portals_cfg:
        return pd.DataFrame()

    keywords = config["search"]["keywords"]
    locations = config["search"].get("locations", [])
    city_map = config["search"].get("location_city_map", {})
    results_per = config["search"].get("results_per_site", 20)
    fetch_desc = portals_cfg.get("fetch_workday_descriptions", False)
    fetch_sf_desc = portals_cfg.get("fetch_successfactors_descriptions", False)
    rows = []

    for entry in portals_cfg.get("greenhouse", []):
        name = entry.get("name", entry["token"])
        print(f"  [greenhouse] {name}...", end=" ", flush=True)
        found = _scrape_greenhouse(entry["token"], name, keywords, locations, city_map)
        rows.extend(found)
        print(f"{len(found)} jobs")

    for entry in portals_cfg.get("lever", []):
        name = entry.get("name", entry["slug"])
        print(f"  [lever] {name}...", end=" ", flush=True)
        found = _scrape_lever(entry["slug"], name, keywords, locations, city_map)
        rows.extend(found)
        print(f"{len(found)} jobs")

    for entry in portals_cfg.get("workday", []):
        name = entry.get("name", entry["api_url"])
        print(f"  [workday] {name}...", end=" ", flush=True)
        found = _scrape_workday(entry["api_url"], name, keywords, locations, city_map, results_per, fetch_desc)
        rows.extend(found)
        print(f"{len(found)} jobs")

    for entry in portals_cfg.get("successfactors", []):
        name = entry.get("name", entry["base_url"])
        print(f"  [successfactors] {name}...", end=" ", flush=True)
        found = _scrape_successfactors(entry["base_url"], name, keywords, locations, city_map, results_per, fetch_sf_desc)
        rows.extend(found)
        print(f"{len(found)} jobs")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["job_url"], keep="first")
    df = df.drop_duplicates(subset=["title", "company"], keep="first")
    return df
