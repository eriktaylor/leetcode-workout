#!/usr/bin/env python3
"""
LeetCode Workout Generator
==========================

Generates a personalized LeetCode workout plan based on:
  - Company frequency  (problems asked by the most companies)
  - Personal weakness  (topics with low success rate)
  - Recency / spaced repetition (problems not seen in a while)

Configuration is read from a local .env file (see .env.example).
Any value can also be overridden on the command line.

Examples
--------
    # Full pipeline, using values from .env
    python leetcode_workout.py

    # Override a single value
    python leetcode_workout.py --top-k 20

    # Skip the fetch step (regenerate recommendations from existing data)
    python leetcode_workout.py --skip-fetch

    # Run just one stage of the pipeline
    python leetcode_workout.py --step recommend
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    sys.stderr.write(
        "ERROR: python-dotenv is not installed.\n"
        "       Run: pip install -r requirements.txt\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Bootstrap: load .env and resolve paths before importing utils
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(
    os.getenv("LEETCODE_DATA_DIR", PROJECT_ROOT / "data")
).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Make sure utils.py (sitting next to this script) is importable.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import (  # noqa: E402  (import-after-path-mutation is intentional)
    LeetCodeFetcher,
    LeetCodeMergeValidator,
    LeetCodeSmartRecommender,
    enrich_and_save_history,
    deduplicate_and_save_clean,
    save_workout_plan,
    generate_analytics_jpeg,
    run_full_workout_pipeline,
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not an integer; using default {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _require_credentials() -> tuple[str, str]:
    session = os.getenv("LEETCODE_SESSION", "").strip()
    csrf = os.getenv("CSRF_TOKEN", "").strip()
    if not session or not csrf:
        sys.stderr.write(
            "ERROR: Missing LeetCode credentials.\n"
            "       Set LEETCODE_SESSION and CSRF_TOKEN in your .env file.\n"
            "       See README.md → 'Getting your LeetCode credentials'.\n"
        )
        sys.exit(1)
    return session, csrf


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a personalized LeetCode workout plan.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--fetch-limit", type=int, default=None,
                   help="Recent submissions to download (env: FETCH_LIMIT)")
    p.add_argument("--enrich-limit", type=int, default=None,
                   help="Max new problems to enrich per run (env: ENRICH_LIMIT)")
    p.add_argument("--target-candidates", type=int, default=None,
                   help="Size of candidate pool for recommendations (env: TARGET_CANDIDATES)")
    p.add_argument("--top-k", type=int, default=None,
                   help="Number of problems in the final workout plan (env: TOP_K)")
    p.add_argument("--review-percentage", type=int, default=None,
                   help="Review vs new problem balance, 1-100 (env: REVIEW_PERCENTAGE)")
    p.add_argument("--skip-fetch", dest="skip_fetch", action="store_true", default=None,
                   help="Skip downloading new submissions (env: SKIP_FETCH)")
    p.add_argument(
        "--step",
        choices=["fetch", "enrich", "harmonize", "recommend", "save-plan", "analytics"],
        help="Run a single pipeline step instead of the full pipeline.",
    )
    return p.parse_args()


def resolve_config(args: argparse.Namespace) -> dict:
    return {
        "fetch_limit": (
            args.fetch_limit if args.fetch_limit is not None
            else _env_int("FETCH_LIMIT", 60)
        ),
        "enrich_limit": (
            args.enrich_limit if args.enrich_limit is not None
            else _env_int("ENRICH_LIMIT", 100)
        ),
        "target_candidates": (
            args.target_candidates if args.target_candidates is not None
            else _env_int("TARGET_CANDIDATES", 75)
        ),
        "top_k": (
            args.top_k if args.top_k is not None
            else _env_int("TOP_K", 10)
        ),
        "review_percentage": (
            args.review_percentage if args.review_percentage is not None
            else _env_int("REVIEW_PERCENTAGE", 71)
        ),
        "skip_fetch": (
            args.skip_fetch if args.skip_fetch is not None
            else _env_bool("SKIP_FETCH", False)
        ),
    }


# ---------------------------------------------------------------------------
# Single-step runners (for --step)
# ---------------------------------------------------------------------------
def run_single_step(step: str, session: str, csrf: str, cfg: dict) -> None:
    drive_path = str(DATA_DIR)

    if step == "fetch":
        # Informational only: fetches and previews submissions without writing
        # them to disk. Use 'enrich' (or the full pipeline) to persist.
        fetcher = LeetCodeFetcher(session, csrf)
        df = fetcher.fetch_submission_history(limit=cfg["fetch_limit"])
        if df.empty:
            print("No submissions fetched.")
            return
        print("\n--- Submission breakdown ---")
        print(df["statusDisplay"].value_counts())
        print("\n--- Preview ---")
        print(df[["date", "title", "statusDisplay", "lang"]].head().to_string())
        print(
            "\nNote: 'fetch' alone does not persist results. "
            "Run --step enrich (or no --step at all) to save your history."
        )

    elif step == "enrich":
        fetcher = LeetCodeFetcher(session, csrf)
        df = fetcher.fetch_submission_history(limit=cfg["fetch_limit"])
        if df.empty:
            print("Nothing to enrich.")
            return
        history_path = enrich_and_save_history(
            df, drive_path=drive_path, enrich_limit=cfg["enrich_limit"]
        )
        deduplicate_and_save_clean(history_path, drive_path=drive_path)

    elif step == "harmonize":
        LeetCodeMergeValidator(drive_path=drive_path).run()

    elif step == "recommend":
        recommender = LeetCodeSmartRecommender(
            base_path=drive_path, review_percentage=cfg["review_percentage"]
        )
        recommender.run(
            target_candidates=cfg["target_candidates"], top_k=cfg["top_k"]
        )

    elif step == "save-plan":
        recommender = LeetCodeSmartRecommender(
            base_path=drive_path, review_percentage=cfg["review_percentage"]
        )
        recommender.run(
            target_candidates=cfg["target_candidates"], top_k=cfg["top_k"]
        )
        save_workout_plan(recommender, drive_path=drive_path, top_n=cfg["top_k"])

    elif step == "analytics":
        generate_analytics_jpeg(drive_path=drive_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    args = parse_args()
    cfg = resolve_config(args)

    print(f"📂 Data directory: {DATA_DIR}")
    print(f"⚙️  Config: {cfg}\n")

    session, csrf = _require_credentials()

    if args.step:
        run_single_step(args.step, session, csrf, cfg)
        return 0

    results = run_full_workout_pipeline(
        leetcode_session=session,
        csrf_token=csrf,
        drive_path=str(DATA_DIR),
        fetch_limit=cfg["fetch_limit"],
        enrich_limit=cfg["enrich_limit"],
        target_candidates=cfg["target_candidates"],
        top_k=cfg["top_k"],
        skip_fetch=cfg["skip_fetch"],
        review_percentage=cfg["review_percentage"],
    )

    if isinstance(results, dict):
        print("\n=== Output files ===")
        for k, v in results.items():
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
