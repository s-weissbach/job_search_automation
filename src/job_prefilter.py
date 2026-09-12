"""Deterministic relevance filtering before any LLM job assessment.

The search providers are intentionally broad and can return hundreds of jobs
that merely mention one query term. This module keeps obvious mismatches away
from the model while retaining an audit trail so the rules can be tuned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "ref", "refid", "source", "src", "trackingid", "trk", "trkinfo",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}

_STRONG_TITLE_PATTERNS = (
    r"\bbioinformatic(?:s|ian)\b",
    r"computational (?:biology|biologist|genomics|medicine)",
    r"\bgenomic(?:s| scientist)\b",
    r"transcriptom",
    r"single[- ]cell",
    r"spatial (?:biology|omics|transcriptom)",
    r"multi[- ]omics",
    r"systems biology",
    r"translational bioinformatic",
    r"data science.*biolog|biolog.*data science",
    r"machine learning.*biolog|biolog.*machine learning",
)

_DOMAIN_PATTERNS = (
    r"\bbioinformatic", r"computational biolog", r"\bgenomic", r"transcriptom",
    r"single[- ]cell", r"spatial (?:biology|omics|transcriptom)", r"multi[- ]omics",
    r"\bomics\b", r"rna[- ]?seq|rna sequencing", r"next[- ]generation sequencing|\bngs\b",
    r"gene expression", r"molecular data", r"biomarker discovery", r"drug discovery",
    r"precision medicine",
)

_SUPPORT_PATTERNS = (
    r"\bpython\b", r"(?<![a-z])r(?![a-z])", r"scanpy", r"seurat", r"nextflow",
    r"pytorch", r"machine learning", r"deep learning", r"biomarker",
    r"life sciences?", r"pharma", r"biotech",
)

_GENERIC_TECHNICAL_TITLE_PATTERNS = (
    r"data scientist", r"data engineer", r"machine learning", r"\bml\b", r"\bai\b",
    r"research scientist", r"computational", r"biostatistic",
)

_EXCLUDED_TITLE_PATTERNS = (
    r"\bsales\b", r"account manager", r"business development", r"marketing",
    r"customer (?:success|experience)", r"product manager", r"project manager",
    r"program manager", r"medical director", r"medical advisor", r"physician",
    r"quality (?:control|assurance)", r"\bqc\b", r"regulatory", r"manufactur",
    r"full[- ]?stack", r"front[- ]?end", r"cloud developer", r"\.net developer",
    r"software engineer", r"solutions architect", r"partnership manager",
    r"legal counsel", r"meteorologist", r"field application",
)

_JUNIOR_TITLE_PATTERNS = (
    r"\bintern(?:ship)?\b", r"working student", r"\bstudent\b", r"\bph\.?d\.?\b",
    r"doctoral (?:student|candidate|position)", r"graduate programme", r"\btrainee\b",
    r"\bpost[ -]?doc(?:toral)?\b",
)

_MANAGEMENT_TITLE_PATTERNS = (
    r"\bvice president\b", r"\bvp\b", r"\bchief\b", r"\bhead of\b",
    r"\bdirector\b", r"\bexecutive\b",
)

_NONINDUSTRY_COMPANY_PATTERNS = (
    r"\buniversity\b", r"\buniversit", r"\bhochschule\b", r"\bcollege\b",
    r"\bmax planck\b", r"\bhelmholtz\b", r"\bembl\b", r"\bnhs\b",
    r"\bpublic health england\b", r"\bgovernment\b", r"\bcancer research uk\b",
)


def _matches(patterns: tuple[str, ...], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def _extra_patterns(config: dict, key: str) -> tuple[str, ...]:
    return tuple(str(value) for value in (config.get(key) or []) if str(value).strip())


def canonical_job_url(value: object) -> str:
    """Normalize tracking noise without removing query keys that identify jobs."""
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        query = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_QUERY_KEYS and not key.casefold().startswith("utm_")
        ]
        return urlunsplit(
            (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(sorted(query)), "")
        )
    except ValueError:
        return url


def normalized_identity(title: object, company: object, location: object) -> str:
    def clean(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return "|".join((clean(title), clean(company), clean(location)))


@dataclass(frozen=True)
class PrefilterDecision:
    accepted: bool
    score: int
    reason: str
    signals: tuple[str, ...]


def evaluate_job(job: dict, known_company_sector: str | None, config: dict) -> PrefilterDecision:
    """Return a conservative, explainable relevance decision for one job."""
    if not config.get("enabled", True):
        return PrefilterDecision(True, 0, "prefilter_disabled", ())

    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    description = str(job.get("description") or "")
    max_chars = int(config.get("max_description_chars", 8000))
    text = f"{title}\n{description[:max_chars]}"

    if config.get("exclude_known_nonindustry", True) and known_company_sector in {
        "academia", "government", "nonprofit"
    }:
        return PrefilterDecision(False, 0, f"known_{known_company_sector}", ())

    nonindustry_patterns = _NONINDUSTRY_COMPANY_PATTERNS + _extra_patterns(
        config, "extra_nonindustry_company_patterns"
    )
    if config.get("exclude_obvious_nonindustry", True) and _matches(nonindustry_patterns, company):
        return PrefilterDecision(False, 0, "obvious_nonindustry_employer", ())

    excluded_patterns = _EXCLUDED_TITLE_PATTERNS + _extra_patterns(config, "extra_excluded_title_patterns")
    if excluded := _matches(excluded_patterns, title):
        return PrefilterDecision(False, 0, "excluded_title", tuple(excluded))

    if config.get("exclude_junior_roles", True):
        if junior := _matches(_JUNIOR_TITLE_PATTERNS, title):
            return PrefilterDecision(False, 0, "too_junior", tuple(junior))

    if config.get("exclude_management_roles", True):
        if management := _matches(_MANAGEMENT_TITLE_PATTERNS, title):
            return PrefilterDecision(False, 0, "too_senior_management", tuple(management))

    strong_patterns = _STRONG_TITLE_PATTERNS + _extra_patterns(config, "extra_strong_title_patterns")
    domain_patterns = _DOMAIN_PATTERNS + _extra_patterns(config, "extra_domain_patterns")
    strong_hits = _matches(strong_patterns, title)
    domain_hits = _matches(domain_patterns, text)
    support_hits = _matches(_SUPPORT_PATTERNS, text)
    generic_title = bool(_matches(_GENERIC_TECHNICAL_TITLE_PATTERNS, title))

    score = min(len(strong_hits), 2) * 7
    score += min(len(domain_hits), 6) * 2
    score += min(len(support_hits), 6)
    if strong_hits:
        score += 2

    if not strong_hits and len(domain_hits) < 2:
        reason = "generic_without_domain_evidence" if generic_title else "weak_domain_evidence"
        return PrefilterDecision(False, score, reason, tuple(domain_hits + support_hits))

    minimum = int(config.get("min_relevance_score", 7))
    if score < minimum:
        return PrefilterDecision(False, score, "below_relevance_threshold", tuple(domain_hits + support_hits))

    signals = tuple(strong_hits + domain_hits + support_hits)
    return PrefilterDecision(True, score, "candidate", signals)
