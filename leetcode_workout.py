#!/usr/bin/env python3
"""
LeetCode Workout Generator
==========================

Builds the NeetCode 150 problem universe (enriching via GraphQL on first run),
fetches your submission history, computes per-tag weakness + spaced repetition
scores, and writes a daily workout plan.

Configuration is read from a local .env file (see env.example). Any value can
also be overridden on the command line.

Examples
--------
    # Full pipeline using values from .env
    python leetcode_workout.py

    # Override a single value
    python leetcode_workout.py --top-k 20

    # Skip the fetch step (regenerate from existing data)
    python leetcode_workout.py --skip-fetch

    # Run just one stage
    python leetcode_workout.py --step universe
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

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import (  # noqa: E402
    LeetCodeFetcher,
    LeetCodeSmartRecommender,
    build_problem_universe,
    deduplicate_and_save_clean,
    enrich_and_save_history,
    generate_analytics_jpeg,
    run_full_workout_pipeline,
    save_workout_plan,
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
                   help="Submissions to download on a normal run (env: FETCH_LIMIT)")
    p.add_argument("--first-run-fetch-limit", type=int, default=None,
                   help="Submissions to download on the very first run (env: FIRST_RUN_FETCH_LIMIT)")
    p.add_argument("--enrich-limit", type=int, default=None,
                   help="Max new problems to enrich per run (env: ENRICH_LIMIT)")
    p.add_argument("--top-k", type=int, default=None,
                   help="Number of problems in the final plan (env: TOP_K)")
    p.add_argument("--review-percentage", type=int, default=None,
                   help="Review vs new mix, 1-100 (env: REVIEW_PERCENTAGE)")
    p.add_argument("--skip-fetch", dest="skip_fetch", action="store_true", default=None,
                   help="Skip downloading new submissions (env: SKIP_FETCH)")

    premium = p.add_mutually_exclusive_group()
    premium.add_argument("--allow-premium", dest="allow_premium", action="store_true",
                         default=None, help="Include LeetCode Premium problems (env: ALLOW_PREMIUM)")
    premium.add_argument("--no-allow-premium", dest="allow_premium", action="store_false",
                         help="Exclude LeetCode Premium problems")

    nc150 = p.add_mutually_exclusive_group()
    nc150.add_argument("--neetcode150-only", dest="neetcode_150_only", action="store_true",
                       default=None, help="Restrict recommendations to NeetCode 150 (env: NEETCODE150_ONLY)")
    nc150.add_argument("--no-neetcode150-only", dest="neetcode_150_only", action="store_false",
                       help="Allow recommendations outside NeetCode 150 (requires data/Leetcode.csv)")

    p.add_argument(
        "--step",
        choices=["universe", "fetch", "enrich", "recommend", "save-plan", "analytics"],
        help="Run a single pipeline step instead of the full pipeline.",
    )
    return p.parse_args()


def resolve_config(args: argparse.Namespace) -> dict:
    return {
        "fetch_limit": (
            args.fetch_limit if args.fetch_limit is not None else _env_int("FETCH_LIMIT", 60)
        ),
        "first_run_fetch_limit": (
            args.first_run_fetch_limit
            if args.first_run_fetch_limit is not None
            else _env_int("FIRST_RUN_FETCH_LIMIT", 500)
        ),
        "enrich_limit": (
            args.enrich_limit if args.enrich_limit is not None else _env_int("ENRICH_LIMIT", 100)
        ),
        "top_k": (
            args.top_k if args.top_k is not None else _env_int("TOP_K", 10)
        ),
        "review_percentage": (
            args.review_percentage if args.review_percentage is not None
            else _env_int("REVIEW_PERCENTAGE", 70)
        ),
        "skip_fetch": (
            args.skip_fetch if args.skip_fetch is not None else _env_bool("SKIP_FETCH", False)
        ),
        "allow_premium": (
            args.allow_premium if args.allow_premium is not None
            else _env_bool("ALLOW_PREMIUM", False)
        ),
        "neetcode_150_only": (
            args.neetcode_150_only if args.neetcode_150_only is not None
            else _env_bool("NEETCODE150_ONLY", True)
        ),
    }


# ---------------------------------------------------------------------------
# Single-step runners (for --step)
# ---------------------------------------------------------------------------
def run_single_step(step: str, session: str, csrf: str, cfg: dict) -> None:
    drive_path = str(DATA_DIR)

    if step == "universe":
        build_problem_universe(drive_path, include_extended=not cfg["neetcode_150_only"])

    elif step == "fetch":
        df = LeetCodeFetcher(session, csrf).fetch_submission_history(limit=cfg["fetch_limit"])
        if df.empty:
            print("No submissions fetched.")
            return
        print("\n--- Submission breakdown ---")
        print(df["statusDisplay"].value_counts())
        print("\n--- Preview ---")
        print(df[["date", "title", "statusDisplay", "lang"]].head().to_string())
        print("\nNote: 'fetch' alone does not persist results. Use --step enrich (or no --step) to save.")

    elif step == "enrich":
        df = LeetCodeFetcher(session, csrf).fetch_submission_history(limit=cfg["fetch_limit"])
        if df.empty:
            print("Nothing to enrich.")
            return
        history_path = enrich_and_save_history(df, drive_path=drive_path, enrich_limit=cfg["enrich_limit"])
        deduplicate_and_save_clean(history_path, drive_path=drive_path)

    elif step in ("recommend", "save-plan"):
        recommender = LeetCodeSmartRecommender(
            base_path=drive_path,
            review_percentage=cfg["review_percentage"],
            allow_premium=cfg["allow_premium"],
            neetcode_150_only=cfg["neetcode_150_only"],
        )
        recommender.run(top_k=cfg["top_k"])
        if step == "save-plan":
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

    # Only steps that actually fetch need credentials
    will_fetch = args.step in ("fetch", "enrich") or (args.step is None and not cfg["skip_fetch"])
    session, csrf = ("", "")
    if will_fetch:
        session, csrf = _require_credentials()

    if args.step:
        run_single_step(args.step, session, csrf, cfg)
        return 0

    results = run_full_workout_pipeline(
        leetcode_session=session,
        csrf_token=csrf,
        drive_path=str(DATA_DIR),
        fetch_limit=cfg["fetch_limit"],
        first_run_fetch_limit=cfg["first_run_fetch_limit"],
        enrich_limit=cfg["enrich_limit"],
        top_k=cfg["top_k"],
        skip_fetch=cfg["skip_fetch"],
        review_percentage=cfg["review_percentage"],
        allow_premium=cfg["allow_premium"],
        neetcode_150_only=cfg["neetcode_150_only"],
    )

    if isinstance(results, dict):
        print("\n=== Output files ===")
        for k, v in results.items():
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
