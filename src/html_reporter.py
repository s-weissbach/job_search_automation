"""Generate a self-contained HTML report from the persistent score store."""

from datetime import datetime
from pathlib import Path

import pandas as pd


_SCORE_COLORS = {
    (8, 10): ("#1a7a4a", "#e6f5ee"),   # green
    (6,  7): ("#7a6a1a", "#f5f0e6"),   # amber
    (4,  5): ("#a05010", "#f5ede6"),   # orange
    (1,  3): ("#8a1a1a", "#f5e6e6"),   # red
}

_SENIORITY_LABELS = {
    "match":      ("✓ Seniority match",    "#1a7a4a", "#e6f5ee"),
    "too_junior": ("↓ Too junior",          "#7a6a1a", "#f5f0e6"),
    "too_senior": ("↑ Too senior",          "#a05010", "#f5ede6"),
    "unclear":    ("? Seniority unclear",  "#555",    "#f0f0f0"),
}


def _score_style(score: int) -> tuple[str, str]:
    for (lo, hi), (fg, bg) in _SCORE_COLORS.items():
        if lo <= score <= hi:
            return fg, bg
    return "#555", "#f0f0f0"


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_description(raw: str) -> str:
    if not raw or raw == "nan":
        return ""
    # Convert newlines to <br> and wrap in a collapsible <details>
    escaped = _html_escape(raw).replace("\n", "<br>")
    return f"""<details class="desc-details">
  <summary class="desc-toggle">Job description</summary>
  <div class="desc-body">{escaped}</div>
</details>"""


def _get(row, field: str, default=""):
    """Get a field from either a namedtuple (itertuples) or a Series."""
    val = getattr(row, field, default)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return val


def _render_card(row, is_new: bool) -> str:
    raw_score = _get(row, "fit_score", 0)
    score = int(raw_score) if raw_score != "" and not (isinstance(raw_score, float) and pd.isna(raw_score)) else 0
    score_fg, score_bg = _score_style(score)

    title      = _html_escape(_get(row, "title", "Unknown title") or "Unknown title")
    company    = _html_escape(_get(row, "company", "") or "")
    location   = _html_escape(_get(row, "location", "") or "")
    site       = _html_escape(_get(row, "site", "") or "")
    date_posted = _html_escape(_get(row, "date_posted", "") or "")
    assessed_at = _html_escape(_get(row, "assessed_at", "") or "")
    url        = str(_get(row, "job_url", "") or "")
    reasoning  = _html_escape(_get(row, "fit_reasoning", "") or "")
    skills_raw = str(_get(row, "matching_skills", "") or "")
    concerns_raw = str(_get(row, "concerns", "") or "")
    description_raw = str(_get(row, "description", "") or "")

    seniority  = str(_get(row, "seniority_match", "unclear") or "unclear")
    sen_label, sen_fg, sen_bg = _SENIORITY_LABELS.get(seniority, _SENIORITY_LABELS["unclear"])

    is_active_val = str(_get(row, "is_active", "")).lower()
    if is_active_val == "true" or is_active_val == "active":
        active_badge = '<span class="badge" style="background:#e6f5ee;color:#1a7a4a">● Active</span>'
    elif is_active_val == "false" or is_active_val == "expired":
        active_badge = '<span class="badge" style="background:#f5e6e6;color:#8a1a1a">✕ Expired</span>'
    else:
        active_badge = '<span class="badge" style="background:#f0f0f0;color:#555">? Status unknown</span>'

    new_badge = '<span class="badge" style="background:#dce8ff;color:#1a4a9a;font-weight:600">NEW</span>' if is_new else ""

    skills_html = ""
    if skills_raw and skills_raw != "nan":
        items = [s.strip() for s in skills_raw.split(";") if s.strip()]
        if items:
            chips = "".join(f'<span class="chip chip-skill">{_html_escape(s)}</span>' for s in items)
            skills_html = f'<div class="chip-row"><strong>Matching:</strong> {chips}</div>'

    concerns_html = ""
    if concerns_raw and concerns_raw != "nan":
        items = [s.strip() for s in concerns_raw.split(";") if s.strip()]
        if items:
            chips = "".join(f'<span class="chip chip-concern">{_html_escape(s)}</span>' for s in items)
            concerns_html = f'<div class="chip-row"><strong>Concerns:</strong> {chips}</div>'

    url_link = f'<a href="{_html_escape(url)}" target="_blank" rel="noopener" class="job-link">Open job ↗</a>' if url else ""

    meta_parts = []
    if site:
        meta_parts.append(f'<span class="meta-item">📍 {site}</span>')
    if location:
        meta_parts.append(f'<span class="meta-item">🌍 {location}</span>')
    if date_posted:
        meta_parts.append(f'<span class="meta-item">📅 Posted: {date_posted}</span>')
    if assessed_at:
        meta_parts.append(f'<span class="meta-item">🔍 Assessed: {assessed_at}</span>')

    meta_html = " ".join(meta_parts)

    return f"""
<div class="card"
     data-score="{score}"
     data-seniority="{_html_escape(seniority)}"
     data-site="{_html_escape(str(_get(row, 'site', '') or ''))}"
     data-active="{_html_escape(is_active_val)}"
     data-search="{_html_escape((title + ' ' + company + ' ' + location).lower())}">
  <div class="card-header">
    <span class="score-badge" style="background:{score_bg};color:{score_fg}">{score}/10</span>
    <div class="card-title-block">
      <div class="card-title">{title}</div>
      <div class="card-company">{company}</div>
    </div>
    <div class="card-badges">
      {new_badge}
      {active_badge}
      <span class="badge" style="background:{sen_bg};color:{sen_fg}">{sen_label}</span>
    </div>
    {url_link}
  </div>
  <div class="card-meta">{meta_html}</div>
  <div class="card-body">
    {f'<p class="reasoning">{reasoning}</p>' if reasoning else ""}
    {skills_html}
    {concerns_html}
  </div>
  {_render_description(description_raw)}
</div>"""


_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f5f7fa; color: #1a1a2e; line-height: 1.5; }
header { background: #1a1a2e; color: #fff; padding: 24px 32px; }
header h1 { font-size: 1.6rem; font-weight: 700; }
header p  { color: #aac; font-size: 0.9rem; margin-top: 4px; }
.stats { display: flex; gap: 24px; margin-top: 12px; flex-wrap: wrap; }
.stat { background: rgba(255,255,255,0.1); border-radius: 8px; padding: 8px 16px; }
.stat-val { font-size: 1.5rem; font-weight: 700; }
.stat-lbl { font-size: 0.75rem; color: #aac; }

.controls { background: #fff; border-bottom: 1px solid #e0e4ed;
            padding: 16px 32px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.controls input, .controls select {
  padding: 8px 12px; border: 1px solid #d0d4dd; border-radius: 8px;
  font-size: 0.9rem; background: #f8f9fb; }
.controls input { flex: 1; min-width: 200px; }
#result-count { margin-left: auto; color: #666; font-size: 0.85rem; }

main { max-width: 960px; margin: 24px auto; padding: 0 16px; }

.card { background: #fff; border-radius: 12px; margin-bottom: 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.07); border: 1px solid #e8ecf3;
        transition: box-shadow 0.15s; }
.card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.11); }
.card.hidden { display: none; }

.card-header { display: flex; align-items: flex-start; gap: 14px; padding: 18px 20px 10px; }
.score-badge { font-size: 1.3rem; font-weight: 800; border-radius: 10px;
               padding: 6px 12px; flex-shrink: 0; }
.card-title-block { flex: 1; min-width: 0; }
.card-title { font-size: 1.05rem; font-weight: 600; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }
.card-company { color: #555; font-size: 0.88rem; margin-top: 2px; }
.card-badges { display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0; }
.badge { font-size: 0.75rem; padding: 3px 9px; border-radius: 20px; white-space: nowrap; }
.job-link { flex-shrink: 0; font-size: 0.85rem; color: #2255cc; text-decoration: none;
            padding: 6px 14px; border: 1px solid #2255cc; border-radius: 8px;
            white-space: nowrap; align-self: center; }
.job-link:hover { background: #f0f4ff; }

.card-meta { padding: 0 20px 10px; display: flex; gap: 16px; flex-wrap: wrap; }
.meta-item { font-size: 0.8rem; color: #666; }

.card-body { padding: 0 20px 16px; }
.reasoning { font-size: 0.9rem; color: #333; margin-bottom: 10px; }
.chip-row { margin-bottom: 8px; font-size: 0.82rem; color: #444; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 20px;
        margin: 3px 3px 0 0; font-size: 0.78rem; }
.chip-skill   { background: #e6f5ee; color: #1a5a35; }
.chip-concern { background: #fff0e6; color: #7a3010; }
.desc-details { border-top: 1px solid #e8ecf3; }
.desc-toggle  { cursor: pointer; padding: 10px 20px; font-size: 0.85rem;
                color: #2255cc; user-select: none; list-style: none; }
.desc-toggle::-webkit-details-marker { display: none; }
.desc-toggle::before { content: "▶  "; font-size: 0.7rem; }
details[open] .desc-toggle::before { content: "▼  "; }
.desc-body    { padding: 12px 20px 16px; font-size: 0.85rem; color: #333;
                line-height: 1.65; max-height: 420px; overflow-y: auto;
                border-top: 1px solid #f0f2f7; background: #fafbfd; }
"""

_JS = """
const cards = document.querySelectorAll('.card');

function applyFilters() {
  const q     = document.getElementById('search').value.toLowerCase();
  const minSc = parseInt(document.getElementById('min-score').value || '1');
  const sen   = document.getElementById('filter-seniority').value;
  const site  = document.getElementById('filter-site').value;
  const act   = document.getElementById('filter-active').value;

  let visible = 0;
  cards.forEach(c => {
    const score  = parseInt(c.dataset.score);
    const match  =
      score >= minSc &&
      (sen  === '' || c.dataset.seniority === sen) &&
      (site === '' || c.dataset.site      === site) &&
      (act  === '' || c.dataset.active    === act) &&
      (q    === '' || c.dataset.search.includes(q));
    c.classList.toggle('hidden', !match);
    if (match) visible++;
  });
  document.getElementById('result-count').textContent = `${visible} jobs shown`;
}

['search','min-score','filter-seniority','filter-site','filter-active']
  .forEach(id => document.getElementById(id)?.addEventListener('input', applyFilters));

applyFilters();
"""


def generate_html_report(
    score_store_path: str | Path,
    output_path: str | Path,
    new_urls: set[str] | None = None,
    min_score: int = 1,
) -> str:
    """Generate a self-contained HTML report from the score store.

    Args:
        score_store_path: Path to .score_store.csv
        output_path: Where to write the HTML file
        new_urls: Set of job URLs assessed in the current run (highlighted as NEW)
        min_score: Jobs below this score are still shown but the filter defaults to this

    Returns:
        Path to the written HTML file.
    """
    store_path = Path(score_store_path)
    if not store_path.exists():
        return ""

    df = pd.read_csv(store_path)
    if df.empty:
        return ""

    # Sort by score desc, then by assessed_at desc
    if "fit_score" in df.columns:
        df = df.sort_values(["fit_score", "assessed_at"], ascending=[False, False])

    # Collect unique sites for filter dropdown
    sites = sorted(df["site"].dropna().unique()) if "site" in df.columns else []

    new_urls = new_urls or set()

    total = len(df)
    new_count = len(new_urls)
    above_min = int((df["fit_score"] >= min_score).sum()) if "fit_score" in df.columns else 0

    cards_html = "\n".join(
        _render_card(row, str(getattr(row, "job_url", "")) in new_urls)
        for row in df.itertuples(index=False)
    )

    # site options
    site_options = "\n".join(f'<option value="{s}">{s}</option>' for s in sites)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Search Report — {timestamp}</title>
<style>
{_CSS}
</style>
</head>
<body>
<header>
  <h1>Job Search Report</h1>
  <p>Generated {timestamp}</p>
  <div class="stats">
    <div class="stat"><div class="stat-val">{total}</div><div class="stat-lbl">Total assessed</div></div>
    <div class="stat"><div class="stat-val">{new_count}</div><div class="stat-lbl">New this run</div></div>
    <div class="stat"><div class="stat-val">{above_min}</div><div class="stat-lbl">Score ≥ {min_score}</div></div>
  </div>
</header>
<div class="controls">
  <input id="search" type="search" placeholder="Search title, company, location…">
  <select id="min-score">
    <option value="1">All scores</option>
    <option value="6" selected>Score ≥ 6</option>
    <option value="7">Score ≥ 7</option>
    <option value="8">Score ≥ 8</option>
  </select>
  <select id="filter-seniority">
    <option value="">All seniority</option>
    <option value="match">Match</option>
    <option value="too_junior">Too junior</option>
    <option value="too_senior">Too senior</option>
    <option value="unclear">Unclear</option>
  </select>
  <select id="filter-site">
    <option value="">All sources</option>
    {site_options}
  </select>
  <select id="filter-active">
    <option value="">Any status</option>
    <option value="true">Active only</option>
    <option value="false">Expired only</option>
    <option value="">Unknown</option>
  </select>
  <span id="result-count"></span>
</div>
<main>
{cards_html}
</main>
<script>
{_JS}
</script>
</body>
</html>"""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
