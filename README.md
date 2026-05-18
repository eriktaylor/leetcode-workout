# 🏋️ LeetCode Workout Generator

Generate a personalized LeetCode practice plan that balances:

- **Company frequency** — problems asked by the most companies
- **Personal weaknesses** — topics where your success rate is low
- **Spaced repetition** — problems you haven't seen in a while

Each run produces a dated workout plan (`leetcode_workout_plan_YYYY-MM-DD.txt`)
and an analytics chart (`leetcode_analytics_YYYY-MM-DD.jpg`).

This is a local CLI tool. Your credentials and generated plans never leave your
machine.

---

## Quick start

```bash
# 1. Clone and enter the repo
git clone <your-fork-url> leetcode-workout
cd leetcode-workout

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure your secrets and preferences
cp .env.example .env
# ...then open .env in your editor and fill in LEETCODE_SESSION and CSRF_TOKEN

# 4. Drop the static input datasets into ./data/ (see "Input datasets" below)

# 5. Run
python leetcode_workout.py
```

Outputs land in `./data/` and are gitignored — you can run this freely without
worrying about accidentally committing your practice history.

---

## Project layout

```
leetcode-workout/
├── leetcode_workout.py     # Entry point — what you run
├── utils.py                # Pipeline implementation (fetcher, enricher,
│                           #   merger, recommender, analytics)
├── requirements.txt
├── .env.example            # Template for your local .env
├── .gitignore              # Keeps .env and data/* out of git
├── README.md
└── data/                   # All inputs and outputs live here (gitignored)
    ├── leetcode_problem_company_tags.csv   # input — you provide
    ├── leetcode_questions_kaggle.csv       # input — you provide
    ├── leetcode_history_enriched_*.csv     # generated, dated snapshots
    ├── leetcode_history_enriched_clean.csv # generated, deduped working file
    ├── leetcode_master_merged_raw.csv      # generated, merged dataset
    ├── leetcode_workout_plan_YYYY-MM-DD.txt   # generated — your workout
    └── leetcode_analytics_YYYY-MM-DD.jpg      # generated — your dashboard
```

Everything under `data/` is gitignored (except a placeholder `.gitkeep`), so
both your input datasets and your generated workout plans stay local.

---

## Configuration

All knobs live in `.env`. Copy `.env.example`, then edit:

| Variable             | Purpose                                                                 | Default |
| -------------------- | ----------------------------------------------------------------------- | ------- |
| `LEETCODE_SESSION`   | Your `LEETCODE_SESSION` cookie (required)                               | —       |
| `CSRF_TOKEN`         | Your `csrftoken` cookie (required)                                      | —       |
| `LEETCODE_DATA_DIR`  | Where inputs/outputs live                                               | `./data` |
| `FETCH_LIMIT`        | Recent submissions to download per run                                  | `60`    |
| `ENRICH_LIMIT`       | Max NEW problems to enrich (one API call each)                          | `100`   |
| `TARGET_CANDIDATES`  | Candidate-pool size before picking the top                              | `75`    |
| `TOP_K`              | Number of problems in the final workout plan                            | `10`    |
| `REVIEW_PERCENTAGE`  | Review vs new mix (1–100); see guide below                              | `71`    |
| `SKIP_FETCH`         | Skip downloading and regen recommendations from existing data           | `false` |

### `REVIEW_PERCENTAGE` guide

Based on learning science suggesting ~85% accuracy is the optimal error rate
for retention:

| Range  | Mode              | When to use                                        |
| ------ | ----------------- | -------------------------------------------------- |
| 80–90  | Consolidation     | Interview prep, solidifying what you already know  |
| 60–80  | Balanced learning | Recommended default for most users                 |
| 40–60  | Growth            | Expanding into new problem types                   |
| 20–40  | Challenge         | Pushing into unfamiliar territory                  |

The algorithm also weights reviews using spaced repetition (7–14d ago = low
priority, 30–90d = high, 90d+ = highest) and boosts problems you previously
struggled with.

---

## Usage

### Full pipeline (typical)

```bash
python leetcode_workout.py
```

Runs: fetch → enrich → harmonize → recommend → save plan → analytics.

### Override one value from the command line

CLI flags take precedence over `.env`:

```bash
python leetcode_workout.py --top-k 20 --review-percentage 50
python leetcode_workout.py --skip-fetch        # use cached data, just rescore
```

### Run a single stage

Useful when iterating on the recommender or troubleshooting:

```bash
python leetcode_workout.py --step fetch        # preview submissions only
python leetcode_workout.py --step enrich       # fetch + enrich + dedupe
python leetcode_workout.py --step harmonize    # rebuild the merged master CSV
python leetcode_workout.py --step recommend    # score candidates
python leetcode_workout.py --step save-plan    # recommend + write .txt
python leetcode_workout.py --step analytics    # just regenerate the chart
```

See `python leetcode_workout.py --help` for the full flag list.

---

## Input datasets

The recommender needs two static reference files in `data/`. They're not
included in this repo because they're third-party datasets — get your own
copies and drop them in:

- `leetcode_problem_company_tags.csv` — mapping of problems to companies
  that have asked them (various community sources publish this)
- `leetcode_questions_kaggle.csv` — problem metadata (difficulty, acceptance
  rate, tags); look on Kaggle for current LeetCode question datasets

Both files are gitignored by the `data/*` rule, so they'll stay local.

---

## Getting your LeetCode credentials

The fetcher impersonates a logged-in browser session using your cookies.

1. Log in to [leetcode.com](https://leetcode.com) in your browser.
2. Open DevTools (F12 or right-click → Inspect).
3. Go to the **Application** tab (Chrome/Edge) or **Storage** tab (Firefox).
4. Expand **Cookies** → click `https://leetcode.com`.
5. Copy the values of:
   - `LEETCODE_SESSION` (long string)
   - `csrftoken` (shorter string) — paste this into `CSRF_TOKEN` in `.env`
6. Paste them into your `.env` file.

These cookies expire periodically. If fetches start failing, grab fresh values.

> ⚠️ Treat these cookies like passwords. Anyone who has them can read your
> LeetCode account. They live only in your local `.env` — which this repo's
> `.gitignore` already excludes.

---

## Troubleshooting

**`ERROR: Missing LeetCode credentials`** — your `.env` is missing or empty.
Run `cp .env.example .env` and fill in both values.

**Fetcher returns 0 submissions** — your cookies have probably expired. Pull
fresh `LEETCODE_SESSION` and `csrftoken` values from your browser.

**`ModuleNotFoundError: utils`** — `utils.py` must sit next to
`leetcode_workout.py`. The script adds the project root to `sys.path`
automatically.

**Outputs landing in the wrong place** — set `LEETCODE_DATA_DIR` in `.env` to
an absolute path if the default `./data` doesn't suit you.

---

## How separation works

- **`.env`** holds your secrets and personal config. Gitignored.
- **`data/`** holds your input datasets, run history, and generated workouts.
  Gitignored (except `.gitkeep`).
- **`leetcode_workout.py`**, **`utils.py`**, **`.env.example`**, and the rest
  are the only files that should ever be committed.

If you fork this repo, you can push your changes freely — nothing personal
will go with them.
