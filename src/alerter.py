"""Sends a Resend email alert for jobs scoring above a threshold.

Reuses the same Resend account the stephanweissbach.dev site already sends
mail through (RESEND_API_KEY) — no new email provider. Dedup state lives in
a small `job_alerts` Supabase table, kept separate from `job_results` so a
missing/misconfigured table only disables dedup for this step instead of
breaking score persistence.
"""
import os
from datetime import datetime, timezone

import requests

DEFAULT_THRESHOLD = 90
DEFAULT_RECIPIENT = "s.weissbach@outlook.com"
RESEND_API_URL = "https://api.resend.com/emails"


def _esc(s) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def filter_high_score_jobs(jobs_df, threshold: int = DEFAULT_THRESHOLD) -> list[dict]:
    """Jobs from this run's dataframe scoring strictly above `threshold`, highest first."""
    if jobs_df is None or jobs_df.empty or "fit_score" not in jobs_df.columns:
        return []
    candidates = jobs_df[jobs_df["fit_score"] > threshold]
    return candidates.sort_values("fit_score", ascending=False).to_dict("records")


def exclude_already_alerted(jobs: list[dict], already_alerted_urls) -> list[dict]:
    """Drop jobs whose job_url has already had an alert email sent for it."""
    if not already_alerted_urls:
        return list(jobs)
    already = set(already_alerted_urls)
    return [j for j in jobs if str(j.get("job_url")) not in already]


def render_alert_email(jobs: list[dict]) -> tuple[str, str, str]:
    """Returns (subject, html, text) for the alert email listing `jobs`."""
    n = len(jobs)
    subject = f"\U0001F3AF {n} job{'s' if n != 1 else ''} scored above {DEFAULT_THRESHOLD}%"

    rows = "\n".join(
        f'<li style="margin-bottom:14px">'
        f'<strong>{_esc(j.get("title") or "Unknown role")}</strong>'
        f' · {_esc(j.get("company") or "Unknown company")}'
        f' <span style="color:#f97316;font-weight:700">{int(j.get("fit_score") or 0)}%</span><br/>'
        f'<a href="{_esc(j.get("job_url"))}" style="color:#2563eb">{_esc(j.get("job_url"))}</a>'
        f"</li>"
        for j in jobs
    )
    html = f"""
    <div style="font-family:system-ui,sans-serif;color:#111;line-height:1.6;max-width:560px">
      <h2 style="margin:0 0 12px">\U0001F3AF {n} top match{'es' if n != 1 else ''} this run</h2>
      <p style="margin:0 0 16px;color:#666">Scored above {DEFAULT_THRESHOLD}% in the latest job search run.</p>
      <ul style="padding-left:20px;margin:0">{rows}</ul>
    </div>
    """.strip()

    text_lines = [f"{n} job(s) scored above {DEFAULT_THRESHOLD}%:", ""]
    for j in jobs:
        text_lines.append(f"- {j.get('title') or 'Unknown role'} @ {j.get('company') or 'Unknown company'} ({int(j.get('fit_score') or 0)}%)")
        text_lines.append(f"  {j.get('job_url')}")
    text = "\n".join(text_lines)

    return subject, html, text


def _load_already_alerted(supabase_client, job_urls: list[str]) -> set[str]:
    if not job_urls:
        return set()
    try:
        resp = supabase_client.table("job_alerts").select("job_url").in_("job_url", job_urls).execute()
        return {row["job_url"] for row in (resp.data or [])}
    except Exception as e:
        print(f"  Alert dedup lookup failed ({e}); proceeding without dedup for this run.")
        return set()


def _mark_alerted(supabase_client, job_urls: list[str]) -> None:
    if not job_urls:
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        supabase_client.table("job_alerts").upsert(
            [{"job_url": u, "alerted_at": now} for u in job_urls],
            on_conflict="job_url",
        ).execute()
    except Exception as e:
        print(f"  Failed to record sent alerts ({e}); a future run may resend these.")


def send_high_score_alert(jobs_df, supabase_client=None, threshold: int = DEFAULT_THRESHOLD) -> int:
    """Sends one email listing this run's jobs scored above `threshold`.

    Returns the number of jobs included in the sent email (0 if nothing was sent).
    Safe to call every run: jobs already alerted (tracked in Supabase `job_alerts`)
    are skipped, and a missing RESEND_API_KEY or zero qualifying jobs is a no-op.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("  Skipping high-score alert: RESEND_API_KEY not set.")
        return 0

    candidates = filter_high_score_jobs(jobs_df, threshold)
    if not candidates:
        print(f"  No jobs above {threshold}% this run — skipping alert email.")
        return 0

    already_alerted = (
        _load_already_alerted(supabase_client, [str(j.get("job_url")) for j in candidates])
        if supabase_client is not None
        else set()
    )
    jobs = exclude_already_alerted(candidates, already_alerted)
    if not jobs:
        print(f"  {len(candidates)} job(s) above {threshold}% but already alerted — skipping.")
        return 0

    subject, html, text = render_alert_email(jobs)
    from_addr = os.environ.get("JOB_ALERT_FROM_EMAIL", "Job Search <onboarding@resend.dev>")
    to_addr = os.environ.get("JOB_ALERT_TO_EMAIL", DEFAULT_RECIPIENT)

    resp = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": from_addr, "to": [to_addr], "subject": subject, "html": html, "text": text},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"  Failed to send high-score alert email: {resp.status_code} {resp.text}")
        return 0

    print(f"  Sent high-score alert email for {len(jobs)} job(s) to {to_addr}.")

    if supabase_client is not None:
        _mark_alerted(supabase_client, [str(j.get("job_url")) for j in jobs])

    return len(jobs)
