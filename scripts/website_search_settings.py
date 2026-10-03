#!/usr/bin/env python3
"""Overlay the website-edited search settings onto a profile's local config.

The Job Search page on stephanweissbach.dev lets each profile edit its job
titles, locations, maximum posting age and minimum score. Before a
run, this script fetches those settings and writes an effective config that
the rest of the pipeline reads instead of config.yaml. Only those five fields
are overridden; everything else (sources, prefilter tuning, portals, ...)
stays local.

The website is never a hard dependency: if it is unreachable, the local
config is used unchanged. When the website has nothing saved yet, the local
values are uploaded once so the settings pane starts from the current setup.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from website_job_sync import keychain_key, with_query  # noqa: E402


DEFAULT_URL = "https://stephanweissbach.dev/api/jobs/settings"


def request_settings(url: str, key: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method,
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read(1_000_000))
    except HTTPError as exc:
        detail = exc.read(2000).decode(errors="replace")
        raise RuntimeError(f"Website settings API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"Website settings request failed: {exc}") from exc


def local_settings(config: dict) -> dict | None:
    """The website-editable subset of a local config, or None if it can't be represented."""
    search = config.get("search") or {}
    keywords = [str(k) for k in search.get("keywords") or [] if str(k).strip()]
    locations = [str(loc) for loc in search.get("locations") or [] if str(loc).strip()]
    if not keywords or not locations:
        return None
    return {
        "keywords": keywords,
        "locations": locations,
        "hours_old": int(search.get("hours_old") or 72),
        "min_score": int((config.get("assessment") or {}).get("min_score", 60)),
    }


def apply_settings(config: dict, settings: dict) -> dict:
    """Return a copy of config with the website settings overlaid."""
    merged = copy.deepcopy(config)
    search = merged.setdefault("search", {})
    search["keywords"] = list(settings["keywords"])
    search["locations"] = list(settings["locations"])
    search["hours_old"] = int(settings["hours_old"])
    merged.setdefault("assessment", {})["min_score"] = int(settings["min_score"])
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="Local profile config.yaml")
    parser.add_argument("output", help="Where to write the effective config")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--profile", choices=("owner", "julia"), default="owner")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text()) or {}
    effective = config
    try:
        key = keychain_key()
        url = with_query(args.url, profile=args.profile)
        settings = request_settings(url, key).get("settings")
        if settings:
            effective = apply_settings(config, settings)
            print(f"Using website search settings: {len(settings['keywords'])} job titles, "
                  f"{len(settings['locations'])} locations.")
        else:
            seed = local_settings(config)
            if seed:
                request_settings(with_query(args.url, profile=args.profile, if_absent=1), key, "PUT", {"settings": seed})
                print("No website search settings yet; uploaded the local config's values.")
            else:
                print("No website search settings yet; using the local config.")
    except Exception as exc:  # noqa: BLE001 - the website must never block a run
        print(f"WARNING: {exc}. Using the local config unchanged.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(effective, sort_keys=False, allow_unicode=True))


if __name__ == "__main__":
    main()
