"""Recall-first deterministic filtering before any LLM job assessment.

The search providers are intentionally broad and can return hundreds of jobs
that merely mention one query term. This module rejects only obvious mismatches,
while sending both clear and plausible-borderline roles to the model.
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

_ALWAYS_EXCLUDED_TITLE_PATTERNS = (
    r"\bsales\b", r"account manager", r"business development", r"marketing",
    r"customer (?:success|experience)", r"product manager", r"project manager",
    r"program manager", r"medical director", r"medical advisor", r"physician",
    r"chief medical officer", r"\bcmo\b", r"\bprofessor(?:ship)?\b",
    r"quality (?:control|assurance)", r"\bqc\b", r"regulatory", r"manufactur",
    r"partnership manager", r"legal counsel", r"meteorologist", r"field application",
)

_CONDITIONAL_TECHNICAL_TITLE_PATTERNS = (
    r"full[- ]?stack", r"front[- ]?end", r"cloud developer", r"\.net developer",
    r"software engineer", r"solutions architect",
)

_SCIENTIFIC_OR_ANALYTICAL_TITLE_PATTERNS = (
    r"scientist", r"research", r"engineer", r"analyst", r"architect",
    r"statistic", r"computational", r"informatics", r"data", r"\bai\b", r"\bml\b",
)

_JUNIOR_TITLE_PATTERNS = (
    r"\bintern(?:ship)?\b", r"working student", r"\bstudent\b", r"\bph\.?d\.?\b",
    r"doctoral (?:student|candidate|position)", r"graduate programme", r"\btrainee\b",
    r"\bpost[ -]?doc(?:toral)?\b", r"\bpostdoktor", r"\bdoktorand",
    r"\bpromotion\b", r"industrial placement",
)

_MANAGEMENT_TITLE_PATTERNS = (
    r"\bvice president\b", r"\bvp\b", r"\bchief\b", r"\bhead of\b",
    r"\bdirector\b", r"\bexecutive\b",
)

_SENIOR_IC_TITLE_PATTERNS = (
    r"\bstaff\b", r"\bprincipal\b", r"\btechnical lead\b", r"\bteam lead\b",
)

_PEOPLE_LEADERSHIP_PATTERNS = (
    r"direct reports?", r"people management", r"engineering manager",
    r"manag(?:e|ing) (?:an? |the )?(?:engineering|scientific|data|machine learning|ai/ml)? ?team",
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


def normalized_role_identity(title: object, company: object) -> str:
    """Stable role identity used to suppress cross-location reposts."""
    def clean(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    title_key, company_key = clean(title), clean(company)
    return f"{title_key}|{company_key}" if title_key and company_key else ""


@dataclass(frozen=True)
class PrefilterDecision:
    accepted: bool
    score: int
    reason: str
    signals: tuple[str, ...]
    tier: str = "rejected"


def evaluate_job(job: dict, known_company_sector: str | None, config: dict) -> PrefilterDecision:
    """Return an explainable, recall-first relevance decision for one job."""
    if not config.get("enabled", True):
        return PrefilterDecision(True, 0, "prefilter_disabled", (), "strong")

    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    description = str(job.get("description") or "")
    max_chars = int(config.get("max_description_chars", 8000))
    text = f"{title}\n{description[:max_chars]}"

    nonindustry_patterns = _NONINDUSTRY_COMPANY_PATTERNS + _extra_patterns(
        config, "extra_nonindustry_company_patterns"
    )

    strong_patterns = _STRONG_TITLE_PATTERNS + _extra_patterns(config, "extra_strong_title_patterns")
    domain_patterns = _DOMAIN_PATTERNS + _extra_patterns(config, "extra_domain_patterns")
    strong_hits = _matches(strong_patterns, title)
    domain_hits = _matches(domain_patterns, text)
    support_hits = _matches(_SUPPORT_PATTERNS, text)
    generic_title = bool(_matches(_GENERIC_TECHNICAL_TITLE_PATTERNS, title))
    scientific_title = bool(_matches(_SCIENTIFIC_OR_ANALYTICAL_TITLE_PATTERNS, title))
    conditional_technical = _matches(_CONDITIONAL_TECHNICAL_TITLE_PATTERNS, title)
    management = _matches(_MANAGEMENT_TITLE_PATTERNS, title)
    obvious_nonindustry = _matches(nonindustry_patterns, company)
    known_nonindustry = known_company_sector in {"academia", "government", "nonprofit"}

    score = min(len(strong_hits), 2) * 7
    score += min(len(domain_hits), 6) * 2
    score += min(len(support_hits), 6)
    if strong_hits:
        score += 2

    context_signals: list[str] = []
    if known_nonindustry:
        context_signals.append(f"sector:{known_company_sector}")
    elif obvious_nonindustry:
        context_signals.append("sector:likely_nonindustry")
    signals = tuple(strong_hits + domain_hits + support_hits + context_signals)

    excluded_patterns = _ALWAYS_EXCLUDED_TITLE_PATTERNS + _extra_patterns(
        config, "extra_excluded_title_patterns"
    )
    if excluded := _matches(excluded_patterns, title):
        return PrefilterDecision(False, score, "definite_title_mismatch", tuple(excluded) + signals)

    if config.get("exclude_junior_roles", True):
        if junior := _matches(_JUNIOR_TITLE_PATTERNS, title):
            return PrefilterDecision(False, score, "too_junior", tuple(junior) + signals)

    if config.get("exclude_known_nonindustry", False) and known_nonindustry:
        return PrefilterDecision(False, score, f"known_{known_company_sector}", signals)

    if config.get("exclude_obvious_nonindustry", False) and obvious_nonindustry:
        return PrefilterDecision(False, score, "obvious_nonindustry_employer", signals)

    if config.get("exclude_management_roles", False) and management:
        return PrefilterDecision(False, score, "too_senior_management", tuple(management) + signals)

    years = [int(value) for value in re.findall(r"\b(\d{1,2})\s*\+?\s*years", description, re.IGNORECASE)]
    senior_title = _matches(_SENIOR_IC_TITLE_PATTERNS, title)
    leadership = _matches(_PEOPLE_LEADERSHIP_PATTERNS, description)
    explicit_seniority_gap = bool(
        senior_title and leadership and any(value >= 5 for value in years)
    )
    if config.get("exclude_explicit_seniority_mismatches", False) and explicit_seniority_gap:
        gap_signals = tuple(senior_title + leadership + [f"{max(years)}+ years"])
        return PrefilterDecision(False, score, "too_senior_requirements", gap_signals + signals)

    if not strong_hits and not domain_hits:
        if conditional_technical:
            reason = "unrelated_technical_title"
        elif management:
            reason = "management_without_domain_evidence"
        else:
            reason = "generic_without_biological_evidence" if generic_title else "weak_domain_evidence"
        return PrefilterDecision(False, score, reason, signals)

    minimum = int(config.get("min_relevance_score", 7))
    is_strong = bool(strong_hits) or (len(domain_hits) >= 2 and score >= minimum)
    needs_model_judgment = bool(conditional_technical or management or explicit_seniority_gap)

    if is_strong and not needs_model_judgment:
        return PrefilterDecision(True, score, "strong_candidate", signals, "strong")

    # One explicit biological/domain signal is enough for the recall-first
    # borderline queue when the role is scientific, analytical, or technical.
    if domain_hits and (scientific_title or support_hits or strong_hits):
        reason = "borderline_candidate"
        if conditional_technical:
            reason = "borderline_technical_with_domain"
        elif management:
            reason = "borderline_management_with_domain"
        elif explicit_seniority_gap:
            reason = "borderline_seniority_gap"
        return PrefilterDecision(True, score, reason, signals, "borderline")

    return PrefilterDecision(False, score, "weak_domain_evidence", signals)
