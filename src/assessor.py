import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_BASEL_TZ = ZoneInfo("Europe/Zurich")

import pandas as pd
import anthropic

_JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": (
                "Fit score 0-100 (percentage; 100 = perfect match). "
                "Use the full range — strong match: 75-90, excellent: 90-100, "
                "average: 50-70, weak: 20-45, very poor: 0-20."
            )
        },
        "job_sector": {
            "type": "string",
            "enum": ["industry", "academia", "government", "nonprofit", "other"],
            "description": (
                "'industry' = private companies, corporations, pharma, biotech, tech; "
                "'academia' = universities, research institutes (e.g. Max Planck, Helmholtz, EMBL, NIH intramural); "
                "'government' = public sector agencies, national labs with government funding; "
                "'nonprofit' = NGOs, foundations, patient advocacy orgs; "
                "'other' = unclear or mixed."
            )
        },
        "seniority_match": {
            "type": "string",
            "enum": ["too_junior", "match", "too_senior", "unclear"],
            "description": (
                "Whether the posted seniority level matches the candidate. "
                "'too_junior' = intern/entry-level/junior roles (candidate is overqualified). "
                "'too_senior' = director/VP/head-of roles requiring management experience the candidate lacks. "
                "'match' = scientist/senior scientist/principal/staff/lead/independent contributor roles. "
                "'unclear' = no seniority signals in the posting."
            )
        },
        "reasoning": {
            "type": "string",
            "description": "2-3 sentence assessment of fit, explicitly noting seniority level if it is a concern"
        },
        "matching_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skills and experiences that match the role"
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Gaps or potential mismatches. Always include a seniority concern when seniority_match is not 'match'."
        }
    },
    "required": ["score", "job_sector", "seniority_match", "reasoning", "matching_skills", "concerns"],
    "additionalProperties": False
}

_SKIP_RESULT = {
    "score": -1,
    "job_sector": "other",
    "seniority_match": "unclear",
    "reasoning": "Skipped: input token limit exceeded.",
    "matching_skills": [],
    "concerns": []
}

_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-4-5":  (0.80,  4.00, 1.00, 0.08),
    "claude-haiku-3":    (0.25,  1.25, 0.30, 0.03),
    "claude-sonnet-4-5": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-opus-4-5":   (15.0, 75.00, 18.75, 1.50),
}

_DEFAULT_INDUSTRY_MALUS = 15


class JobAssessor:
    def __init__(self, client: anthropic.Anthropic, cv_text: str, config: dict):
        self.client = client
        self.model = config.get("model", "claude-haiku-4-5")
        self.max_desc_chars = config.get("max_description_chars", 4000)
        self.max_input_tokens = config.get("max_input_tokens")
        self.industry_malus = config.get("industry_malus", _DEFAULT_INDUSTRY_MALUS)
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_tokens_read = 0
        self._cache_tokens_written = 0
        self._skipped = 0

        self._system = [
            {
                "type": "text",
                "text": (
                    "You assess job postings for candidate fit. "
                    "Score 0-100 (percentage) based on: technical skill overlap, domain expertise alignment, "
                    "seniority level match, and role type fit. Use the full 0-100 range — "
                    "don't cluster scores; a strong match should be 75-90, an excellent fit 90-100, "
                    "an average fit 50-65, a weak fit 20-40. Be concise and specific.\n\n"
                    "SENIORITY CHECK (mandatory):\n"
                    "The candidate's seniority is described under the 'seniority' key in the CANDIDATE PROFILE below "
                    "(degree, years of experience, current level, and appropriate titles). "
                    "Use this to judge level fit:\n"
                    "- If the posting targets interns, trainees, entry-level, or junior candidates clearly below "
                    "the candidate's level: set seniority_match='too_junior', reduce score by at least 20 points, "
                    "and list a seniority concern.\n"
                    "- If the posting requires substantial people-management, budget authority, or executive "
                    "leadership clearly beyond the candidate's current level: set seniority_match='too_senior', "
                    "reduce score by at least 10 points, and list a seniority concern.\n"
                    "- If seniority is compatible or no clear signals exist: set seniority_match='match' or 'unclear'.\n\n"
                    "JOB SECTOR: Identify whether the employer is 'industry' (private company/pharma/biotech/tech), "
                    "'academia' (university/research institute), 'government', 'nonprofit', or 'other'. "
                    "Score purely on technical fit — sector preference is not your concern.\n\n"
                    f"CANDIDATE PROFILE:\n{cv_text}"
                ),
                "cache_control": {"type": "ephemeral"}
            }
        ]

    def _build_message(self, job: dict) -> str:
        raw = job.get("description")
        desc = str(raw).strip() if isinstance(raw, str) else ""
        if len(desc) > self.max_desc_chars:
            desc = desc[:self.max_desc_chars] + "..."

        parts = [
            f"Title: {job.get('title') or 'N/A'}",
            f"Company: {job.get('company') or 'N/A'}",
            f"Location: {job.get('location') or 'N/A'}",
        ]
        if job.get("job_type"):
            parts.append(f"Type: {job['job_type']}")
        if desc:
            parts.append(f"\nDescription:\n{desc}")

        return "Assess candidate fit for this job:\n\n" + "\n".join(parts)

    def _count_tokens(self, message: str) -> int:
        result = self.client.messages.count_tokens(
            model=self.model,
            system=self._system,
            messages=[{"role": "user", "content": message}],
        )
        return result.input_tokens

    def _assess_one(self, job: dict) -> dict:
        message = self._build_message(job)

        if self.max_input_tokens is not None:
            token_count = self._count_tokens(message)
            if token_count > self.max_input_tokens:
                print(f"skipped ({token_count:,} tokens > limit {self.max_input_tokens:,})")
                self._skipped += 1
                return _SKIP_RESULT

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=self._system,
                messages=[{"role": "user", "content": message}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": _JOB_SCHEMA
                    }
                }
            )
            self._input_tokens += (response.usage.input_tokens or 0)
            self._output_tokens += (response.usage.output_tokens or 0)
            self._cache_tokens_read += response.usage.cache_read_input_tokens or 0
            self._cache_tokens_written += response.usage.cache_creation_input_tokens or 0

            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text)

        except Exception as e:
            return {
                "score": 0,
                "job_sector": "other",
                "seniority_match": "unclear",
                "reasoning": f"Assessment error: {e}",
                "matching_skills": [],
                "concerns": []
            }

    def _apply_malus(self, raw_score: int, sector: str) -> int:
        if raw_score < 0:
            return raw_score
        if sector != "industry":
            return max(0, raw_score - self.industry_malus)
        return raw_score

    def assess_all(self, df: pd.DataFrame, cache_path: str | None = None) -> pd.DataFrame:
        cached = {}
        if cache_path:
            p = Path(cache_path)
            if p.exists():
                try:
                    cache_df = pd.read_csv(p)
                    for _, crow in cache_df.iterrows():
                        url = crow.get("job_url", "")
                        if url and pd.notna(url):
                            cached[str(url)] = {
                                "score": int(crow["fit_score"]) if pd.notna(crow.get("fit_score")) else 0,
                                "job_sector": str(crow["job_sector"]) if pd.notna(crow.get("job_sector")) else "other",
                                "seniority_match": str(crow["seniority_match"]) if pd.notna(crow.get("seniority_match")) else "unclear",
                                "reasoning": str(crow["fit_reasoning"]) if pd.notna(crow.get("fit_reasoning")) else "",
                                "matching_skills": str(crow["matching_skills"]) if pd.notna(crow.get("matching_skills")) else "",
                                "concerns": str(crow["concerns"]) if pd.notna(crow.get("concerns")) else "",
                            }
                    if cached:
                        print(f"  Score store: {len(cached)} previously assessed jobs loaded")
                except Exception:
                    pass

        cache_written = Path(cache_path).exists() if cache_path else False
        scores, sectors, seniority_matches, reasonings, skills_list, concerns_list = [], [], [], [], [], []
        cache_hits = 0

        for i, (_, row) in enumerate(df.iterrows(), 1):
            title = (row.get("title") or "Unknown")[:50]
            company = (row.get("company") or "Unknown")[:30]
            url = str(row.get("job_url", ""))

            if url and url in cached:
                r = cached[url]
                scores.append(r["score"])
                sectors.append(r.get("job_sector", "other"))
                seniority_matches.append(r.get("seniority_match", "unclear"))
                reasonings.append(r["reasoning"])
                skills_list.append(r["matching_skills"])
                concerns_list.append(r["concerns"])
                label = f"score: {r['score']}% (cached)" if r["score"] != -1 else "skipped (cached)"
                print(f"  [{i:>3}/{len(df)}] {title} @ {company}... {label}")
                cache_hits += 1
                continue

            print(f"  [{i:>3}/{len(df)}] {title} @ {company}", end="... ", flush=True)

            result = self._assess_one(row.to_dict())
            raw_score = result["score"]
            sector = result.get("job_sector", "other")
            adjusted_score = self._apply_malus(raw_score, sector)

            joined_skills = "; ".join(result.get("matching_skills", []))
            joined_concerns = "; ".join(result.get("concerns", []))
            seniority = result.get("seniority_match", "unclear")

            scores.append(adjusted_score)
            sectors.append(sector)
            seniority_matches.append(seniority)
            reasonings.append(result["reasoning"])
            skills_list.append(joined_skills)
            concerns_list.append(joined_concerns)

            if cache_path:
                cache_row = pd.DataFrame([{
                    "job_url": url,
                    "fit_score": adjusted_score,
                    "job_sector": sector,
                    "seniority_match": seniority,
                    "fit_reasoning": result["reasoning"],
                    "matching_skills": joined_skills,
                    "concerns": joined_concerns,
                    "assessed_at": datetime.now(_BASEL_TZ).date().isoformat(),
                    "is_active": "active",
                    "title": row.get("title", ""),
                    "company": row.get("company", ""),
                    "location": row.get("location", ""),
                    "site": row.get("site", ""),
                    "date_posted": row.get("date_posted", ""),
                    "description": row.get("description", ""),
                }])
                cache_row.to_csv(cache_path, mode="a", header=not cache_written, index=False)
                cache_written = True

            if adjusted_score != -1:
                malus_note = (
                    f" (-{self.industry_malus} {sector})"
                    if sector != "industry" and raw_score != adjusted_score
                    else ""
                )
                seniority_label = f" [{seniority}]" if seniority != "match" else ""
                print(f"score: {adjusted_score}%{malus_note}{seniority_label}")
            time.sleep(0.05)

        if cache_hits:
            print(f"  Score store: {cache_hits} jobs reused from previous runs (0 tokens)")
        if self._skipped:
            print(f"  Skipped {self._skipped} jobs (exceeded max_input_tokens)")

        out = df.copy()
        out["fit_score"] = scores
        out["job_sector"] = sectors
        out["seniority_match"] = seniority_matches
        out["fit_reasoning"] = reasonings
        out["matching_skills"] = skills_list
        out["concerns"] = concerns_list
        return out.sort_values("fit_score", ascending=False)

    def usage_summary(self) -> str:
        prices = _PRICING.get(self.model)
        lines = [
            f"Token usage ({self.model}):",
            f"  Input:        {self._input_tokens:>10,}",
            f"  Output:       {self._output_tokens:>10,}",
            f"  Cache write:  {self._cache_tokens_written:>10,}",
            f"  Cache read:   {self._cache_tokens_read:>10,}",
        ]
        if prices:
            p_in, p_out, p_cw, p_cr = (p / 1_000_000 for p in prices)
            cost = (
                self._input_tokens * p_in
                + self._output_tokens * p_out
                + self._cache_tokens_written * p_cw
                + self._cache_tokens_read * p_cr
            )
            lines.append(f"  Estimated cost: ~${cost:.4f} USD")
        else:
            lines.append(f"  Estimated cost: unknown model '{self.model}' — add to _PRICING")
        return "\n".join(lines)
