import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import anthropic

# Prompt caching: the system prompt (CV + instructions) is marked ephemeral.
# After the first job is assessed, subsequent assessments in the same run read
# the CV from cache. Cache activates when the system prefix exceeds the model
# minimum: 2048 tokens for claude-sonnet-4-6, 4096 for claude-haiku-4-5.
# A typical multi-page CV easily exceeds both thresholds.

_JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "Fit score 1-10 (10 = perfect match)"
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
    "required": ["score", "seniority_match", "reasoning", "matching_skills", "concerns"],
    "additionalProperties": False
}

_SKIP_RESULT = {
    "score": -1,
    "seniority_match": "unclear",
    "reasoning": "Skipped: input token limit exceeded.",
    "matching_skills": [],
    "concerns": []
}


# Pricing per million tokens (USD). Update if Anthropic changes rates.
# Rows: (input, output, cache_write, cache_read)
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-4-5":  (0.80,  4.00, 1.00, 0.08),
    "claude-haiku-3":    (0.25,  1.25, 0.30, 0.03),
    "claude-sonnet-4-5": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-opus-4-5":   (15.0, 75.00, 18.75, 1.50),
}


class JobAssessor:
    def __init__(self, client: anthropic.Anthropic, cv_text: str, config: dict):
        self.client = client
        self.model = config.get("model", "claude-haiku-4-5")
        self.max_desc_chars = config.get("max_description_chars", 4000)
        self.max_input_tokens = config.get("max_input_tokens")  # None = no limit
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
                    "Score 1-10 based on: technical skill overlap, domain expertise alignment, "
                    "seniority level match, and role type fit. Be concise and specific.\n\n"
                    "SENIORITY CHECK (mandatory):\n"
                    "The candidate's seniority is described under the 'seniority' key in the CANDIDATE PROFILE below "
                    "(degree, years of experience, current level, and appropriate titles). "
                    "Use this to judge level fit:\n"
                    "- If the posting targets interns, trainees, entry-level, or junior candidates clearly below "
                    "the candidate's level: set seniority_match='too_junior', reduce score by at least 2 points, "
                    "and list a seniority concern.\n"
                    "- If the posting requires substantial people-management, budget authority, or executive "
                    "leadership clearly beyond the candidate's current level: set seniority_match='too_senior', "
                    "reduce score by at least 1 point, and list a seniority concern.\n"
                    "- If seniority is compatible or no clear signals exist: set seniority_match='match' or 'unclear'.\n\n"
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
                "seniority_match": "unclear",
                "reasoning": f"Assessment error: {e}",
                "matching_skills": [],
                "concerns": []
            }

    def assess_all(self, df: pd.DataFrame, cache_path: str | None = None) -> pd.DataFrame:
        # Load incremental cache keyed by job_url
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
                                "seniority_match": str(crow["seniority_match"]) if pd.notna(crow.get("seniority_match")) else "unclear",
                                "reasoning": str(crow["fit_reasoning"]) if pd.notna(crow.get("fit_reasoning")) else "",
                                "matching_skills": str(crow["matching_skills"]) if pd.notna(crow.get("matching_skills")) else "",
                                "concerns": str(crow["concerns"]) if pd.notna(crow.get("concerns")) else "",
                            }
                    if cached:
                        print(f"  Score store: {len(cached)} previously assessed jobs loaded")
                except Exception:
                    pass  # corrupt cache, start fresh

        cache_written = Path(cache_path).exists() if cache_path else False
        scores, seniority_matches, reasonings, skills_list, concerns_list = [], [], [], [], []
        cache_hits = 0

        for i, (_, row) in enumerate(df.iterrows(), 1):
            title = (row.get("title") or "Unknown")[:50]
            company = (row.get("company") or "Unknown")[:30]
            url = str(row.get("job_url", ""))

            if url and url in cached:
                r = cached[url]
                scores.append(r["score"])
                seniority_matches.append(r.get("seniority_match", "unclear"))
                reasonings.append(r["reasoning"])
                skills_list.append(r["matching_skills"])
                concerns_list.append(r["concerns"])
                label = f"score: {r['score']}/10 (cached)" if r["score"] != -1 else "skipped (cached)"
                print(f"  [{i:>3}/{len(df)}] {title} @ {company}... {label}")
                cache_hits += 1
                continue

            print(f"  [{i:>3}/{len(df)}] {title} @ {company}", end="... ", flush=True)

            result = self._assess_one(row.to_dict())
            joined_skills = "; ".join(result.get("matching_skills", []))
            joined_concerns = "; ".join(result.get("concerns", []))
            seniority = result.get("seniority_match", "unclear")

            scores.append(result["score"])
            seniority_matches.append(seniority)
            reasonings.append(result["reasoning"])
            skills_list.append(joined_skills)
            concerns_list.append(joined_concerns)

            if cache_path:
                cache_row = pd.DataFrame([{
                    "job_url": url,
                    "fit_score": result["score"],
                    "seniority_match": seniority,
                    "fit_reasoning": result["reasoning"],
                    "matching_skills": joined_skills,
                    "concerns": joined_concerns,
                    "assessed_at": date.today().isoformat(),
                }])
                cache_row.to_csv(cache_path, mode="a", header=not cache_written, index=False)
                cache_written = True

            if result["score"] != -1:
                seniority_label = f" [{seniority}]" if seniority != "match" else ""
                print(f"score: {result['score']}/10{seniority_label}")
            time.sleep(0.05)

        if cache_hits:
            print(f"  Score store: {cache_hits} jobs reused from previous runs (0 tokens)")
        if self._skipped:
            print(f"  Skipped {self._skipped} jobs (exceeded max_input_tokens)")

        out = df.copy()
        out["fit_score"] = scores
        out["seniority_match"] = seniority_matches
        out["fit_reasoning"] = reasonings
        out["matching_skills"] = skills_list
        out["concerns"] = concerns_list
        # skipped jobs (score -1) go to the bottom
        return out.sort_values("fit_score", ascending=False)

    def usage_summary(self) -> str:
        """Return a formatted string with token counts and estimated cost for this run."""
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
