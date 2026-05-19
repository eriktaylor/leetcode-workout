# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env   # then fill in LEETCODE_SESSION and CSRF_TOKEN
```

The bundled NeetCode 150 list (`problems_list/neetcode_150.json`) is the
default problem universe. To recommend problems outside the NeetCode 150,
drop a Kaggle problem dump at `data/Leetcode.csv` and set
`NEETCODE150_ONLY=false`.

## Running

```bash
# Full pipeline (typical usage)
python leetcode_workout.py

# Override config (CLI flags beat .env)
python leetcode_workout.py --top-k 20 --review-percentage 50
python leetcode_workout.py --skip-fetch
python leetcode_workout.py --no-neetcode150-only --allow-premium

# Run a single pipeline stage
python leetcode_workout.py --step universe    # build/refresh problem universe
python leetcode_workout.py --step fetch       # preview submissions only (no write)
python leetcode_workout.py --step enrich      # fetch + enrich + dedupe history
python leetcode_workout.py --step recommend   # score and display recommendations
python leetcode_workout.py --step save-plan   # recommend + write .txt
python leetcode_workout.py --step analytics   # regenerate the chart only
```

## Project layout

```
leetcode-workout/
├── leetcode_workout.py     # Entry point
├── utils.py                # All pipeline implementation
├── problems_list/
│   └── neetcode_150.json   # Bundled curated problem list (source of truth)
├── .env.example
└── data/                   # Gitignored; all inputs and outputs live here
    ├── problem_universe.csv               # enriched candidate pool (cached)
    ├── Leetcode.csv                        # optional Kaggle dataset (user-provided)
    ├── leetcode_history_enriched_clean.csv # deduped working history
    └── leetcode_workout_plan_YYYY-MM-DD.txt
```

## Architecture

Two-file project: `leetcode_workout.py` (CLI/entry point) + `utils.py` (implementation).

**`leetcode_workout.py`** loads `.env` via `python-dotenv`, resolves config (CLI flags beat env beat defaults), validates credentials only when a fetch is actually going to happen, and dispatches to `run_full_workout_pipeline()` or to a single-step runner.

**`utils.py`** is organized into four logical blocks:

| Block | Role |
|---|---|
| `LeetCodeFetcher` | Paginates the GraphQL `submissionList` query using browser session cookies (20 per batch, jittered sleep, 429 backoff). Auth-required. |
| `LeetCodeEnricher` + history helpers (`enrich_and_save_history`, `deduplicate_and_save_clean`) | Fetch difficulty + tags for slugs in the user's history (unauthenticated GraphQL), merge into `leetcode_history_enriched_<ts>.csv`, dedupe to `leetcode_history_enriched_clean.csv`. |
| `build_problem_universe` (+ `load_neetcode_150`, `load_extended_dataset`, `enrich_problem_universe`) | Build the recommendation candidate pool: NeetCode 150 ∪ optional `data/Leetcode.csv`. Enrich missing entries via GraphQL with sequential 0.5–1.5s jitter + exponential backoff on 429s. Cached to `data/problem_universe.csv` so subsequent runs are fast. |
| `LeetCodeSmartRecommender` | Loads the universe and the clean history, applies `ALLOW_PREMIUM` / `NEETCODE150_ONLY` filters, computes per-tag weakness and per-problem spaced-repetition stats from history, scores candidates, enforces the review/new mix, and renders the plan. |

**Pipeline data flow:**

```
build_problem_universe
  → data/problem_universe.csv     (cached; only re-enriches missing slugs)

LeetCodeFetcher.fetch_submission_history (500 on first run, FETCH_LIMIT after)
  → enrich_and_save_history → leetcode_history_enriched_<timestamp>.csv
  → deduplicate_and_save_clean → leetcode_history_enriched_clean.csv

LeetCodeSmartRecommender.run
  → save_workout_plan → leetcode_workout_plan_YYYY-MM-DD.txt
  → generate_analytics_jpeg → leetcode_analytics_YYYY-MM-DD.jpg
```

First-run detection: absence of `data/leetcode_history_enriched_clean.csv` triggers the larger `FIRST_RUN_FETCH_LIMIT` pull.

## Scoring (in `LeetCodeSmartRecommender._score_row`)

Company-frequency scoring was removed (LeetCode Premium data isn't redistributable). Current signals:

- **Tag weakness**: `(1 − tag_success_rate) × 25` per matching tag — primary signal
- **Difficulty preference**: `Medium=10, Hard=8, Easy=5` — mild medium-heavy bias
- **Reviews only — spaced repetition**: ramps from −50 (under 7 days) to +35 (90+ days)
- **Reviews only — struggle bonus**: `(1 − success_rate) × 20`
- **New problems**: +12 novelty bonus
- Small `N(0, 2)` jitter for variety

The review/new split is enforced by `REVIEW_PERCENTAGE` via separate top-K-from-each-bucket selection, with shortfall fallback to the other bucket.

## Legal / dataset notes

- **Do not redistribute** `leetcode_problem_company_tags.csv` — it's derived from LeetCode Premium data and including it would be a ToS violation / DMCA risk. This file is intentionally not used anywhere in the current code.
- The optional extended dataset (`data/Leetcode.csv`) is user-provided and gitignored.
- Slug extraction for the extended dataset uses `re.search(r"problems/([^/?#]+)", url)` on the `Link` column; rows without parseable links are dropped with a warning count.
