"""Repair exact decimal/k-expansion errors against the disclosed source phrase."""

import math
import re


_AMOUNT = re.compile(r"(?<![\w])\d+(?:[.,'’]\d+)*(?:[ \u00a0\u202f]\d{3})*(?:\s*[kK](?!\w))?")
_INSTALLMENTS = re.compile(r"\b(1[2-4])\s*(?:mal|x)\s*(?:jährlich|im jahr|pro jahr)\b", re.I)


def disclosed_amounts(text: str) -> list[float]:
    amounts = []
    for match in _AMOUNT.finditer(text):
        token = re.sub(r"[\s'’]", "", match.group())
        scale = 1000 if token.lower().endswith("k") else 1
        token = token.rstrip("kK")
        # A one/two-digit final group is decimal cents; three-digit groups
        # are thousands. This covers 3.463,06, 3,463.06, and 931,700.00.
        groups = re.split(r"[.,]", token)
        if len(groups) > 1 and len(groups[-1]) in (1, 2):
            number = "".join(groups[:-1]) + "." + groups[-1]
        else:
            number = "".join(groups)
        value = float(number) * scale
        if value > 0:
            amounts.append(value)
    return amounts


def normalize_salary_fields(row: dict) -> dict:
    """Correct only exact 100x/1000x errors with one matching source amount.

    Never infer salary from the role or silently discard an outlier. The
    source text and currency stay unchanged for auditability. Explicit monthly
    pay with 12–14 stated yearly installments is stored as its exact yearly sum.
    """
    normalized = dict(row)
    amounts = disclosed_amounts(str(row.get("salary_text") or ""))
    for field in ("salary_min", "salary_max"):
        raw = row.get(field)
        if not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw <= 0:
            continue
        if any(math.isclose(raw, amount, rel_tol=1e-9) for amount in amounts):
            continue
        matches = {amount for amount in amounts
                   if any(math.isclose(raw, amount * scale, rel_tol=1e-9) for scale in (100, 1000))}
        if len(matches) == 1:
            normalized[field] = matches.pop()
    text = str(row.get("salary_text") or "")
    installments = _INSTALLMENTS.search(text)
    if installments and re.search(r"\b(?:monatlich|pro monat)\b", text, re.I):
        values = [normalized.get(field) for field in ("salary_min", "salary_max")]
        present = [value for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
        # Only annualize values confirmed as the actual monthly disclosure;
        # an already annualized import must stay unchanged on the next pass.
        if present and all(any(math.isclose(value, amount, rel_tol=1e-9) for amount in amounts) for value in present):
            for field in ("salary_min", "salary_max"):
                if normalized.get(field) is not None:
                    normalized[field] = round(normalized[field] * int(installments.group(1)), 2)
            normalized["salary_period"] = "year"
    return normalized
