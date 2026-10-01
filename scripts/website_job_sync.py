#!/usr/bin/env python3
"""Synchronise the local job score store through the owner-only website API."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


DEFAULT_URL = "https://stephanweissbach.dev/api/jobs"


def keychain_key() -> str:
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-w", "-a", "private-ai", "-s", "stephanweissbach.dev-focus-api"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    key = result.stdout.strip()
    if result.returncode or not key:
        raise RuntimeError("Perry's website credential is unavailable in macOS Keychain.")
    return key


def request_json(url: str, key: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method="POST" if data is not None else "GET",
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=90) as response:
            return json.loads(response.read(20_000_000))
    except HTTPError as exc:
        detail = exc.read(2000).decode(errors="replace")
        raise RuntimeError(f"Website API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"Website API request failed: {exc}") from exc


def download(url: str, key: str, output: Path) -> int:
    jobs: list[dict] = []
    page = 0
    while True:
        payload = request_json(f"{url}?{urlencode({'sync': '1', 'page': page})}", key)
        batch = payload.get("jobs") or []
        if not isinstance(batch, list):
            raise RuntimeError("Website API returned an invalid jobs payload.")
        jobs.extend(batch)
        if not payload.get("hasMore"):
            break
        page += 1
    if jobs:
        frame = pd.DataFrame(jobs).drop(columns=["created_at"], errors="ignore")
        output.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output, index=False)
    print(f"Downloaded {len(jobs)} website job records to {output}.")
    return len(jobs)


def json_records(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    records = json.loads(pd.read_csv(path).to_json(orient="records"))
    for record in records:
        for key, value in list(record.items()):
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                record[key] = None
    return records


def upload(url: str, key: str, source: Path) -> int:
    records = json_records(source)
    sent = 0
    for start in range(0, len(records), 200):
        batch = records[start:start + 200]
        response = request_json(url, key, {"jobs": batch})
        if not response.get("ok"):
            raise RuntimeError("Website API did not confirm the job import.")
        sent += len(batch)
    print(f"Uploaded {sent} job records to the website.")
    return sent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["download", "upload"])
    parser.add_argument("path")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    key = keychain_key()
    if args.action == "download":
        download(args.url, key, Path(args.path))
    else:
        upload(args.url, key, Path(args.path))


if __name__ == "__main__":
    main()
