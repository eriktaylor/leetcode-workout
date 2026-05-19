"""Tests for workout recommendation logic — jitter, variety, and trial naming.

These tests use synthetic CSV data and never touch the LeetCode API.
Run with:
    pytest tests/test_workout.py -v
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from utils import (
    CLEAN_HISTORY_FILENAME,
    PROBLEM_UNIVERSE_FILENAME,
    LeetCodeSmartRecommender,
    _get_next_trial_number,
    save_workout_plan,
)

# ---------------------------------------------------------------------------
# Shared synthetic data
# ---------------------------------------------------------------------------
_DIFFICULTIES = ["Easy", "Medium", "Hard", "Easy", "Medium", "Hard", "Easy", "Medium", "Hard", "Medium"]
_TAGS = [
    "Array, Hash Table",
    "Dynamic Programming, Memoization",
    "Graph, DFS",
    "Binary Search, Array",
    "Two Pointers, String",
    "Tree, DFS",
    "Stack, Monotonic Stack",
    "Heap (Priority Queue)",
    "Sliding Window, Array",
    "Backtracking, Recursion",
]

UNIVERSE_ROWS = [
    {
        "slug": f"problem-{i}",
        "title": f"Problem {i}",
        "difficulty": d,
        "tags": t,
        "acceptance": 50.0,
        "isPremium": False,
        "source": "neetcode150",
    }
    for i, (d, t) in enumerate(zip(_DIFFICULTIES, _TAGS))
]


def _write_universe(path: str) -> None:
    pd.DataFrame(UNIVERSE_ROWS).to_csv(path, index=False)


def _write_history(path: str, n_solved: int = 5) -> None:
    """Write a history where the first n_solved problems have 3 submissions each
    (2 Accepted, 1 Wrong Answer), all solved ~200 days ago so spaced-rep fires."""
    base_date = datetime(2025, 10, 1)
    rows = []
    for i in range(n_solved):
        slug = f"problem-{i}"
        tags = UNIVERSE_ROWS[i]["tags"]
        diff = UNIVERSE_ROWS[i]["difficulty"]
        solve_date = base_date + timedelta(days=i * 30)
        for j in range(3):
            rows.append({
                "id": str(i * 10 + j),
                "slug": slug,
                "titleSlug": slug,
                "statusDisplay": "Accepted" if j < 2 else "Wrong Answer",
                "date": solve_date.isoformat(),
                "date_dt": solve_date.isoformat(),
                "tags": tags,
                "difficulty": diff,
                "lang": "python3",
            })
    pd.DataFrame(rows).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path) -> str:
    _write_universe(tmp_path / PROBLEM_UNIVERSE_FILENAME)
    _write_history(tmp_path / CLEAN_HISTORY_FILENAME)
    return str(tmp_path)


@pytest.fixture
def cold_data_dir(tmp_path) -> str:
    """Universe only — no history file (cold-start scenario)."""
    _write_universe(tmp_path / PROBLEM_UNIVERSE_FILENAME)
    return str(tmp_path)


def _make_recommender(data_dir: str, jitter: float = 2.0, **kwargs) -> LeetCodeSmartRecommender:
    r = LeetCodeSmartRecommender(base_path=data_dir, jitter=jitter, **kwargs)
    r.load_data()
    return r


def _slugs(df: pd.DataFrame) -> list[str]:
    return df["slug"].tolist()


# ---------------------------------------------------------------------------
# Jitter behaviour
# ---------------------------------------------------------------------------

class TestJitter:
    def test_zero_jitter_is_deterministic(self, data_dir):
        """jitter=0 → pure signal, same ranking regardless of seed."""
        r = _make_recommender(data_dir, jitter=0)
        cands = r._generate_candidates()
        result1 = _slugs(r.rank(cands.copy(), top_k=5, seed=1))
        result2 = _slugs(r.rank(cands.copy(), top_k=5, seed=99))
        assert result1 == result2

    def test_high_jitter_produces_variety(self, data_dir):
        """jitter=100 → noise dominates signal; different seeds → different picks."""
        r = _make_recommender(data_dir, jitter=100)
        cands = r._generate_candidates()
        unique_orderings = {
            tuple(_slugs(r.rank(cands.copy(), top_k=5, seed=s)))
            for s in range(20)
        }
        assert len(unique_orderings) > 1, "High jitter should produce varied recommendations"

    def test_default_jitter_scores_are_positive(self, data_dir):
        """Signal should dominate default jitter — all scores should be positive."""
        r = _make_recommender(data_dir, jitter=2)
        cands = r._generate_candidates()
        top = r.rank(cands, top_k=5, seed=42)
        assert not top.empty
        assert (top["Final_Score"] > 0).all()

    def test_seed_reproduces_same_result(self, data_dir):
        """Same seed → same ranked list even with non-zero jitter."""
        r = _make_recommender(data_dir, jitter=5)
        cands = r._generate_candidates()
        run_a = _slugs(r.rank(cands.copy(), top_k=5, seed=7))
        run_b = _slugs(r.rank(cands.copy(), top_k=5, seed=7))
        assert run_a == run_b


# ---------------------------------------------------------------------------
# Trial naming
# ---------------------------------------------------------------------------

class TestTrialNaming:
    def test_first_run_no_suffix(self, tmp_path):
        assert _get_next_trial_number(str(tmp_path), "2026-05-19") == 1

    def test_existing_base_plan_yields_trial2(self, tmp_path):
        (tmp_path / "leetcode_workout_plan_2026-05-19.txt").write_text("plan1")
        assert _get_next_trial_number(str(tmp_path), "2026-05-19") == 2

    def test_sequential_trials(self, tmp_path):
        (tmp_path / "leetcode_workout_plan_2026-05-19.txt").write_text("plan1")
        (tmp_path / "leetcode_workout_plan_2026-05-19_trial2.txt").write_text("plan2")
        assert _get_next_trial_number(str(tmp_path), "2026-05-19") == 3

    def test_save_plan_trial_suffix_in_filename(self, data_dir):
        r = _make_recommender(data_dir, jitter=0)
        cands = r._generate_candidates()
        r.df_final = r.rank(cands, top_k=5, seed=42)
        path = save_workout_plan(r, drive_path=data_dir, top_n=5, trial=3)
        assert "_trial3" in os.path.basename(path)
        assert os.path.exists(path)

    def test_save_plan_trial1_has_no_suffix(self, data_dir):
        r = _make_recommender(data_dir, jitter=0)
        cands = r._generate_candidates()
        r.df_final = r.rank(cands, top_k=5, seed=42)
        path = save_workout_plan(r, drive_path=data_dir, top_n=5, trial=1)
        assert "trial" not in os.path.basename(path)
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# Mock pipeline scenarios
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_cold_start_all_new(self, cold_data_dir):
        """No history → every pick is a New problem."""
        r = _make_recommender(cold_data_dir, jitter=0)
        cands = r._generate_candidates()
        top = r.rank(cands, top_k=5, seed=42)
        assert not top.empty
        assert (~top["is_review"]).all(), "Cold-start should produce only New picks"

    def test_premium_filter_excludes_problems(self, tmp_path):
        """isPremium=True problems are excluded when allow_premium=False."""
        rows = [dict(r) for r in UNIVERSE_ROWS]
        rows[0]["isPremium"] = True
        pd.DataFrame(rows).to_csv(tmp_path / PROBLEM_UNIVERSE_FILENAME, index=False)

        r = LeetCodeSmartRecommender(base_path=str(tmp_path), allow_premium=False, jitter=0)
        r.load_data()
        assert "problem-0" not in r.df_universe["slug"].tolist()

    def test_review_percentage_total(self, data_dir):
        """Regardless of review/new split, total picks == top_k."""
        r = _make_recommender(data_dir, review_percentage=60, jitter=0)
        cands = r._generate_candidates()
        top = r.rank(cands, top_k=10, seed=42)
        assert len(top) == 10

    def test_reviews_and_new_coexist(self, data_dir):
        """With history present and review_percentage=50, both buckets are represented."""
        r = _make_recommender(data_dir, review_percentage=50, jitter=0)
        cands = r._generate_candidates()
        top = r.rank(cands, top_k=10, seed=42)
        assert top["is_review"].any(), "Should include at least one Review"
        assert (~top["is_review"]).any(), "Should include at least one New"

    def test_spaced_rep_boosts_old_problems(self, data_dir):
        """Problems solved ~200 days ago should outscore recently solved problems
        when difficulty and tag signals are equal."""
        r = _make_recommender(data_dir, jitter=0)
        cands = r._generate_candidates()
        top = r.rank(cands, top_k=10, seed=42)

        review_rows = top[top["is_review"]]
        if len(review_rows) >= 2:
            # Fixture problems were solved 110-230 days ago (all get spaced-rep boost)
            days = [r.problem_stats[slug]["days_since_solve"] for slug in review_rows["slug"]]
            assert all(d >= 100 for d in days), (
                f"All selected reviews should be old enough for spaced-rep boost, got days={days}"
            )

    def test_plan_file_content(self, data_dir):
        """save_workout_plan writes a readable file with correct structure."""
        r = _make_recommender(data_dir, jitter=0)
        cands = r._generate_candidates()
        r.df_final = r.rank(cands, top_k=5, seed=42)
        path = save_workout_plan(r, drive_path=data_dir, top_n=5)

        with open(path) as f:
            content = f.read()
        assert "leetcode.com/problems/" in content
        assert "Score:" in content
        assert "Tags:" in content
