"""Shared date normalization for scraped job records."""

from __future__ import annotations

import math


def posting_date_or_scrape_date(value: object, scrape_date: str) -> str:
    """Use the source posting date when present, otherwise the scrape date."""
    if value is None:
        return scrape_date
    if isinstance(value, float) and math.isnan(value):
        return scrape_date
    normalized = str(value).strip()
    if normalized.casefold() in {"", "nan", "nat", "none"}:
        return scrape_date
    return normalized[:10]
