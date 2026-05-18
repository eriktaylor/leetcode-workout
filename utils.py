"""
LeetCode Workout Utils
======================
Core utilities for fetching submission history, building the problem universe
(NeetCode 150 + optional extended Kaggle dataset), and recommending the next
problems to practice via spaced repetition + personal weakness analysis.
"""

from __future__ import annotations

import glob
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

# =============================================================================
# CONFIGURATION
# =============================================================================
DEFAULT_DRIVE_PATH = "data"
LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"
DEFAULT_HEADERS = {
    "content-type": "application/json",
    "origin": "https://leetcode.com",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    ),
}

NEETCODE_150_PATH = Path(__file__).resolve().parent / "problems_list" / "neetcode_150.json"
PROBLEM_UNIVERSE_FILENAME = "problem_universe.csv"
EXTENDED_DATASET_FILENAME = "Leetcode.csv"
CLEAN_HISTORY_FILENAME = "leetcode_history_enriched_clean.csv"


# =============================================================================
# LEETCODE FETCHER — downloads your submission history
# =============================================================================
class LeetCodeFetcher:
    """Fetch personal submission history via LeetCode's authenticated GraphQL API."""

    def __init__(self, leetcode_session: str, csrf_token: str):
        self.url = LEETCODE_GRAPHQL_URL
        self.session_id = leetcode_session
        self.csrf_token = csrf_token
        self.headers = {
            **DEFAULT_HEADERS,
            "referer": "https://leetcode.com/submission/",
            "cookie": f"LEETCODE_SESSION={self.session_id}; csrftoken={self.csrf_token};",
            "x-csrftoken": self.csrf_token,
        }

    def fetch_submission_history(self, limit: int = 200) -> pd.DataFrame:
        print(f"🚀 Fetching last {limit} submissions...")

        query = """
        query Submissions($offset: Int!, $limit: Int!) {
            submissionList(offset: $offset, limit: $limit) {
                hasNext
                submissions {
                    id
                    lang
                    time
                    timestamp
                    statusDisplay
                    memory
                    title
                    titleSlug
                }
            }
        }
        """

        submissions = []
        offset = 0
        batch_size = 20
        auth_failed = False
        pbar = tqdm(total=limit, desc="Fetching submissions")

        while len(submissions) < limit:
            payload = {"query": query, "variables": {"offset": offset, "limit": batch_size}}

            try:
                response = requests.post(self.url, headers=self.headers, json=payload, timeout=30)

                if response.status_code in (401, 403):
                    print(f"\n❌ Auth failed ({response.status_code}). Cookies likely expired.")
                    auth_failed = True
                    break
                if response.status_code == 429:
                    print("\n⚠️ Rate limited. Sleeping 30s and retrying...")
                    time.sleep(30)
                    continue
                if response.status_code != 200:
                    print(f"\n❌ Status {response.status_code}: {response.text[:300]}")
                    break

                data = response.json()
                if "errors" in data:
                    print("\n❌ GraphQL error (cookies likely invalid):")
                    for err in data["errors"]:
                        print(f"   - {err.get('message', err)}")
                    auth_failed = True
                    break

                submission_list = data.get("data", {}).get("submissionList")
                if submission_list is None:
                    print("\n❌ No submissionList in response. Auth likely failed.")
                    auth_failed = True
                    break

                batch = submission_list.get("submissions", [])
                has_next = submission_list.get("hasNext", False)
                if not batch:
                    break

                submissions.extend(batch)
                pbar.update(len(batch))

                if not has_next:
                    break

                offset += batch_size
                time.sleep(random.uniform(1, 3))

            except requests.exceptions.Timeout:
                print("\n❌ Request timed out.")
                break
            except requests.exceptions.ConnectionError:
                print("\n❌ Connection error.")
                break
            except Exception as e:
                print(f"\n❌ Exception: {e}")
                break

        pbar.close()

        if auth_failed and not submissions:
            print(
                "\n💡 TIP: Refresh your cookies:"
                "\n   1. Log in at leetcode.com"
                "\n   2. DevTools → Application → Cookies → leetcode.com"
                "\n   3. Copy LEETCODE_SESSION and csrftoken into your .env"
            )

        print(f"\n✅ Fetched {len(submissions)} submissions.")
        df = pd.DataFrame(submissions)
        if not df.empty and "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
        return df


# =============================================================================
# LEETCODE ENRICHER — fetches problem metadata (difficulty, tags)
# =============================================================================
class LeetCodeEnricher:
    """Fetch problem metadata via the public (unauthenticated) GraphQL endpoint."""

    def __init__(self):
        self.url = LEETCODE_GRAPHQL_URL
        self.headers = DEFAULT_HEADERS

    def get_problem_metadata(self, title_slugs: list) -> pd.DataFrame:
        print(f"🔍 Enriching {len(title_slugs)} problems...")

        query = """
        query questionData($titleSlug: String!) {
            question(titleSlug: $titleSlug) {
                difficulty
                topicTags { name }
            }
        }
        """

        meta_data = []
        pbar = tqdm(total=len(title_slugs), desc="Enriching")

        for slug in title_slugs:
            payload = {"query": query, "variables": {"titleSlug": slug}}
            try:
                response = requests.post(self.url, headers=self.headers, json=payload, timeout=15)
                if response.status_code == 200:
                    q = response.json().get("data", {}).get("question") or {}
                    tags = [t["name"] for t in q.get("topicTags", [])]
                    meta_data.append({
                        "titleSlug": slug,
                        "difficulty": q.get("difficulty", "Unknown"),
                        "tags": ", ".join(tags),
                    })
                elif response.status_code == 429:
                    print("\n⚠️ Rate limited; sleeping 30s")
                    time.sleep(30)
            except Exception as e:
                print(f"\n   ⚠️ {slug}: {e}")
            pbar.update(1)
            time.sleep(random.uniform(0.5, 1.5))

        pbar.close()
        return pd.DataFrame(meta_data)


# =============================================================================
# HISTORY: load / enrich / dedupe
# =============================================================================
def load_latest_history(drive_path: str = DEFAULT_DRIVE_PATH):
    """Return (DataFrame, path) for the most recent enriched history snapshot."""
    pattern = os.path.join(drive_path, "leetcode_history_enriched_*.csv")
    files = [f for f in glob.glob(pattern) if "clean" not in os.path.basename(f)]
    if not files:
        raise FileNotFoundError(f"No leetcode_history_enriched_*.csv files in {drive_path}")
    latest = max(files, key=os.path.getmtime)
    print(f"📂 Using history file: {os.path.basename(latest)}")
    return pd.read_csv(latest), latest


def load_clean_history(drive_path: str = DEFAULT_DRIVE_PATH) -> pd.DataFrame:
    """Return the canonical deduped history (raises if missing)."""
    path = os.path.join(drive_path, CLEAN_HISTORY_FILENAME)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Clean history file not found: {path}")
    return pd.read_csv(path)


def enrich_and_save_history(
    df_new: pd.DataFrame,
    drive_path: str = DEFAULT_DRIVE_PATH,
    enrich_limit: int = 100,
) -> str:
    """Enrich newly-fetched submissions with difficulty/tags, merge with history,
    write a dated snapshot, and return its path."""
    try:
        history_df, _ = load_latest_history(drive_path)
        print(f"📊 Existing history: {len(history_df)} rows")
    except FileNotFoundError:
        print("⚠️ No existing history. Starting fresh.")
        history_df = pd.DataFrame()

    if not history_df.empty:
        history_meta = history_df[["titleSlug", "difficulty", "tags"]].drop_duplicates("titleSlug")
        already_enriched = set(
            history_meta.loc[
                history_meta["difficulty"].notna()
                & (history_meta["difficulty"] != "Unknown"),
                "titleSlug",
            ]
        )
    else:
        history_meta = pd.DataFrame(columns=["titleSlug", "difficulty", "tags"])
        already_enriched = set()

    current_slugs = set(df_new["titleSlug"].dropna().unique())
    slugs_to_enrich = sorted(current_slugs - already_enriched)

    print(f"📈 New-data slugs: {len(current_slugs)}  Already enriched: {len(already_enriched)}  Needing enrichment: {len(slugs_to_enrich)}")

    if slugs_to_enrich:
        if len(slugs_to_enrich) > enrich_limit:
            print(f"⚠️ Capping enrichment at {enrich_limit}")
            slugs_to_enrich = slugs_to_enrich[:enrich_limit]
        meta_new_df = LeetCodeEnricher().get_problem_metadata(slugs_to_enrich)
        meta_updated = pd.concat([history_meta, meta_new_df], ignore_index=True).drop_duplicates(
            "titleSlug", keep="last"
        )
    else:
        meta_updated = history_meta
        print("✅ No new problems need enrichment.")

    df_enriched = df_new.merge(meta_updated, on="titleSlug", how="left")

    if not history_df.empty and "id" in history_df.columns:
        known_ids = set(history_df["id"])
        new_rows = df_enriched[~df_enriched["id"].isin(known_ids)].copy()
        updated_history = pd.concat([history_df, new_rows], ignore_index=True)
    else:
        updated_history = df_enriched

    print(f"📊 Updated history: {len(updated_history)} rows")

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = os.path.join(drive_path, f"leetcode_history_enriched_{ts}.csv")
    updated_history.to_csv(out_path, index=False)
    print(f"💾 Saved: {out_path}")
    return out_path


def deduplicate_and_save_clean(history_path: str, drive_path: str = DEFAULT_DRIVE_PATH) -> str:
    """Dedupe a history snapshot and save it as the canonical clean file."""
    df = pd.read_csv(history_path)
    if "date" in df.columns:
        df = df.sort_values("date", ascending=False)
    df_clean = df.drop_duplicates(subset=["id"], keep="last")
    print(f"📊 Rows: {len(df)} → {len(df_clean)} after dedupe")

    clean_path = os.path.join(drive_path, CLEAN_HISTORY_FILENAME)
    df_clean.to_csv(clean_path, index=False)
    print(f"💾 Clean history saved to: {clean_path}")
    return clean_path


# =============================================================================
# PROBLEM UNIVERSE — NeetCode 150 (+ optional extended dataset)
# =============================================================================
def load_neetcode_150() -> pd.DataFrame:
    """Load the bundled NeetCode 150 problem list."""
    with open(NEETCODE_150_PATH) as f:
        data = json.load(f)
    df = pd.DataFrame(data["problems"])
    df["source"] = "neetcode150"
    return df


def _extract_slug_from_link(url) -> str | None:
    if not isinstance(url, str):
        return None
    m = re.search(r"problems/([^/?#]+)", url)
    return m.group(1) if m else None


def load_extended_dataset(base_path: str = DEFAULT_DRIVE_PATH) -> pd.DataFrame | None:
    """Load the optional Kaggle extended dataset (data/Leetcode.csv) if present.

    Returns a normalized DataFrame with columns: slug, title, difficulty, tags,
    acceptance, isPremium, category, source. Returns None if the file is absent.
    """
    path = os.path.join(base_path, EXTENDED_DATASET_FILENAME)
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path)

    if "Link" not in df.columns:
        print(f"   ⚠️ {EXTENDED_DATASET_FILENAME} has no 'Link' column; ignoring")
        return None

    df["slug"] = df["Link"].apply(_extract_slug_from_link)

    rename_map = {
        "Title": "title",
        "Difficulty": "difficulty",
        "Topics": "tags",
        "Acceptance Rate (%)": "acceptance",
        "Premium Only": "isPremium",
        "Category": "category",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    if "isPremium" in df.columns:
        df["isPremium"] = (
            df["isPremium"].astype(str).str.strip().str.lower().isin(["yes", "true", "1"])
        )
    if "acceptance" in df.columns:
        df["acceptance"] = pd.to_numeric(
            df["acceptance"].astype(str).str.replace("%", "", regex=False),
            errors="coerce",
        )

    before = len(df)
    df = df.dropna(subset=["slug"])
    if before - len(df) > 0:
        print(f"   ⚠️ Dropped {before - len(df)} extended-dataset rows with invalid links")

    keep_cols = ["slug", "title", "difficulty", "tags", "acceptance", "isPremium", "category"]
    df = df[[c for c in keep_cols if c in df.columns]].copy()
    df["source"] = "extended"
    return df


def _parse_acceptance_from_stats(stats_raw) -> float | None:
    if not stats_raw:
        return None
    try:
        s = json.loads(stats_raw) if isinstance(stats_raw, str) else stats_raw
        ar = s.get("acRate", "")
        if isinstance(ar, str) and ar.endswith("%"):
            return float(ar.rstrip("%"))
    except Exception:
        pass
    return None


def enrich_problem_universe(slugs: list[str]) -> pd.DataFrame:
    """Enrich a list of slugs via GraphQL (sequential, jittered, with 429 backoff)."""
    if not slugs:
        return pd.DataFrame()

    print(f"   -> Enriching {len(slugs)} problems via LeetCode GraphQL...")

    query = """
    query questionData($titleSlug: String!) {
        question(titleSlug: $titleSlug) {
            difficulty
            isPaidOnly
            stats
            topicTags { name }
        }
    }
    """

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    enriched = []
    consecutive_failures = 0
    pbar = tqdm(total=len(slugs), desc="Enriching universe")

    for slug in slugs:
        attempt = 0
        success = False
        while attempt < 4 and not success:
            try:
                resp = session.post(
                    LEETCODE_GRAPHQL_URL,
                    json={"query": query, "variables": {"titleSlug": slug}},
                    timeout=15,
                )
                if resp.status_code == 429:
                    backoff = min(60, 4 * (2 ** attempt))
                    print(f"\n   ⚠️ Rate limited on {slug}; backing off {backoff}s")
                    time.sleep(backoff)
                    attempt += 1
                    continue
                if resp.status_code != 200:
                    break

                q = resp.json().get("data", {}).get("question") or {}
                enriched.append({
                    "slug": slug,
                    "difficulty": q.get("difficulty"),
                    "tags": ", ".join(t["name"] for t in q.get("topicTags", [])),
                    "acceptance": _parse_acceptance_from_stats(q.get("stats")),
                    "isPremium": bool(q.get("isPaidOnly", False)),
                })
                consecutive_failures = 0
                success = True
            except requests.exceptions.RequestException:
                attempt += 1
                if attempt < 4:
                    time.sleep(2 ** attempt)

        if not success:
            consecutive_failures += 1

        pbar.update(1)
        time.sleep(random.uniform(0.5, 1.5))

        if consecutive_failures >= 5:
            print("\n   ⚠️ Too many consecutive failures; aborting enrichment early")
            break

    pbar.close()
    return pd.DataFrame(enriched)


def build_problem_universe(
    base_path: str = DEFAULT_DRIVE_PATH,
    include_extended: bool = True,
) -> pd.DataFrame:
    """Build (and cache) the recommendation universe.

    First run: enriches NeetCode 150 via GraphQL.
    Subsequent runs: reads cached universe and only enriches any new slugs
    (e.g. additions from the extended Kaggle dataset).

    If include_extended is False, data/Leetcode.csv is ignored even if present
    (avoids wasted GraphQL calls when NEETCODE150_ONLY is in effect).
    """
    print("\n🌐 BUILDING PROBLEM UNIVERSE...")
    os.makedirs(base_path, exist_ok=True)
    universe_path = os.path.join(base_path, PROBLEM_UNIVERSE_FILENAME)

    cached = None
    if os.path.exists(universe_path):
        cached = pd.read_csv(universe_path)
        print(f"   -> Loaded cached universe: {len(cached)} problems")

    nc150 = load_neetcode_150()
    print(f"   -> NeetCode 150 list: {len(nc150)} problems")

    extended = load_extended_dataset(base_path) if include_extended else None
    if extended is not None:
        print(f"   -> Extended dataset: {len(extended)} problems")
        nc_slugs = set(nc150["slug"])
        extended_only = extended[~extended["slug"].isin(nc_slugs)]
        combined = pd.concat([nc150, extended_only], ignore_index=True, sort=False)
    else:
        combined = nc150.copy()

    for col in ["tags", "acceptance", "isPremium", "category"]:
        if col not in combined.columns:
            combined[col] = pd.NA

    if cached is not None and not cached.empty:
        cache_cols = [c for c in ["slug", "difficulty", "tags", "acceptance", "isPremium"] if c in cached.columns]
        cache_lookup = cached[cache_cols].drop_duplicates("slug").set_index("slug")
        for col in ["difficulty", "tags", "acceptance", "isPremium"]:
            if col in cache_lookup.columns:
                fill = combined["slug"].map(cache_lookup[col])
                combined[col] = combined[col].where(combined[col].notna() & (combined[col].astype(str) != ""), fill)

    needs_enrich_mask = combined["tags"].isna() | (combined["tags"].astype(str).str.strip() == "")
    to_enrich = combined.loc[needs_enrich_mask, "slug"].dropna().unique().tolist()

    if to_enrich:
        print(f"   -> Need GraphQL enrichment: {len(to_enrich)} problems")
        new_data = enrich_problem_universe(to_enrich)
        if not new_data.empty:
            new_lookup = new_data.drop_duplicates("slug").set_index("slug")
            for col in ["difficulty", "tags", "acceptance", "isPremium"]:
                if col in new_lookup.columns:
                    fill = combined["slug"].map(new_lookup[col])
                    combined[col] = combined[col].where(
                        combined[col].notna() & (combined[col].astype(str) != ""), fill
                    )
    else:
        print("   -> Universe already fully enriched")

    combined.to_csv(universe_path, index=False)
    print(f"   -> Saved universe ({len(combined)} problems) → {universe_path}")
    return combined


# =============================================================================
# LEETCODE SMART RECOMMENDER — generates the personalized workout
# =============================================================================
class LeetCodeSmartRecommender:
    """Score and rank problems using:
      - Tag weakness (low success rate on certain topics)
      - Spaced repetition (optimal review timing for previously-solved problems)
      - Difficulty preference (mild medium-heavy bias)
    """

    def __init__(
        self,
        base_path: str = DEFAULT_DRIVE_PATH,
        review_percentage: int = 70,
        allow_premium: bool = False,
        neetcode_150_only: bool = True,
    ):
        self.base_path = base_path
        self.review_percentage = max(1, min(100, review_percentage))
        self.allow_premium = allow_premium
        self.neetcode_150_only = neetcode_150_only

        self.df_universe = pd.DataFrame()
        self.df_hist = pd.DataFrame()
        self.problem_stats: dict = {}
        self.tag_weakness: dict = {}
        self.df_final = pd.DataFrame()

    def load_data(self) -> bool:
        print("⚙️ INITIALIZING RECOMMENDER...")
        print(
            f"   -> Mix: {self.review_percentage}% reviews / {100 - self.review_percentage}% new"
            f"  |  premium={self.allow_premium}  |  neetcode_150_only={self.neetcode_150_only}"
        )

        universe_path = os.path.join(self.base_path, PROBLEM_UNIVERSE_FILENAME)
        if not os.path.exists(universe_path):
            print(f"❌ Problem universe missing: {universe_path}")
            print("   Run build_problem_universe() first.")
            return False
        self.df_universe = pd.read_csv(universe_path)

        if not self.allow_premium and "isPremium" in self.df_universe.columns:
            premium = self.df_universe["isPremium"].fillna(False).astype(bool)
            self.df_universe = self.df_universe[~premium]

        if self.neetcode_150_only and "source" in self.df_universe.columns:
            self.df_universe = self.df_universe[self.df_universe["source"] == "neetcode150"]

        self.df_universe = self.df_universe.drop_duplicates(subset="slug", keep="first").reset_index(drop=True)

        hist_path = os.path.join(self.base_path, CLEAN_HISTORY_FILENAME)
        if os.path.exists(hist_path):
            self.df_hist = pd.read_csv(hist_path)
            if "titleSlug" in self.df_hist.columns and "slug" not in self.df_hist.columns:
                self.df_hist = self.df_hist.rename(columns={"titleSlug": "slug"})
            if "date" in self.df_hist.columns:
                self.df_hist["date_dt"] = pd.to_datetime(self.df_hist["date"], errors="coerce")
            elif "timestamp" in self.df_hist.columns:
                self.df_hist["date_dt"] = pd.to_datetime(
                    self.df_hist["timestamp"].astype(int), unit="s", errors="coerce"
                )
            else:
                self.df_hist["date_dt"] = pd.NaT
            if "tags" not in self.df_hist.columns:
                self.df_hist["tags"] = ""
            self.df_hist["tags"] = self.df_hist["tags"].fillna("").astype(str)
        else:
            print("   -> No prior history (cold start)")

        self._compute_problem_stats()
        self._compute_tag_weakness()

        solved = set(self.df_hist["slug"].dropna().unique()) if not self.df_hist.empty else set()
        self.df_universe["is_review"] = self.df_universe["slug"].isin(solved)

        print(
            f"   -> Universe: {len(self.df_universe)} problems"
            f" ({self.df_universe['is_review'].sum()} reviews, "
            f"{(~self.df_universe['is_review']).sum()} new)"
        )
        if not self.df_hist.empty:
            print(f"   -> History: {len(self.df_hist)} submissions ({len(solved)} unique)")
        return True

    def _compute_problem_stats(self) -> None:
        if self.df_hist.empty:
            return
        now = pd.Timestamp.now()
        for slug, grp in self.df_hist.dropna(subset=["slug"]).groupby("slug"):
            most_recent = grp["date_dt"].max()
            days_since = (now - most_recent).days if pd.notna(most_recent) else 999
            attempts = len(grp)
            accepted = (grp["statusDisplay"] == "Accepted").sum()
            self.problem_stats[slug] = {
                "days_since_solve": days_since,
                "success_rate": accepted / attempts if attempts else 0,
                "attempts": attempts,
                "accepted": int(accepted),
            }

    def _compute_tag_weakness(self) -> None:
        if self.df_hist.empty:
            return
        exploded = self.df_hist.assign(tag=self.df_hist["tags"].str.split(", ")).explode("tag")
        exploded["tag"] = exploded["tag"].fillna("").str.strip()
        exploded = exploded[exploded["tag"] != ""]
        if exploded.empty:
            return
        success = exploded.groupby("tag")["statusDisplay"].apply(lambda x: (x == "Accepted").mean())
        self.tag_weakness = {tag: 1.0 - rate for tag, rate in success.items()}

    def _generate_candidates(self) -> pd.DataFrame:
        if self.df_hist.empty:
            return self.df_universe.copy()
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
        fresh = self.df_hist[
            (self.df_hist["statusDisplay"] == "Accepted") & (self.df_hist["date_dt"] > cutoff)
        ]["slug"].dropna().unique()
        if len(fresh):
            print(f"   -> Excluding {len(fresh)} problems solved in last 7 days")
        return self.df_universe[~self.df_universe["slug"].isin(fresh)].copy()

    def _score_row(self, row) -> float:
        score = 0.0
        is_review = bool(row.get("is_review", False))

        tags_str = str(row.get("tags", "") or "")
        for tag in (t.strip() for t in tags_str.split(",")):
            if tag and tag in self.tag_weakness:
                score += self.tag_weakness[tag] * 25

        diff = str(row.get("difficulty", "") or "").strip()
        score += {"Easy": 5, "Medium": 10, "Hard": 8}.get(diff, 5)

        if is_review:
            stats = self.problem_stats.get(row["slug"], {})
            days = stats.get("days_since_solve", 999)
            success = stats.get("success_rate", 1.0)

            if days < 7:
                time_boost = -50  # filtered out earlier; defensive
            elif days < 14:
                time_boost = 5
            elif days < 30:
                time_boost = 15 + (days - 14) * 0.5
            elif days < 90:
                time_boost = 25 + (days - 30) * 0.1
            else:
                time_boost = 35
            score += time_boost
            score += (1.0 - success) * 20
        else:
            score += 12

        score += float(np.random.normal(0, 2))
        return score

    def rank(self, candidates: pd.DataFrame, top_k: int = 10) -> pd.DataFrame:
        if candidates.empty:
            print("⚠️ No candidates to rank")
            return pd.DataFrame()

        candidates = candidates.copy()
        candidates["Final_Score"] = candidates.apply(self._score_row, axis=1)
        candidates = candidates.drop_duplicates(subset="slug", keep="first")

        n_reviews_target = int(top_k * self.review_percentage / 100)
        n_new_target = top_k - n_reviews_target

        reviews = candidates[candidates["is_review"]].sort_values("Final_Score", ascending=False)
        new = candidates[~candidates["is_review"]].sort_values("Final_Score", ascending=False)

        selected_reviews = reviews.head(n_reviews_target)
        selected_new = new.head(n_new_target)

        if len(selected_reviews) < n_reviews_target:
            shortfall = n_reviews_target - len(selected_reviews)
            selected_new = pd.concat([selected_new, new.iloc[n_new_target : n_new_target + shortfall]])
        if len(selected_new) < n_new_target:
            shortfall = n_new_target - len(selected_new)
            selected_reviews = pd.concat(
                [selected_reviews, reviews.iloc[n_reviews_target : n_reviews_target + shortfall]]
            )

        top = pd.concat([selected_reviews, selected_new]).sort_values("Final_Score", ascending=False)
        self.df_final = top
        print(
            f"   -> Selected {len(top)}: "
            f"{int(top['is_review'].sum())} reviews, {int((~top['is_review']).sum())} new"
        )
        return top

    def display(self, top: pd.DataFrame) -> None:
        print("\n" + "=" * 70)
        print(f"🚀 YOUR PERSONALIZED WORKOUT (Top {len(top)})")
        print("=" * 70)
        for _, row in top.iterrows():
            slug = row["slug"]
            is_review = bool(row.get("is_review", False))
            stats = self.problem_stats.get(slug, {})

            if is_review:
                days = stats.get("days_since_solve", 0)
                success = stats.get("success_rate", 1.0)
                label = f"🧠 Review ({days}d ago, {success:.0%} success)"
            else:
                label = "🆕 New"

            diff = row.get("difficulty") or "Unknown"
            title = row.get("title") or slug.replace("-", " ").title()
            acc = row.get("acceptance")
            acc_str = f"{acc:.1f}%" if isinstance(acc, (int, float)) and not pd.isna(acc) else "N/A"

            print(f"[{diff}] {title} | {label}")
            print(f"    Acc: {acc_str}  |  Tags: {row.get('tags', '')}")
            print(f"    Score: {row['Final_Score']:.1f}")
            print(f"    Link: https://leetcode.com/problems/{slug}/")
            print("-" * 70)

    def run(self, top_k: int = 10) -> pd.DataFrame:
        if not self.load_data():
            return pd.DataFrame()
        candidates = self._generate_candidates()
        top = self.rank(candidates, top_k=top_k)
        if not top.empty:
            self.display(top)
        return top


# =============================================================================
# WORKOUT PLAN EXPORT
# =============================================================================
def save_workout_plan(
    recommender: LeetCodeSmartRecommender,
    drive_path: str = DEFAULT_DRIVE_PATH,
    top_n: int = 10,
) -> str:
    if recommender.df_final.empty:
        print("❌ No recommendations available.")
        return ""

    top = recommender.df_final.head(top_n)
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(drive_path, f"leetcode_workout_plan_{date_str}.txt")

    lines = [
        "=" * 60,
        f"🚀 YOUR PERSONALIZED LEETCODE PLAN ({date_str})",
        "=" * 60,
        f"Mix: {recommender.review_percentage}% reviews / {100 - recommender.review_percentage}% new",
        "Scoring: Personal Weaknesses + Spaced Repetition\n",
    ]

    for i, row in enumerate(top.itertuples(), 1):
        slug = row.slug
        is_review = bool(getattr(row, "is_review", False))
        stats = recommender.problem_stats.get(slug, {})

        if is_review:
            days = stats.get("days_since_solve", 0)
            success = stats.get("success_rate", 1.0)
            type_label = f"🧠 Review ({days}d ago, {success:.0%} success)"
        else:
            type_label = "🆕 New Skill"

        diff = getattr(row, "difficulty", None) or "Unknown"
        title = getattr(row, "title", None) or slug.replace("-", " ").title()
        acc = getattr(row, "acceptance", None)
        acc_str = f"{acc:.1f}%" if isinstance(acc, (int, float)) and not pd.isna(acc) else "N/A"

        lines.append(f"{i}. [{diff}] {title}")
        lines.append(f"    Type:  {type_label}")
        lines.append(f"    Acc:   {acc_str}")
        lines.append(f"    Tags:  {getattr(row, 'tags', '') or ''}")
        lines.append(f"    Score: {row.Final_Score:.1f}")
        lines.append(f"    Link:  https://leetcode.com/problems/{slug}/")
        lines.append("-" * 60)

    lines.append("\nGood luck! Re-run anytime to update based on new submissions.")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Workout plan saved to: {output_path}")
    return output_path


# =============================================================================
# ANALYTICS VISUALIZATION
# =============================================================================
def generate_analytics_jpeg(drive_path: str = DEFAULT_DRIVE_PATH) -> str:
    """Generate a 4-panel analytics chart from the clean history file."""
    import matplotlib.pyplot as plt
    import seaborn as sns

    print("🎨 GENERATING ANALYTICS CHART...")
    hist_path = os.path.join(drive_path, CLEAN_HISTORY_FILENAME)
    if not os.path.exists(hist_path):
        print("❌ No history file found.")
        return ""

    df = pd.read_csv(hist_path)
    if df.empty:
        print("❌ Empty history file.")
        return ""

    df["is_accepted"] = (df["statusDisplay"] == "Accepted").astype(int)

    df_tags = df.copy()
    df_tags["tags"] = df_tags["tags"].fillna("").astype(str)
    df_tags = df_tags[df_tags["tags"] != ""]
    df_tags = df_tags.assign(tag=df_tags["tags"].str.split(", ")).explode("tag")
    df_tags = df_tags[df_tags["tag"] != ""]

    sns.set_theme(style="whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    plt.subplots_adjust(hspace=0.4, wspace=0.3)

    # Chart 1: Difficulty Success
    ax1 = axes[0, 0]
    diff_df = df[df["difficulty"].isin(["Easy", "Medium", "Hard"])]
    if not diff_df.empty:
        diff_stats = diff_df.groupby("difficulty")["is_accepted"].mean().reset_index()
        diff_order = ["Easy", "Medium", "Hard"]
        diff_stats["difficulty"] = pd.Categorical(diff_stats["difficulty"], categories=diff_order, ordered=True)
        diff_stats = diff_stats.sort_values("difficulty")
        color_map = {"Easy": "#66c2a5", "Medium": "#fdae61", "Hard": "#d53e4f"}
        sns.barplot(data=diff_stats, x="difficulty", y="is_accepted", hue="difficulty",
                    palette=color_map, legend=False, ax=ax1)
        ax1.set_title("Success Rate by Difficulty", fontsize=14, fontweight="bold")
        ax1.set_ylim(0, 1.1)
        for i, row in enumerate(diff_stats.itertuples()):
            ax1.text(i, row.is_accepted + 0.02, f"{row.is_accepted:.1%}", ha="center")
    else:
        ax1.text(0.5, 0.5, "No difficulty data", ha="center", transform=ax1.transAxes)

    # Chart 2: Weakest Tags
    ax2 = axes[0, 1]
    if not df_tags.empty:
        tag_groups = df_tags.groupby("tag").agg(
            attempts=("id", "count"), success_rate=("is_accepted", "mean")
        ).reset_index()
        valid_tags = tag_groups[tag_groups["attempts"] >= 3]
        weakest_tags = valid_tags.sort_values("success_rate").head(8)
        if not weakest_tags.empty:
            sns.barplot(data=weakest_tags, y="tag", x="success_rate", hue="tag",
                        palette="magma", legend=False, ax=ax2)
            ax2.set_title("⚠️ Weakest Topics (Low Acceptance)", fontsize=14, fontweight="bold")
            ax2.set_xlim(0, 1.0)
        else:
            ax2.text(0.5, 0.5, "Not enough data (>3 attempts)", ha="center", transform=ax2.transAxes)
    else:
        ax2.text(0.5, 0.5, "No tag data", ha="center", transform=ax2.transAxes)

    # Chart 3: Grind Factor
    ax3 = axes[1, 0]
    if "titleSlug" in df.columns:
        attempts_capped = df.groupby("titleSlug").size().apply(lambda x: min(x, 10))
        sns.histplot(attempts_capped, bins=range(1, 12), kde=False, color="royalblue",
                     ax=ax3, discrete=True)
        ax3.set_title("Grind Factor (Attempts per Problem)", fontsize=14, fontweight="bold")
        ax3.set_xlabel("Attempts")
        ax3.set_ylabel("Count")
    else:
        ax3.text(0.5, 0.5, "No attempt data", ha="center", transform=ax3.transAxes)

    # Chart 4: Most Practiced
    ax4 = axes[1, 1]
    if not df_tags.empty:
        tag_groups = df_tags.groupby("tag").agg(attempts=("id", "count")).reset_index()
        top_tags = tag_groups.sort_values("attempts", ascending=False).head(8)
        sns.barplot(data=top_tags, y="tag", x="attempts", hue="tag",
                    palette="viridis", legend=False, ax=ax4)
        ax4.set_title("Most Practiced Topics", fontsize=14, fontweight="bold")
    else:
        ax4.text(0.5, 0.5, "No tag data", ha="center", transform=ax4.transAxes)

    plt.tight_layout()
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(drive_path, f"leetcode_analytics_{date_str}.jpg")
    plt.savefig(output_path, format="jpg", dpi=300)
    plt.close()
    print(f"✅ Analytics saved to: {output_path}")
    return output_path


# =============================================================================
# FULL PIPELINE
# =============================================================================
def run_full_workout_pipeline(
    leetcode_session: str = None,
    csrf_token: str = None,
    drive_path: str = DEFAULT_DRIVE_PATH,
    fetch_limit: int = 60,
    first_run_fetch_limit: int = 500,
    enrich_limit: int = 100,
    top_k: int = 10,
    skip_fetch: bool = False,
    review_percentage: int = 70,
    allow_premium: bool = False,
    neetcode_150_only: bool = True,
) -> dict:
    """Run the full pipeline: universe → fetch → enrich → recommend → save → analytics."""
    results: dict = {}

    print("\n" + "=" * 60)
    print("STEP 1: PROBLEM UNIVERSE")
    print("=" * 60)
    build_problem_universe(drive_path, include_extended=not neetcode_150_only)
    results["universe_path"] = os.path.join(drive_path, PROBLEM_UNIVERSE_FILENAME)

    clean_path = os.path.join(drive_path, CLEAN_HISTORY_FILENAME)
    is_first_run = not os.path.exists(clean_path)
    actual_fetch_limit = first_run_fetch_limit if is_first_run else fetch_limit
    if is_first_run:
        print(f"\n📭 First run detected — fetching {actual_fetch_limit} submissions for profile bootstrap")

    if not skip_fetch:
        if not leetcode_session or not csrf_token:
            print("⚠️ Skipping fetch: no credentials provided")
        else:
            print("\n" + "=" * 60)
            print(f"STEP 2: FETCHING SUBMISSIONS ({actual_fetch_limit})")
            print("=" * 60)
            df_new = LeetCodeFetcher(leetcode_session, csrf_token).fetch_submission_history(
                limit=actual_fetch_limit
            )

            if df_new.empty:
                print("\n" + "!" * 60)
                print("⚠️ WARNING: No submissions fetched.")
                print("!" * 60)
                print("Possible causes: expired cookies, rate limiting, or network.")
                print("Continuing with EXISTING history (if any).\n")
            else:
                print("\n" + "=" * 60)
                print("STEP 3: ENRICHING HISTORY")
                print("=" * 60)
                history_path = enrich_and_save_history(df_new, drive_path, enrich_limit)
                results["history_path"] = history_path
                results["clean_path"] = deduplicate_and_save_clean(history_path, drive_path)

    print("\n" + "=" * 60)
    print("STEP 4: RECOMMENDATIONS")
    print("=" * 60)
    recommender = LeetCodeSmartRecommender(
        base_path=drive_path,
        review_percentage=review_percentage,
        allow_premium=allow_premium,
        neetcode_150_only=neetcode_150_only,
    )
    recommender.run(top_k=top_k)

    results["workout_path"] = save_workout_plan(recommender, drive_path, top_n=top_k)

    print("\n" + "=" * 60)
    print("STEP 5: ANALYTICS")
    print("=" * 60)
    results["analytics_path"] = generate_analytics_jpeg(drive_path)

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE")
    print("=" * 60)
    for k, v in results.items():
        if v:
            print(f"   {k}: {os.path.basename(v)}")
    return results
