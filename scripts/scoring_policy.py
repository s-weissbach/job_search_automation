"""Deterministic score and seniority rules applied after local model output."""

from __future__ import annotations

import re


_NON_PHD_DEGREE = re.compile(r"\b(?:bachelor(?:'s)?|master(?:'s)?|bsc|msc|m\.sc\.)\b", re.I)
_PHD = re.compile(r"\b(?:ph\.?d\.?|doctorate|doctoral degree)\b", re.I)
_ALTERNATIVE_DEGREES = re.compile(
    r"(?:master(?:'s)?|msc|m\.sc\.).{0,100}(?:or|and/or).{0,100}(?:ph\.?d\.?)"
    r"|(?:ph\.?d\.?|doctorate).{0,100}(?:or|and/or).{0,100}(?:master(?:'s)?|msc|m\.sc\.)",
    re.I | re.S,
)
_JUNIOR_TITLE = re.compile(r"\b(?:research associate|technician|laboratory assistant|graduate|trainee|intern)\b", re.I)


def _julia_is_below_phd_level(job: dict) -> bool:
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    if _JUNIOR_TITLE.search(title):
        return True
    has_non_phd = bool(_NON_PHD_DEGREE.search(description))
    if not has_non_phd:
        return False
    # An MSc/BSc-only qualification or an explicit MSc-or-PhD alternative is
    # not a PhD-level target role under Julia's chosen seniority policy.
    return not _PHD.search(description) or bool(_ALTERNATIVE_DEGREES.search(description))


def apply_scoring_policy(job: dict, assessment: dict, policy: str | None = None) -> dict:
    """Return a normalized copy whose score follows auditable fixed rules."""
    normalized = dict(assessment)
    if policy == "julia-phd" and _julia_is_below_phd_level(job):
        normalized["seniority_match"] = "too_junior"
        concerns = list(normalized.get("concerns") or [])
        marker = "Qualification accepts a bachelor's/master's degree; below Julia's PhD-level target"
        if marker not in concerns:
            concerns.append(marker)
        normalized["concerns"] = concerns[:6]

    components = normalized.get("score_components")
    if isinstance(components, dict) and components and all(isinstance(value, int) for value in components.values()):
        # This component is factual rather than interpretive: pharma/biotech
        # R&D receives the rubric's full five points.
        if normalized.get("job_sector") == "industry" and "industry_fit" in components:
            components = {**components, "industry_fit": 5}
            normalized["score_components"] = components
        score = sum(components.values())
    else:
        score = int(normalized.get("score", 0))

    seniority = normalized.get("seniority_match")
    if seniority == "too_junior":
        score = min(score, 59)
    elif seniority == "too_senior":
        score = min(score, 54)
    normalized["score"] = max(0, min(100, score))
    return normalized
