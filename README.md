# 🏋️ LeetCode Workout Generator

Generate a personalized LeetCode practice plan that balances:

- **Personal weaknesses** — topics where your success rate is low
- **Spaced repetition** — problems you haven't seen in a while
- **Curated quality** — built on the [NeetCode 150](https://neetcode.io/practice/practice/neetcode150) interview prep list

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
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 3. Configure your secrets and preferences
cp env.example .env
# ...then open .env in your editor and fill in LEETCODE_SESSION and CSRF_TOKEN

# 4. Run
python leetcode_workout.py
```

The first run enriches the NeetCode 150 list via the LeetCode GraphQL API
(2–5 minutes, 150 calls) and pulls 500 of your most recent submissions to
bootstrap a personal weakness profile. Subsequent runs reuse the cached
universe and pull only `FETCH_LIMIT` new submissions (default: 60).

Outputs land in `./data/` and are gitignored — you can run this freely without
worrying about accidentally committing your practice history.

---

## Project layout

```
leetcode-workout/
├── leetcode_workout.py     # Entry point — what you run
├── utils.py                # Pipeline implementation
├── problems_list/
│   └── neetcode_150.json   # Bundled curated problem list
├── requirements.txt
├── env.example             # Template for your local .env
├── .gitignore              # Keeps .env and data/* out of git
├── README.md
└── data/                   # All inputs and outputs live here (gitignored)
    ├── problem_universe.csv               # generated — enriched candidate pool
    ├── Leetcode.csv                        # input — optional Kaggle dataset
    ├── leetcode_history_enriched_*.csv     # generated — dated history snapshots
    ├── leetcode_history_enriched_clean.csv # generated — deduped working file
    ├── leetcode_workout_plan_YYYY-MM-DD.txt   # generated — your workout
    └── leetcode_analytics_YYYY-MM-DD.jpg      # generated — your dashboard
```

Everything under `data/` is gitignored (except a placeholder `.gitkeep`), so
any datasets you drop in stay local.

---

## Configuration

All knobs live in `.env`. Copy `env.example`, then edit:

| Variable                 | Purpose                                                        | Default |
| ------------------------ | -------------------------------------------------------------- | ------- |
| `LEETCODE_SESSION`       | Your `LEETCODE_SESSION` cookie (required)                      | —       |
| `CSRF_TOKEN`             | Your `csrftoken` cookie (required)                             | —       |
| `LEETCODE_DATA_DIR`      | Where inputs/outputs live                                      | `./data` |
| `FETCH_LIMIT`            | Submissions per normal run                                     | `60`    |
| `FIRST_RUN_FETCH_LIMIT`  | Submissions on first run (no prior history)                    | `500`   |
| `ENRICH_LIMIT`           | Max NEW problems to enrich (one API call each)                 | `100`   |
| `TOP_K`                  | Number of problems in the final workout plan                   | `10`    |
| `REVIEW_PERCENTAGE`      | Review vs new mix (1–100); see guide below                     | `70`    |
| `SKIP_FETCH`             | Skip downloading; regenerate from existing data                | `false` |
| `ALLOW_PREMIUM`          | Include LeetCode Premium problems                              | `false` |
| `NEETCODE150_ONLY`       | Restrict recommendations to the NeetCode 150 list              | `true`  |

### `REVIEW_PERCENTAGE` guide

Based on learning science suggesting ~85% accuracy is the optimal error rate
for retention:

| Range  | Mode              | When to use                                        |
| ------ | ----------------- | -------------------------------------------------- |
| 80–90  | Consolidation     | Interview prep, solidifying what you already know  |
| 60–80  | Balanced learning | Recommended default for most users                 |
| 40–60  | Growth            | Expanding into new problem types                   |
| 20–40  | Challenge         | Pushing into unfamiliar territory                  |

Reviews are also weighted by spaced repetition (7–14d ago = low priority,
30–90d = high, 90d+ = highest) and boosted for problems you previously
struggled with.

---

## Usage

### Full pipeline (typical)

```bash
python leetcode_workout.py
```

Runs: build universe → fetch → enrich history → recommend → save plan → analytics.

### Override one value from the command line

CLI flags take precedence over `.env`:

```bash
python leetcode_workout.py --top-k 20 --review-percentage 50
python leetcode_workout.py --skip-fetch          # use cached data, just rescore
python leetcode_workout.py --no-neetcode150-only # mix in problems beyond NeetCode 150
python leetcode_workout.py --allow-premium       # include Premium problems
```

### Run a single stage

Useful when iterating on the recommender or troubleshooting:

```bash
python leetcode_workout.py --step universe    # build/refresh the problem universe
python leetcode_workout.py --step fetch       # preview submissions only
python leetcode_workout.py --step enrich      # fetch + enrich + dedupe history
python leetcode_workout.py --step recommend   # score and display recommendations
python leetcode_workout.py --step save-plan   # recommend + write .txt
python leetcode_workout.py --step analytics   # just regenerate the chart
```

See `python leetcode_workout.py --help` for the full flag list.

---

## Input datasets

This tool ships with the NeetCode 150 problem list
(`problems_list/neetcode_150.json`) as its default universe. On first run it
enriches each entry via the LeetCode GraphQL API and caches the result in
`data/problem_universe.csv`.

**Optional — expanded universe:** to get recommendations beyond the NeetCode
150, download a broader dataset and place it at `data/Leetcode.csv`:

- Suggested source: [Latest Complete LeetCode Problems Dataset 2025](https://www.kaggle.com/datasets/ashutoshpapnoi/latest-complete-leetcode-problems-dataset-2025) on Kaggle (3.6k problems)
- Expected columns: `Title`, `Difficulty`, `Link`, `Topics`, `Acceptance Rate (%)`, `Premium Only`, `Category`
- Slugs are derived from the `Link` column automatically
- Set `NEETCODE150_ONLY=false` in `.env` (or pass `--no-neetcode150-only`) to activate

All files under `data/` are gitignored, so anything you place there stays local.

---

## Troubleshooting

**`ERROR: Missing LeetCode credentials`** — your `.env` is missing or empty.
Run `cp env.example .env` and fill in both values.

**Fetcher returns 0 submissions** — your cookies have probably expired. Pull
fresh `LEETCODE_SESSION` and `csrftoken` values from your browser.

**Universe enrichment stalls or returns 429s** — LeetCode is rate-limiting.
The script backs off automatically; let it finish, or re-run and it will
resume from cache.

**Outputs landing in the wrong place** — set `LEETCODE_DATA_DIR` in `.env` to
an absolute path if the default `./data` doesn't suit you.

---

## How separation works

- **`.env`** holds your secrets and personal config. Gitignored.
- **`data/`** holds your problem-universe cache, history, and generated
  workouts. Gitignored (except `.gitkeep`).
- Everything else is safe to commit.

If you fork this repo, you can push your changes freely — nothing personal
will go with them.
