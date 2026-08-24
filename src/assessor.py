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

# Message Batches API: flat 50% off standard per-token rates, applied uniformly
# to input and output tokens.
#
# NOTE: this does NOT compose with prompt caching the way it might seem to.
# Anthropic's docs and our own smoke test agree: batch requests execute in
# parallel with no ordering guarantee, and "a cache entry only becomes
# available after the first response begins" — a guarantee batch processing
# can't make. In practice cache_read/cache_creation stay at 0 for batch
# requests regardless of how many share an identical prefix. The system
# prompt below still carries `cache_control` and is still padded past
# Haiku's 2048-token cache floor (harmless, and it'd matter if this ever
# falls back to a synchronous call), but budget for the 50% batch discount
# alone — not batch-plus-caching.
_BATCH_DISCOUNT = 0.5

_DEFAULT_INDUSTRY_MALUS = 15

# Batches are near-always done in minutes for a few hundred requests, but
# Anthropic gives no hard SLA short of the 24h expiry. Poll with a bounded
# wait so a stuck batch fails loudly instead of hanging a scheduled run.
_BATCH_POLL_SECONDS = 20
_BATCH_MAX_WAIT_SECONDS = 50 * 60

# Worked calibration examples appended to the system prompt, mainly to anchor
# score/seniority/sector judgment with concrete cases (see the smoke test:
# they produced sensible too_senior/too_junior/malus behavior on synthetic
# postings). Also pushes the system block over Haiku's 2048-token prompt-
# caching floor — which turns out not to matter under the Batches API (see
# _BATCH_DISCOUNT above), but costs nothing to leave in place.
_FEW_SHOT_EXAMPLES = """
WORKED EXAMPLES (calibration only — judge every posting on its own merits
against the candidate profile above; do not copy these numbers verbatim):

Example A — strong industry match:
Posting: "Senior Scientist, Computational Biology" at a mid-size biotech.
Requires a PhD in bioinformatics or a related field, 4+ years applying
machine learning to single-cell or spatial omics data, proficiency in
Python and one of Seurat/Scanpy, and experience presenting findings to
non-technical stakeholders. If the candidate profile shows a matching
degree, comparable years of experience, and overlapping technical tools,
this is a strong match: score around 80-85, job_sector "industry",
seniority_match "match". Reasoning should name the specific overlapping
skills and tools rather than restate the posting. matching_skills lists
the concrete overlaps (e.g. ["single-cell analysis", "Python", "Scanpy"]);
concerns stays empty or names one minor gap (e.g. a named tool not present
in the profile).

Example B — seniority mismatch (too senior):
Posting: "Director, Translational Bioinformatics" at a pharma company.
Requires 10+ years of experience, direct line management of a 6-person
team, and budget ownership for a departmental analytics function. Even
with strong technical overlap, if the candidate profile shows no
people-management or budget-ownership experience at that scale, this is
too senior: reduce the raw technical-fit score by at least 10 points
(e.g. a 75 technical baseline becomes ~60-65), job_sector "industry",
seniority_match "too_senior". Reasoning must explicitly name the
management-scope gap; concerns must include a clear seniority concern
(e.g. "role requires direct people management not shown in the profile").

Example C — seniority mismatch (too junior):
Posting: "PhD Student, Computational Genomics" at a university research
group. Entry-level, no independent publication record required, focused
on coursework alongside supervised research. For a candidate profile
showing postdoctoral-level or industry experience already, this is too
junior regardless of topical overlap: reduce the score by at least 20
points, job_sector "academia", seniority_match "too_junior". Reasoning
notes the level gap explicitly; concerns names the seniority mismatch.

Example D — sector labeling only, no seniority concern:
Posting: "Bioinformatics Analyst" at a national public-health institute
funded by government appropriations, not a university. This is
job_sector "government", not "academia" — academia is specifically
universities and research institutes, government is public agencies and
national labs. Score purely on technical fit as usual; sector is recorded
for filtering, not penalized in the score itself.

Example E — vague or sparse posting:
Posting: a two-line listing with only a job title ("Data Scientist") and
a company name, no description, no seniority signals, no explicit
requirements. Do not assume seniority or over-penalize for missing
information. Score on the limited technical signal available (job title
and any domain keywords), set seniority_match "unclear" rather than
guessing "too_junior" or "too_senior", and use the reasoning field to
note explicitly that the posting had little information to judge on
(e.g. "Limited posting detail; scored on title and domain overlap only").
concerns may include "posting lacks detail" rather than a fabricated
technical gap.

REASONING STYLE: keep reasoning grounded in specifics from the posting
and the candidate profile — name the actual overlapping tools, domains,
or experience rather than generic phrases like "good fit" or "relevant
background". A reader should be able to tell from the reasoning alone
which parts of the posting drove the score up or down.
""".strip()


class JobAssessor:
    def __init__(self, client: anthropic.Anthropic, cv_text: str, config: dict):
        self.client = client
        self.model = config.get("model", "claude-haiku-4-5")
        self.max_desc_chars = config.get("max_description_chars", 4000)
        self.max_input_tokens = config.get("max_input_tokens")
        self.industry_malus = config.get("industry_malus", _DEFAULT_INDUSTRY_MALUS)
        self.sector_blacklist = set(config.get("sector_blacklist") or [])
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_tokens_read = 0
        self._cache_tokens_written = 0
        self._skipped = 0
        self._blacklist_skipped = 0
        self._batch_failed = 0

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
                    f"CANDIDATE PROFILE:\n{cv_text}\n\n"
                    f"{_FEW_SHOT_EXAMPLES}"
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

    def _request_params(self, message: str) -> dict:
        return {
            "model": self.model,
            "max_tokens": 512,
            "system": self._system,
            "messages": [{"role": "user", "content": message}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _JOB_SCHEMA
                }
            }
        }

    def _parse_batch_result(self, result) -> dict:
        """Turn one MessageBatchIndividualResponse.result into an assessment dict,
        accounting tokens along the way. Never raises — batch failures degrade to
        a zero-score result with the failure reason in `reasoning`, same as a
        single-call exception did before."""
        if result.type != "succeeded":
            self._batch_failed += 1
            return {
                "score": 0,
                "job_sector": "other",
                "seniority_match": "unclear",
                "reasoning": f"Assessment error: batch request {result.type}.",
                "matching_skills": [],
                "concerns": [],
            }

        message = result.message
        usage = message.usage
        self._input_tokens += usage.input_tokens or 0
        self._output_tokens += usage.output_tokens or 0
        self._cache_tokens_read += usage.cache_read_input_tokens or 0
        self._cache_tokens_written += usage.cache_creation_input_tokens or 0

        try:
            text = next(b.text for b in message.content if b.type == "text")
            return json.loads(text)
        except Exception as e:
            self._batch_failed += 1
            return {
                "score": 0,
                "job_sector": "other",
                "seniority_match": "unclear",
                "reasoning": f"Assessment error: {e}",
                "matching_skills": [],
                "concerns": []
            }

    def _run_batch(self, requests: list[dict]) -> dict[str, dict]:
        """Submit a Message Batch and block until every request in it has ended,
        then return custom_id -> parsed assessment dict."""
        batch = self.client.messages.batches.create(requests=requests)
        print(f"  Submitted batch {batch.id} ({len(requests)} jobs) — polling...")

        waited = 0
        while batch.processing_status != "ended":
            if waited >= _BATCH_MAX_WAIT_SECONDS:
                raise RuntimeError(
                    f"Batch {batch.id} did not finish within {_BATCH_MAX_WAIT_SECONDS // 60} min "
                    f"(status: {batch.processing_status}, counts: {batch.request_counts})"
                )
            time.sleep(_BATCH_POLL_SECONDS)
            waited += _BATCH_POLL_SECONDS
            batch = self.client.messages.batches.retrieve(batch.id)
            rc = batch.request_counts
            print(
                f"    ...{waited}s elapsed — processing:{rc.processing} "
                f"succeeded:{rc.succeeded} errored:{rc.errored} "
                f"canceled:{rc.canceled} expired:{rc.expired}"
            )

        return {
            line.custom_id: self._parse_batch_result(line.result)
            for line in self.client.messages.batches.results(batch.id)
        }

    def _apply_malus(self, raw_score: int, sector: str) -> int:
        if raw_score < 0:
            return raw_score
        if sector != "industry":
            return max(0, raw_score - self.industry_malus)
        return raw_score

    def assess_all(self, df: pd.DataFrame, cache_path: str | None = None) -> pd.DataFrame:
        cached = {}
        company_sector: dict[str, str] = {}
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
                        if self.sector_blacklist:
                            crow_company = crow.get("company")
                            crow_sector = crow.get("job_sector")
                            if pd.notna(crow_company) and pd.notna(crow_sector) and str(crow_company).strip():
                                company_sector[str(crow_company).strip().lower()] = str(crow_sector)
                    if cached:
                        print(f"  Score store: {len(cached)} previously assessed jobs loaded")
                except Exception:
                    pass

        cache_written = Path(cache_path).exists() if cache_path else False
        existing_cols = None
        if cache_written and cache_path:
            try:
                existing_cols = pd.read_csv(cache_path, nrows=0).columns.tolist()
            except Exception:
                pass

        n = len(df)
        scores: list = [None] * n
        sectors: list = [None] * n
        seniority_matches: list = [None] * n
        reasonings: list = [None] * n
        skills_list: list = [None] * n
        concerns_list: list = [None] * n
        cache_hits = 0

        def record(idx: int, result: dict, row: pd.Series, url: str) -> int:
            nonlocal cache_written
            raw_score = result["score"]
            sector = result.get("job_sector", "other")
            adjusted_score = self._apply_malus(raw_score, sector)
            joined_skills = "; ".join(result.get("matching_skills", []))
            joined_concerns = "; ".join(result.get("concerns", []))
            seniority = result.get("seniority_match", "unclear")

            scores[idx] = adjusted_score
            sectors[idx] = sector
            seniority_matches[idx] = seniority
            reasonings[idx] = result["reasoning"]
            skills_list[idx] = joined_skills
            concerns_list[idx] = joined_concerns

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
                if existing_cols is not None:
                    cache_row = cache_row.reindex(columns=existing_cols)
                cache_row.to_csv(cache_path, mode="a", header=not cache_written, index=False)
                cache_written = True
            return adjusted_score

        pending: list[tuple[int, int, pd.Series, str, str, str, str]] = []

        for idx, (_, row) in enumerate(df.iterrows()):
            i = idx + 1
            raw_title, raw_company = row.get("title"), row.get("company")
            title = (raw_title if pd.notna(raw_title) and raw_title else "Unknown")[:50]
            company = (raw_company if pd.notna(raw_company) and raw_company else "Unknown")[:30]
            url = str(row.get("job_url", ""))

            if url and url in cached:
                r = cached[url]
                scores[idx] = r["score"]
                sectors[idx] = r.get("job_sector", "other")
                seniority_matches[idx] = r.get("seniority_match", "unclear")
                reasonings[idx] = r["reasoning"]
                skills_list[idx] = r["matching_skills"]
                concerns_list[idx] = r["concerns"]
                label = f"score: {r['score']}% (cached)" if r["score"] != -1 else "skipped (cached)"
                print(f"  [{i:>3}/{n}] {title} @ {company}... {label}")
                cache_hits += 1
                continue

            company_key = company.strip().lower() if company != "Unknown" else None
            blacklisted_sector = company_sector.get(company_key) if company_key else None

            if blacklisted_sector in self.sector_blacklist:
                print(f"  [{i:>3}/{n}] {title} @ {company}... skipped (blacklisted: {blacklisted_sector})")
                self._blacklist_skipped += 1
                result = {
                    "score": -1,
                    "job_sector": blacklisted_sector,
                    "seniority_match": "unclear",
                    "reasoning": f"Skipped: company sector blacklisted ({blacklisted_sector}).",
                    "matching_skills": [],
                    "concerns": [],
                }
                record(idx, result, row, url)
                continue

            message = self._build_message(row.to_dict())

            if self.max_input_tokens is not None:
                token_count = self._count_tokens(message)
                if token_count > self.max_input_tokens:
                    print(f"  [{i:>3}/{n}] {title} @ {company}... skipped ({token_count:,} tokens > limit {self.max_input_tokens:,})")
                    self._skipped += 1
                    record(idx, _SKIP_RESULT, row, url)
                    continue

            print(f"  [{i:>3}/{n}] {title} @ {company}... queued for batch")
            pending.append((idx, i, row, url, title, company, message))

        if cache_hits:
            print(f"  Score store: {cache_hits} jobs reused from previous runs (0 tokens)")

        if pending:
            print(f"\n  {len(pending)} new jobs to assess — submitting as one Message Batch...")
            requests = [
                {"custom_id": f"job-{idx}", "params": self._request_params(message)}
                for idx, _i, _row, _url, _title, _company, message in pending
            ]
            batch_results = self._run_batch(requests)

            print()
            for idx, i, row, url, title, company, _message in pending:
                result = batch_results.get(f"job-{idx}")
                if result is None:
                    self._batch_failed += 1
                    result = {
                        "score": 0,
                        "job_sector": "other",
                        "seniority_match": "unclear",
                        "reasoning": "Assessment error: no result returned for this request.",
                        "matching_skills": [],
                        "concerns": [],
                    }
                raw_score = result["score"]
                adjusted_score = record(idx, result, row, url)

                if adjusted_score != -1:
                    sector = sectors[idx]
                    malus_note = (
                        f" (-{self.industry_malus} {sector})"
                        if sector != "industry" and raw_score != adjusted_score
                        else ""
                    )
                    seniority_label = f" [{seniority_matches[idx]}]" if seniority_matches[idx] != "match" else ""
                    print(f"  [{i:>3}/{n}] {title} @ {company}... score: {adjusted_score}%{malus_note}{seniority_label}")
                else:
                    print(f"  [{i:>3}/{n}] {title} @ {company}... skipped")

        if self._skipped:
            print(f"  Skipped {self._skipped} jobs (exceeded max_input_tokens)")
        if self._blacklist_skipped:
            print(f"  Skipped {self._blacklist_skipped} jobs (sector blacklist)")
        if self._batch_failed:
            print(f"  {self._batch_failed} jobs failed in the batch (errored/expired/canceled) — scored 0, see reasoning column")

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
            f"Token usage ({self.model}, Batches API — {int(_BATCH_DISCOUNT * 100)}% off standard rates):",
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
            ) * _BATCH_DISCOUNT
            lines.append(f"  Estimated cost: ~${cost:.4f} USD")
        else:
            lines.append(f"  Estimated cost: unknown model '{self.model}' — add to _PRICING")
        return "\n".join(lines)
