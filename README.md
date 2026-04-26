# Job Search Automation

Scrapes LinkedIn and Indeed via [JobSpy](https://github.com/speedyapply/JobSpy), then scores each posting against your CV using the Claude AI API. Results are ranked and saved as a CSV.

## How it works

1. **Scrape** — searches your configured keywords × locations across job boards
2. **Assess** — Claude reads your CV (cached in the prompt for efficiency) and scores each job 1–10
3. **Report** — prints a ranked table and saves a timestamped CSV to `results/`

Prompt caching means your CV is only sent to the API once per run — all subsequent job assessments read it from cache at ~10% of the normal token cost.

## Setup

### 1. Clone and create environment

```bash
git clone <your-repo-url>
cd job_search_automation
conda create -n job_search python=3.11 -y
conda activate job_search
pip install -r requirements.txt
```

### 2. Add your API key

Get a key from [console.anthropic.com](https://console.anthropic.com/settings/keys), then:

```bash
cp .env.example .env
# open .env and set: ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Configure your search

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your keywords, locations, and preferences. This file is gitignored — your settings stay private.

### 4. Add your CV

Place your CV in the `cv/` folder:

```
cv/cv.pdf     ← PDF preferred
cv/cv.txt     ← plain text also works
```

The `cv/` folder is gitignored — your CV is never committed.

### 5. Compress your CV (recommended, one-time)

Compresses your CV into a compact YAML profile to reduce token usage on every run:

```bash
python run_search.py --compress-cv
```

This saves `cv/cv_compressed.yaml` (also gitignored). Review and edit it if you like — it's what Claude will use to assess fit.

## Running

```bash
conda activate job_search

python run_search.py                  # full run (auto-detects CV)
python run_search.py --min-score 8   # only show high-confidence matches
python run_search.py --dry-run       # scrape only, skip AI scoring
python run_search.py --cv cv/cv.pdf  # force a specific CV file
```

Results are saved to `results/jobs_YYYYMMDD_HHMMSS.csv`.

## What's gitignored (private)

| Path | Why |
|---|---|
| `.env` | API key |
| `config.yaml` | your keywords and locations |
| `cv/` | your CV and compressed profile |
| `results/` | scraped job data |

## Configuration

| Key | Default | Description |
|---|---|---|
| `search.keywords` | — | Job titles / search terms |
| `search.locations` | — | Cities or "Remote" |
| `search.sites` | `linkedin, indeed` | Job boards to scrape |
| `search.hours_old` | `72` | Only jobs posted in the last N hours |
| `search.results_per_site` | `20` | Max results per keyword × location × site |
| `assessment.model` | `claude-haiku-4-5` | Model for scoring (haiku = cheap & fast) |
| `assessment.min_score` | `6` | Minimum score to include in summary |

## Requirements

- Python 3.11+
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- CV as PDF or text file
