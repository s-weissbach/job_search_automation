import time
from datetime import datetime

import pandas as pd
import requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}


def _keyword_match(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


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


def _scrape_greenhouse(token: str, name: str, keywords: list[str]) -> list[dict]:
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
        rows.append(_row(
            title=title, company=name, location=location,
            url=job.get("absolute_url", ""),
            description=content, site="greenhouse",
            date_posted=(job.get("updated_at") or "")[:10] or None,
        ))
    return rows


def _scrape_lever(slug: str, name: str, keywords: list[str]) -> list[dict]:
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
        created = job.get("createdAt")
        date = datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d") if created else None
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


def _fetch_workday_description(base_cxs: str, external_path: str) -> str:
    try:
        resp = requests.get(base_cxs + external_path, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        return info.get("jobDescription", "") or ""
    except Exception:
        return ""


def _scrape_workday(
    api_url: str,
    name: str,
    keywords: list[str],
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

            description = ""
            if fetch_descriptions and ext_path:
                description = _fetch_workday_description(base_cxs, ext_path)
                time.sleep(0.2)

            rows.append(_row(
                title=job.get("title", ""),
                company=name,
                location=job.get("locationsText", ""),
                url=job_url,
                description=description,
                site="workday",
                date_posted=job.get("postedOn") or None,
            ))
        time.sleep(0.3)

    return rows


def scrape_portals(config: dict) -> pd.DataFrame:
    portals_cfg = config.get("company_portals")
    if not portals_cfg:
        return pd.DataFrame()

    keywords = config["search"]["keywords"]
    results_per = config["search"].get("results_per_site", 20)
    fetch_desc = portals_cfg.get("fetch_workday_descriptions", False)
    rows = []

    for entry in portals_cfg.get("greenhouse", []):
        name = entry.get("name", entry["token"])
        print(f"  [greenhouse] {name}...", end=" ", flush=True)
        found = _scrape_greenhouse(entry["token"], name, keywords)
        rows.extend(found)
        print(f"{len(found)} jobs")

    for entry in portals_cfg.get("lever", []):
        name = entry.get("name", entry["slug"])
        print(f"  [lever] {name}...", end=" ", flush=True)
        found = _scrape_lever(entry["slug"], name, keywords)
        rows.extend(found)
        print(f"{len(found)} jobs")

    for entry in portals_cfg.get("workday", []):
        name = entry.get("name", entry["api_url"])
        print(f"  [workday] {name}...", end=" ", flush=True)
        found = _scrape_workday(entry["api_url"], name, keywords, results_per, fetch_desc)
        rows.extend(found)
        print(f"{len(found)} jobs")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["job_url"], keep="first")
    df = df.drop_duplicates(subset=["title", "company"], keep="first")
    return df
