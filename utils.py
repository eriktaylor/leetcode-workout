"""
LeetCode Workout Utils
======================
Core utilities for fetching, enriching, harmonizing, and recommending LeetCode problems.

This module contains all the heavy lifting functions for the LeetCode workout system.
Keep this file next to leetcode_workout.py in your project root.
"""

import os
import re
import glob
import time
import random
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from tqdm import tqdm

# =============================================================================
# CONFIGURATION
# =============================================================================
DEFAULT_DRIVE_PATH = "data"  # relative to CWD; the CLI overrides this via LEETCODE_DATA_DIR
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


# =============================================================================
# LEETCODE FETCHER - Downloads your submission history
# =============================================================================
class LeetCodeFetcher:
    """
    Fetches your personal submission history from LeetCode using their GraphQL API.
    Requires LEETCODE_SESSION and CSRF_TOKEN from browser cookies.
    """

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
        """
        Fetches the last N submissions.
        
        Args:
            limit: Maximum number of submissions to fetch (default 200)
            
        Returns:
            DataFrame with submission history
        """
        print(f"🚀 Starting fetch for last {limit} submissions...")

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
        batch_size = 20  # LeetCode default limit
        auth_failed = False

        pbar = tqdm(total=limit, desc="Fetching submissions")

        while len(submissions) < limit:
            payload = {
                "query": query,
                "variables": {"offset": offset, "limit": batch_size},
            }

            try:
                response = requests.post(self.url, headers=self.headers, json=payload, timeout=30)

                if response.status_code == 401:
                    print("\n❌ Authentication failed (401). Your cookies have expired.")
                    auth_failed = True
                    break
                elif response.status_code == 403:
                    print("\n❌ Access forbidden (403). Possible rate limiting or invalid session.")
                    auth_failed = True
                    break
                elif response.status_code != 200:
                    print(f"\n❌ Error: Status Code {response.status_code}")
                    print(response.text[:500] if response.text else "No response body")
                    break

                data = response.json()

                if "errors" in data:
                    print("\n❌ GraphQL Error (usually means expired/invalid cookies):")
                    for err in data["errors"]:
                        print(f"   - {err.get('message', err)}")
                    auth_failed = True
                    break

                submission_list = data.get("data", {}).get("submissionList")
                
                if submission_list is None:
                    print("\n❌ No submissionList in response. Authentication likely failed.")
                    auth_failed = True
                    break
                
                batch = submission_list.get("submissions", [])
                has_next = submission_list.get("hasNext", False)

                if not batch:
                    if offset == 0:
                        # First request returned no data
                        print("\n⚠️ No submissions returned on first request.")
                    break

                submissions.extend(batch)
                pbar.update(len(batch))

                if not has_next:
                    break

                offset += batch_size
                time.sleep(random.uniform(1, 3))

            except requests.exceptions.Timeout:
                print("\n❌ Request timed out. LeetCode may be slow or blocking requests.")
                break
            except requests.exceptions.ConnectionError:
                print("\n❌ Connection error. Check your internet connection.")
                break
            except Exception as e:
                print(f"\n❌ Exception occurred: {e}")
                break

        pbar.close()
        
        if auth_failed and len(submissions) == 0:
            print("\n💡 TIP: Get fresh cookies from your browser:")
            print("   1. Go to leetcode.com and make sure you're logged in")
            print("   2. Open DevTools (F12) → Application tab → Cookies → leetcode.com")
            print("   3. Copy the values for LEETCODE_SESSION and csrftoken")
            print("   4. Update your local .env file and re-run.")
        
        print(f"\n✅ Fetched {len(submissions)} raw submissions.")
        
        df = pd.DataFrame(submissions)
        if not df.empty and "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
        
        return df


# =============================================================================
# LEETCODE ENRICHER - Fetches problem metadata (difficulty, tags)
# =============================================================================
class LeetCodeEnricher:
    """
    Enriches problems with metadata (difficulty, tags) from LeetCode's public API.
    Does not require authentication.
    """

    def __init__(self):
        self.url = LEETCODE_GRAPHQL_URL
        self.headers = DEFAULT_HEADERS

    def get_problem_metadata(self, title_slugs: list) -> pd.DataFrame:
        """
        Fetches difficulty and tags for a list of problem slugs.
        
        Args:
            title_slugs: List of problem slugs to enrich
            
        Returns:
            DataFrame with slug, difficulty, and tags columns
        """
        print(f"🔍 Fetching metadata for {len(title_slugs)} unique problems...")

        query = """
        query questionData($titleSlug: String!) {
            question(titleSlug: $titleSlug) {
                difficulty
                topicTags {
                    name
                }
            }
        }
        """

        meta_data = []
        pbar = tqdm(total=len(title_slugs), desc="Enriching problems")

        for slug in title_slugs:
            payload = {"query": query, "variables": {"titleSlug": slug}}

            try:
                response = requests.post(self.url, headers=self.headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    q_data = data.get("data", {}).get("question", {})

                    if q_data:
                        tags = [t["name"] for t in q_data.get("topicTags", [])]
                        meta_data.append({
                            "titleSlug": slug,
                            "difficulty": q_data.get("difficulty", "Unknown"),
                            "tags": ", ".join(tags),
                        })
                    else:
                        meta_data.append({"titleSlug": slug, "difficulty": "Unknown", "tags": ""})
                else:
                    print(f"Error fetching {slug}: {response.status_code}")

            except Exception as e:
                print(f"Exception for {slug}: {e}")

            pbar.update(1)
            time.sleep(random.uniform(0.5, 1.5))

        pbar.close()
        return pd.DataFrame(meta_data)


# =============================================================================
# DATA LOADING & SAVING UTILITIES
# =============================================================================
def load_latest_history(drive_path: str = DEFAULT_DRIVE_PATH) -> pd.DataFrame:
    """Load the latest enriched history file."""
    pattern = os.path.join(drive_path, "leetcode_history_enriched_*.csv")
    files = glob.glob(pattern)

    if not files:
        raise FileNotFoundError(f"No leetcode_history_enriched_*.csv files found in {drive_path}")

    latest_file = max(files, key=os.path.getmtime)
    print(f"📂 Using history file: {os.path.basename(latest_file)}")
    return pd.read_csv(latest_file), latest_file


def load_clean_history(drive_path: str = DEFAULT_DRIVE_PATH) -> pd.DataFrame:
    """Load the canonical clean history file."""
    clean_path = os.path.join(drive_path, "leetcode_history_enriched_clean.csv")
    if os.path.exists(clean_path):
        return pd.read_csv(clean_path)
    raise FileNotFoundError(f"Clean history file not found: {clean_path}")


def enrich_and_save_history(
    df_new: pd.DataFrame,
    drive_path: str = DEFAULT_DRIVE_PATH,
    enrich_limit: int = 100
) -> str:
    """
    Enriches new submissions and merges with existing history.
    
    Args:
        df_new: DataFrame with new submissions (must have 'titleSlug' column)
        drive_path: Path to the leetcode folder
        enrich_limit: Maximum number of new problems to enrich
        
    Returns:
        Path to the saved enriched history file
    """
    # Load existing history
    try:
        history_df, _ = load_latest_history(drive_path)
        print(f"📊 Loaded existing history: {len(history_df)} rows")
    except FileNotFoundError:
        print("⚠️ No existing history found. Starting fresh.")
        history_df = pd.DataFrame()

    # Determine which slugs need enrichment
    if not history_df.empty:
        history_meta = history_df[["titleSlug", "difficulty", "tags"]].drop_duplicates("titleSlug")
        already_enriched = set(
            history_meta.loc[
                history_meta["difficulty"].notna() & (history_meta["difficulty"] != "Unknown"),
                "titleSlug"
            ]
        )
    else:
        history_meta = pd.DataFrame(columns=["titleSlug", "difficulty", "tags"])
        already_enriched = set()

    current_slugs = set(df_new["titleSlug"].dropna().unique())
    slugs_to_enrich = sorted(current_slugs - already_enriched)

    print(f"📈 Total slugs in new data: {len(current_slugs)}")
    print(f"✅ Already enriched: {len(already_enriched)}")
    print(f"🆕 Needing enrichment: {len(slugs_to_enrich)}")

    # Enrich new problems
    if slugs_to_enrich:
        if len(slugs_to_enrich) > enrich_limit:
            print(f"⚠️ Limiting enrichment to {enrich_limit} problems")
            slugs_to_enrich = slugs_to_enrich[:enrich_limit]

        enricher = LeetCodeEnricher()
        meta_new_df = enricher.get_problem_metadata(slugs_to_enrich)

        # Update metadata
        meta_updated = pd.concat([history_meta, meta_new_df], ignore_index=True)
        meta_updated = meta_updated.drop_duplicates("titleSlug", keep="last")
    else:
        meta_updated = history_meta
        print("✅ No new problems need enrichment.")

    # Merge with new submissions
    df_enriched = df_new.merge(meta_updated, on="titleSlug", how="left")

    # Append only truly new rows
    if not history_df.empty and "id" in history_df.columns:
        known_ids = set(history_df["id"])
        new_rows = df_enriched[~df_enriched["id"].isin(known_ids)].copy()
        updated_history = pd.concat([history_df, new_rows], ignore_index=True)
    else:
        updated_history = df_enriched

    print(f"📊 Updated history size: {len(updated_history)}")

    # Save with timestamp
    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = os.path.join(drive_path, f"leetcode_history_enriched_{timestamp_str}.csv")
    updated_history.to_csv(out_path, index=False)
    print(f"💾 Saved to: {out_path}")

    return out_path


def deduplicate_and_save_clean(
    history_path: str,
    drive_path: str = DEFAULT_DRIVE_PATH
) -> str:
    """
    Deduplicates the history file and saves a clean canonical version.
    
    Args:
        history_path: Path to the history file to deduplicate
        drive_path: Path to save the clean file
        
    Returns:
        Path to the clean history file
    """
    df = pd.read_csv(history_path)
    
    # Sort by date descending, then dedupe on id
    if "date" in df.columns:
        df_sorted = df.sort_values(["date"], ascending=False)
    else:
        df_sorted = df
    
    df_clean = df_sorted.drop_duplicates(subset=["id"], keep="last")

    print(f"📊 Rows before: {len(df)}")
    print(f"📊 Rows after dedupe: {len(df_clean)}")
    print(f"📊 Unique ids: {df_clean['id'].nunique()}")

    clean_path = os.path.join(drive_path, "leetcode_history_enriched_clean.csv")
    df_clean.to_csv(clean_path, index=False)
    print(f"💾 Clean history saved to: {clean_path}")

    return clean_path


# =============================================================================
# LEETCODE MERGE VALIDATOR - Harmonizes multiple data sources
# =============================================================================
class LeetCodeMergeValidator:
    """
    Merges and validates data from multiple sources:
    - Personal history (your submissions)
    - Company tags (which companies ask each problem)
    - Kaggle metadata (problem descriptions, acceptance rates)
    """

    def __init__(self, drive_path: str = DEFAULT_DRIVE_PATH):
        self.drive_path = drive_path
        self.df_history = None
        self.df_company = None
        self.df_kaggle = None
        self.df_master = None

    def load_files(self) -> bool:
        """Load all data sources."""
        print("📂 LOADING DATASETS...")

        # History
        hist_path = os.path.join(self.drive_path, "leetcode_history_enriched_clean.csv")
        if os.path.exists(hist_path):
            self.df_history = pd.read_csv(hist_path)
            print(f"   -> History: {len(self.df_history)} rows")
        else:
            print("❌ History missing")
            return False

        # Company tags
        comp_path = os.path.join(self.drive_path, "leetcode_problem_company_tags.csv")
        if os.path.exists(comp_path):
            self.df_company = pd.read_csv(comp_path)
            print(f"   -> Company: {len(self.df_company)} rows")
        else:
            print("⚠️ Company tags missing (optional)")
            self.df_company = pd.DataFrame()

        # Kaggle metadata
        kag_path = os.path.join(self.drive_path, "leetcode_questions_kaggle.csv")
        if os.path.exists(kag_path):
            self.df_kaggle = pd.read_csv(kag_path)
            print(f"   -> Kaggle:  {len(self.df_kaggle)} rows")
        else:
            print("⚠️ Kaggle metadata missing (optional)")
            self.df_kaggle = pd.DataFrame()

        return self.df_history is not None

    def _extract_slug(self, url):
        """Extract slug from LeetCode URL."""
        if not isinstance(url, str):
            return None
        match = re.search(r"problems/([^/]+)", url)
        return match.group(1) if match else None

    def harmonize_keys(self):
        """Harmonize key columns across datasets."""
        print("\n🔑 HARMONIZING KEYS (Slugs)...")

        # History: titleSlug -> slug
        if "titleSlug" in self.df_history.columns:
            self.df_history.rename(columns={"titleSlug": "slug"}, inplace=True)

        # Kaggle: Question_Link -> slug
        if not self.df_kaggle.empty and "Question_Link" in self.df_kaggle.columns:
            self.df_kaggle["slug"] = self.df_kaggle["Question_Link"].apply(self._extract_slug)

        # Add source flags
        self.df_history["src_history"] = 1
        if not self.df_company.empty:
            self.df_company["src_company"] = 1
        if not self.df_kaggle.empty:
            self.df_kaggle["src_kaggle"] = 1

    def execute_merge(self):
        """Execute the master merge."""
        print("\n🔗 EXECUTING MASTER MERGE...")

        # Start with history as base
        self.df_master = self.df_history.copy()

        # Merge with Kaggle if available
        if not self.df_kaggle.empty and "slug" in self.df_kaggle.columns:
            self.df_master = pd.merge(
                self.df_master,
                self.df_kaggle,
                on="slug",
                how="outer",
                suffixes=("", "_kag"),
            )

        # Merge with Company if available
        if not self.df_company.empty and "slug" in self.df_company.columns:
            self.df_master = pd.merge(
                self.df_master,
                self.df_company,
                on="slug",
                how="outer",
                suffixes=("", "_com"),
            )

        print(f"   -> Total Rows in Master: {len(self.df_master)}")

        # Fill source flags
        for col in ["src_history", "src_company", "src_kaggle"]:
            if col in self.df_master.columns:
                self.df_master[col] = self.df_master[col].fillna(0).astype(int)

    def validate(self):
        """Validate and display merge statistics."""
        print("\n🔍 VALIDATION: Source Overlap Matrix")
        
        cols = [c for c in ["src_kaggle", "src_company", "src_history"] if c in self.df_master.columns]
        if cols:
            overlap = self.df_master.groupby(cols).size().reset_index(name="Count")
            print(overlap.to_string(index=False))

    def save_master(self) -> str:
        """Save the master merged file."""
        out_path = os.path.join(self.drive_path, "leetcode_master_merged_raw.csv")
        self.df_master.to_csv(out_path, index=False)
        print(f"\n💾 Saved master merge to: {out_path}")
        return out_path

    def run(self) -> pd.DataFrame:
        """Run the full merge pipeline."""
        if self.load_files():
            self.harmonize_keys()
            self.execute_merge()
            self.validate()
            self.save_master()
            return self.df_master
        return pd.DataFrame()


# =============================================================================
# LEETCODE SMART RECOMMENDER - Generates personalized workout
# =============================================================================
class LeetCodeSmartRecommender:
    """
    Generates personalized problem recommendations based on:
    - Company frequency (how often the problem is asked)
    - Personal weaknesses (tags you struggle with)
    - Spaced repetition (optimal review timing)
    - Desirable difficulty (mix of new challenges and reviews)
    """

    def __init__(self, base_path: str = DEFAULT_DRIVE_PATH, review_percentage: int = 80):
        """
        Args:
            base_path: Path to the leetcode data folder
            review_percentage: 1-100, percentage of workout that should be review problems.
                              Default 80 means 80% reviews, 20% new problems.
                              Based on learning science suggesting ~85% accuracy is optimal.
        """
        self.base_path = base_path
        self.url = LEETCODE_GRAPHQL_URL
        self.headers = DEFAULT_HEADERS
        self.df_master = None
        self.df_hist = None
        self.df_candidates = pd.DataFrame()
        self.review_percentage = max(1, min(100, review_percentage))  # Clamp to 1-100
        
        # Computed during load_data
        self.problem_stats = {}  # slug -> {days_since_solve, success_rate, attempts}

    def load_data(self) -> bool:
        """Load master universe and history, compute problem statistics."""
        print("⚙️ INITIALIZING SMART RECOMMENDER...")
        print(f"   -> Review/New ratio: {self.review_percentage}% reviews, {100-self.review_percentage}% new")

        # Load Master
        master_path = os.path.join(self.base_path, "leetcode_master_merged_raw.csv")
        if not os.path.exists(master_path):
            print(f"❌ Master file missing: {master_path}")
            return False

        self.df_master = pd.read_csv(master_path)

        if "slug" not in self.df_master.columns:
            raise ValueError("Master file must contain 'slug' column.")

        # Dedupe master
        if "n_companies" in self.df_master.columns:
            self.df_master["n_companies"] = self.df_master["n_companies"].fillna(0)
            self.df_master = (
                self.df_master.sort_values(["n_companies"], ascending=False)
                .drop_duplicates(subset="slug", keep="first")
                .reset_index(drop=True)
            )
        else:
            self.df_master["n_companies"] = 0
            self.df_master = self.df_master.drop_duplicates(subset="slug", keep="first")

        # Load History
        hist_path = os.path.join(self.base_path, "leetcode_history_enriched_clean.csv")
        if not os.path.exists(hist_path):
            print(f"❌ History file missing: {hist_path}")
            return False

        self.df_hist = pd.read_csv(hist_path)

        # Harmonize slug
        if "titleSlug" in self.df_hist.columns and "slug" not in self.df_hist.columns:
            self.df_hist.rename(columns={"titleSlug": "slug"}, inplace=True)

        # Process dates - use pandas Timestamp for proper comparison
        if "date" in self.df_hist.columns:
            self.df_hist["date_dt"] = pd.to_datetime(self.df_hist["date"], errors="coerce")
        elif "timestamp" in self.df_hist.columns:
            self.df_hist["date_dt"] = pd.to_datetime(
                self.df_hist["timestamp"].astype(int), unit="s", errors="coerce"
            )
        else:
            self.df_hist["date_dt"] = pd.Timestamp("1970-01-01")

        if "tags" not in self.df_hist.columns:
            self.df_hist["tags"] = ""

        # =====================================================================
        # COMPUTE PROBLEM STATISTICS FOR SPACED REPETITION
        # =====================================================================
        now = pd.Timestamp.now()
        
        for slug in self.df_hist["slug"].dropna().unique():
            problem_rows = self.df_hist[self.df_hist["slug"] == slug]
            
            # Days since most recent attempt
            most_recent = problem_rows["date_dt"].max()
            if pd.notna(most_recent):
                days_since = (now - most_recent).days
            else:
                days_since = 999  # Very old / unknown
            
            # Success rate on this problem
            total_attempts = len(problem_rows)
            accepted = (problem_rows["statusDisplay"] == "Accepted").sum()
            success_rate = accepted / total_attempts if total_attempts > 0 else 0
            
            self.problem_stats[slug] = {
                "days_since_solve": days_since,
                "success_rate": success_rate,
                "attempts": total_attempts,
                "last_accepted": problem_rows[problem_rows["statusDisplay"] == "Accepted"]["date_dt"].max()
            }

        # Mark solved problems
        solved_slugs = set(self.df_hist["slug"].dropna().unique())
        self.df_master["src_history"] = self.df_master["slug"].isin(solved_slugs).astype(int)

        print(f"   -> Loaded Universe: {len(self.df_master)} problems")
        print(f"   -> Loaded History: {len(self.df_hist)} submissions ({len(solved_slugs)} unique problems)")
        return True

    def generate_candidates(self, target_count: int = 75) -> pd.DataFrame:
        """Generate candidate problems for recommendation."""
        print("\n🧠 GENERATING CANDIDATES...")

        # Use pandas Timestamp for proper comparison with datetime64
        cutoff_date = pd.Timestamp.now() - pd.Timedelta(days=7)  # Reduced to 7 days

        # Fresh solves: Accepted in last 7 days (skip these entirely)
        fresh = self.df_hist[
            (self.df_hist["statusDisplay"] == "Accepted")
            & (self.df_hist["date_dt"] > cutoff_date)
        ]["slug"].dropna().unique()
        fresh_solves = set(fresh)
        
        print(f"   -> Excluding {len(fresh_solves)} problems solved in last 7 days")

        # Eligible: not solved in last 7 days
        df_eligible = self.df_master[~self.df_master["slug"].isin(fresh_solves)].copy()

        # Quality filter
        if "Acceptance" in df_eligible.columns:
            col = df_eligible["Acceptance"]
            if col.dtype == "O":
                col = col.astype(str).str.replace("%", "", regex=False)
                col = pd.to_numeric(col, errors="coerce")
            df_eligible["Acceptance"] = col
            df_eligible = df_eligible[
                (df_eligible["Acceptance"].isna()) | (df_eligible["Acceptance"] >= 20.0)
            ]

        # Selection score (preliminary - final scoring happens in rank_and_display)
        df_eligible["n_companies"] = df_eligible["n_companies"].fillna(0)
        df_eligible["selection_score"] = np.log1p(df_eligible["n_companies"]) * 10

        # Add noise for variety
        df_eligible["selection_score"] += np.random.normal(0, 2.0, size=len(df_eligible))

        df_sorted = df_eligible.sort_values("selection_score", ascending=False)
        df_unique = df_sorted.drop_duplicates(subset="slug", keep="first")

        self.df_candidates = df_unique.head(target_count).copy()
        
        n_reviews = (self.df_candidates["src_history"] == 1).sum()
        n_new = (self.df_candidates["src_history"] == 0).sum()
        print(f"   -> Selected {len(self.df_candidates)} candidates ({n_reviews} reviews, {n_new} new)")
        
        return self.df_candidates

    def enrich_candidates(self):
        """Fetch live tags for candidate problems."""
        if self.df_candidates.empty:
            self.df_candidates["live_tags"] = ""
            return

        print(f"\n⚡ ENRICHING {len(self.df_candidates)} CANDIDATES...")

        slugs = self.df_candidates["slug"].tolist()
        enriched_data = []

        query = """
        query questionData($titleSlug: String!) {
            question(titleSlug: $titleSlug) {
                topicTags { name }
            }
        }
        """

        session = requests.Session()
        session.headers.update(self.headers)

        for slug in tqdm(slugs, desc="Enriching"):
            try:
                resp = session.post(
                    self.url,
                    json={"query": query, "variables": {"titleSlug": slug}},
                    timeout=10,
                )
                if resp.status_code == 200:
                    q = resp.json().get("data", {}).get("question", {})
                    if q:
                        tags = [t["name"] for t in q.get("topicTags", [])]
                        enriched_data.append({"slug": slug, "live_tags": ", ".join(tags)})
            except Exception:
                pass
            time.sleep(random.uniform(0.1, 0.3))

        df_tags = pd.DataFrame(enriched_data)
        if not df_tags.empty:
            self.df_candidates = pd.merge(self.df_candidates, df_tags, on="slug", how="left")
            self.df_candidates["live_tags"] = self.df_candidates["live_tags"].fillna("")
        else:
            self.df_candidates["live_tags"] = ""

    def rank_and_display(self, top_k: int = 20) -> pd.DataFrame:
        """Calculate final rankings with spaced repetition and display results."""
        print("\n🏆 CALCULATING FINAL RANKING (Spaced Repetition)...")

        # Build weakness profile from tag success rates
        self.df_hist["tags"] = self.df_hist["tags"].fillna("").astype(str)
        all_tags = (
            self.df_hist.assign(tag=self.df_hist["tags"].str.split(", "))
            .explode("tag")
        )
        all_tags["tag"] = all_tags["tag"].fillna("").astype(str).str.strip()
        all_tags = all_tags[all_tags["tag"] != ""]

        if all_tags.empty:
            weakness_map = {}
        else:
            tag_stats = (
                all_tags.groupby("tag")["statusDisplay"]
                .apply(lambda x: (x == "Accepted").mean())
                .reset_index(name="success_rate")
            )
            weakness_map = dict(zip(tag_stats["tag"], 1.0 - tag_stats["success_rate"]))

        def calculate_final_score(row):
            """
            Scoring with spaced repetition principles:
            - Base: Company frequency (log scale)
            - Tag weakness: Boost problems in your weak areas
            - Spaced repetition: Optimal review timing based on days since solve
            - Struggle bonus: Problems you failed get priority for review
            """
            slug = row["slug"]
            is_review = row.get("src_history", 0) == 1
            
            # BASE SCORE: Company frequency (log scale, 0-100 range)
            score = np.log1p(row.get("n_companies", 0)) * 20
            
            # TAG WEAKNESS: Boost problems matching weak tags
            tags = str(row.get("live_tags", "")).split(", ")
            for tag in tags:
                tag = tag.strip()
                if tag and tag in weakness_map:
                    score += weakness_map[tag] * 15
            
            if is_review:
                # =========================================================
                # SPACED REPETITION SCORING FOR REVIEWS
                # =========================================================
                stats = self.problem_stats.get(slug, {})
                days = stats.get("days_since_solve", 999)
                success_rate = stats.get("success_rate", 1.0)
                
                # Time-based review boost (spaced repetition curve)
                # - Too recent (< 7 days): Strong penalty (already filtered, but just in case)
                # - 7-14 days: Small boost (might be too soon)
                # - 14-30 days: Medium boost (good review window)
                # - 30-90 days: High boost (optimal review timing)
                # - 90+ days: Maximum boost (at risk of forgetting)
                if days < 7:
                    time_boost = -30  # Too recent, heavily penalize
                elif days < 14:
                    time_boost = 0    # Neutral
                elif days < 30:
                    time_boost = 5 + (days - 14) * 0.3  # Ramp up
                elif days < 90:
                    time_boost = 10 + (days - 30) * 0.15  # Sweet spot
                else:
                    time_boost = 20   # Maximum for very old problems
                
                # Struggle bonus: prioritize problems you found hard
                # Low success rate = high struggle = more review value
                struggle_boost = (1.0 - success_rate) * 15
                
                score += time_boost + struggle_boost
                
            else:
                # =========================================================
                # SCORING FOR NEW PROBLEMS
                # =========================================================
                # Give new problems a base boost to ensure some appear
                # Tag weakness already handled above
                new_boost = 8  # Base novelty bonus
                score += new_boost

            return score

        self.df_candidates["Final_Score"] = self.df_candidates.apply(calculate_final_score, axis=1)

        # =====================================================================
        # ENFORCE NEW/REVIEW MIX BASED ON review_percentage
        # =====================================================================
        n_reviews_target = int(top_k * self.review_percentage / 100)
        n_new_target = top_k - n_reviews_target
        
        print(f"   -> Target mix: {n_reviews_target} reviews, {n_new_target} new (based on {self.review_percentage}% review setting)")

        # Sort within each category
        df_reviews = (
            self.df_candidates[self.df_candidates["src_history"] == 1]
            .sort_values("Final_Score", ascending=False)
            .drop_duplicates(subset="slug", keep="first")
        )
        df_new = (
            self.df_candidates[self.df_candidates["src_history"] == 0]
            .sort_values("Final_Score", ascending=False)
            .drop_duplicates(subset="slug", keep="first")
        )
        
        # Take the target number from each category (or as many as available)
        selected_reviews = df_reviews.head(n_reviews_target)
        selected_new = df_new.head(n_new_target)
        
        # If we don't have enough of one type, fill with the other
        if len(selected_reviews) < n_reviews_target:
            shortfall = n_reviews_target - len(selected_reviews)
            extra_new = df_new.iloc[n_new_target:n_new_target + shortfall]
            selected_new = pd.concat([selected_new, extra_new])
        
        if len(selected_new) < n_new_target:
            shortfall = n_new_target - len(selected_new)
            extra_reviews = df_reviews.iloc[n_reviews_target:n_reviews_target + shortfall]
            selected_reviews = pd.concat([selected_reviews, extra_reviews])
        
        # Combine and sort by final score
        top = pd.concat([selected_reviews, selected_new]).sort_values("Final_Score", ascending=False)
        
        actual_reviews = (top["src_history"] == 1).sum()
        actual_new = (top["src_history"] == 0).sum()
        print(f"   -> Actual mix: {actual_reviews} reviews, {actual_new} new")

        # Display results
        print("\n" + "=" * 70)
        print(f"🚀 YOUR PERSONALIZED WORKOUT (Top {len(top)})")
        print("=" * 70)

        for _, row in top.iterrows():
            slug = row["slug"]
            is_review = row.get("src_history", 0) == 1
            stats = self.problem_stats.get(slug, {})
            
            if is_review:
                days = stats.get("days_since_solve", 0)
                success = stats.get("success_rate", 1.0)
                type_label = f"🧠 Review ({days}d ago, {success:.0%} success)"
            else:
                type_label = "🆕 New"
            
            diff = row.get("Difficulty", row.get("difficulty", "Unknown"))
            if pd.isna(diff):
                diff = "Unknown"
            title = row.get("Question", None)
            if pd.isna(title) or not title:
                title = row["slug"].replace("-", " ").title()
            acc = row.get("Acceptance", "N/A")
            if isinstance(acc, (float, int)) and not pd.isna(acc):
                acc_str = f"{acc:.1f}"
            else:
                acc_str = str(acc)

            print(f"[{diff}]  {title} | {type_label}")
            print(f"    Freq: {int(row.get('n_companies', 0))} companies | Acc: {acc_str}%")
            print(f"    Tags: {row.get('live_tags', '')}")
            print(f"    Score: {row['Final_Score']:.1f}")
            print(f"    Link: https://leetcode.com/problems/{row['slug']}/")
            print("-" * 70)

        # Store for later use
        self.df_final = top
        return top

    def run(self, target_candidates: int = 75, top_k: int = 20) -> pd.DataFrame:
        """Run the full recommendation pipeline."""
        if self.load_data():
            self.generate_candidates(target_count=target_candidates)
            self.enrich_candidates()
            return self.rank_and_display(top_k=top_k)
        return pd.DataFrame()


# =============================================================================
# WORKOUT PLAN EXPORT
# =============================================================================
def save_workout_plan(
    recommender: LeetCodeSmartRecommender,
    drive_path: str = DEFAULT_DRIVE_PATH,
    top_n: int = 10
) -> str:
    """
    Save the workout plan to a text file.
    
    Args:
        recommender: The recommender instance with candidates
        drive_path: Path to save the file
        top_n: Number of problems to include
        
    Returns:
        Path to the saved file
    """
    # Use df_final if available (post-mix enforcement), otherwise fall back to df_candidates
    if hasattr(recommender, 'df_final') and not recommender.df_final.empty:
        df_source = recommender.df_final
    elif not recommender.df_candidates.empty and "Final_Score" in recommender.df_candidates.columns:
        df_source = recommender.df_candidates.sort_values("Final_Score", ascending=False)
    else:
        print("❌ No recommendations available.")
        return ""

    top = df_source.head(top_n)
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(drive_path, f"leetcode_workout_plan_{date_str}.txt")

    lines = []
    lines.append("=" * 60)
    lines.append(f"🚀 YOUR PERSONALIZED LEETCODE PLAN ({date_str})")
    lines.append("=" * 60)
    lines.append(f"Review/New ratio: {recommender.review_percentage}% reviews, {100-recommender.review_percentage}% new")
    lines.append("Scoring: Company Frequency + Personal Weaknesses + Spaced Repetition\n")

    for i, row in enumerate(top.itertuples(), 1):
        slug = row.slug
        is_review = getattr(row, "src_history", 0) == 1
        stats = recommender.problem_stats.get(slug, {})
        
        if is_review:
            days = stats.get("days_since_solve", 0)
            success = stats.get("success_rate", 1.0)
            type_label = f"🧠 Review ({days}d ago, {success:.0%} success)"
        else:
            type_label = "🆕 New Skill"
        
        diff = getattr(row, "Difficulty", "Unknown")
        if pd.isna(diff):
            diff = "Unknown"
        title = getattr(row, "Question", None)
        if pd.isna(title) or not title:
            title = slug.replace("-", " ").title()

        lines.append(f"{i}. [{diff}] {title}")
        lines.append(f"    Type:  {type_label}")
        lines.append(f"    Freq:  {int(getattr(row, 'n_companies', 0))} companies")
        lines.append(f"    Tags:  {getattr(row, 'live_tags', '')}")
        lines.append(f"    Score: {row.Final_Score:.1f}")
        lines.append(f"    Link:  https://leetcode.com/problems/{slug}/")
        lines.append("-" * 60)

    lines.append("\nGood luck! Run the tracker again next week to update your progress.")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Workout plan saved to: {output_path}")
    return output_path


# =============================================================================
# ANALYTICS VISUALIZATION
# =============================================================================
def generate_analytics_jpeg(drive_path: str = DEFAULT_DRIVE_PATH) -> str:
    """
    Generate a 4-panel analytics visualization.
    
    Args:
        drive_path: Path to save the file
        
    Returns:
        Path to the saved file
    """
    import matplotlib.pyplot as plt
    import seaborn as sns

    print("🎨 GENERATING ANALYTICS CHART...")

    # Load history
    hist_path = os.path.join(drive_path, "leetcode_history_enriched_clean.csv")
    if not os.path.exists(hist_path):
        print("❌ No History file found.")
        return ""

    df = pd.read_csv(hist_path)
    print(f"   -> Analyzing: {os.path.basename(hist_path)}")

    if df.empty:
        print("❌ Empty history file.")
        return ""

    # Preprocessing
    df["is_accepted"] = df["statusDisplay"].apply(lambda x: 1 if x == "Accepted" else 0)

    df_tags = df.copy()
    df_tags["tags"] = df_tags["tags"].fillna("").astype(str)
    df_tags = df_tags[df_tags["tags"] != ""]
    df_tags = df_tags.assign(tag=df_tags["tags"].str.split(", ")).explode("tag")
    df_tags = df_tags[df_tags["tag"] != ""]

    # Setup plot
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
        sns.barplot(data=diff_stats, x="difficulty", y="is_accepted", hue="difficulty", palette=color_map, legend=False, ax=ax1)
        ax1.set_title("Success Rate by Difficulty", fontsize=14, fontweight="bold")
        ax1.set_ylim(0, 1.1)
        for i, row in enumerate(diff_stats.itertuples()):
            ax1.text(i, row.is_accepted + 0.02, f"{row.is_accepted:.1%}", ha="center")
    else:
        ax1.text(0.5, 0.5, "No difficulty data", ha="center", transform=ax1.transAxes)

    # Chart 2: Weakest Tags
    ax2 = axes[0, 1]
    if not df_tags.empty:
        tag_groups = df_tags.groupby("tag").agg(attempts=("id", "count"), success_rate=("is_accepted", "mean")).reset_index()
        valid_tags = tag_groups[tag_groups["attempts"] >= 3]
        weakest_tags = valid_tags.sort_values("success_rate").head(8)
        if not weakest_tags.empty:
            sns.barplot(data=weakest_tags, y="tag", x="success_rate", hue="tag", palette="magma", legend=False, ax=ax2)
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
        sns.histplot(attempts_capped, bins=range(1, 12), kde=False, color="royalblue", ax=ax3, discrete=True)
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
        sns.barplot(data=top_tags, y="tag", x="attempts", hue="tag", palette="viridis", legend=False, ax=ax4)
        ax4.set_title("Most Practiced Topics", fontsize=14, fontweight="bold")
    else:
        ax4.text(0.5, 0.5, "No tag data", ha="center", transform=ax4.transAxes)

    plt.tight_layout()

    # Save
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(drive_path, f"leetcode_analytics_{date_str}.jpg")
    plt.savefig(output_path, format="jpg", dpi=300)
    plt.close()

    print(f"✅ Analytics saved to: {output_path}")
    return output_path


# =============================================================================
# CONVENIENCE FUNCTION - Run full pipeline
# =============================================================================
def run_full_workout_pipeline(
    leetcode_session: str = None,
    csrf_token: str = None,
    drive_path: str = DEFAULT_DRIVE_PATH,
    fetch_limit: int = 200,
    enrich_limit: int = 100,
    target_candidates: int = 75,
    top_k: int = 10,
    skip_fetch: bool = False,
    review_percentage: int = 80
) -> dict:
    """
    Run the complete LeetCode workout pipeline.
    
    Args:
        leetcode_session: Your LEETCODE_SESSION cookie (required for fetch)
        csrf_token: Your CSRF token cookie (required for fetch)
        drive_path: Path to the leetcode folder
        fetch_limit: Number of submissions to fetch
        enrich_limit: Maximum problems to enrich
        target_candidates: Number of candidates to generate
        top_k: Number of recommendations to show
        skip_fetch: If True, skip fetching new submissions
        review_percentage: 1-100, percentage of workout that should be review problems.
                          Default 80 = 80% reviews, 20% new problems.
                          Based on learning science (~85% optimal accuracy).
        
    Returns:
        Dictionary with paths to generated files
    """
    results = {}

    # Step 1: Fetch new submissions
    if not skip_fetch:
        if not leetcode_session or not csrf_token:
            print("⚠️ Skipping fetch: No credentials provided")
        else:
            print("\n" + "=" * 60)
            print("STEP 1: FETCHING RECENT SUBMISSIONS")
            print("=" * 60)
            fetcher = LeetCodeFetcher(leetcode_session, csrf_token)
            df_new = fetcher.fetch_submission_history(limit=fetch_limit)
            
            if df_new.empty:
                # Clear warning when fetch fails
                print("\n" + "!" * 60)
                print("⚠️  WARNING: No submissions fetched!")
                print("!" * 60)
                print("\nPossible causes:")
                print("  1. EXPIRED COOKIES (most likely)")
                print("     → Go to leetcode.com in your browser")
                print("     → Open DevTools (F12) → Application → Cookies")
                print("     → Copy fresh LEETCODE_SESSION and csrftoken values")
                print("     → Update LEETCODE_SESSION and CSRF_TOKEN in your .env file")
                print("")
                print("  2. RATE LIMITING")
                print("     → Wait 10-15 minutes and try again")
                print("")
                print("  3. NETWORK ISSUES")
                print("     → Check your internet connection")
                print("")
                print("Continuing with EXISTING history data...")
                print("(Your recommendations will be based on previously saved submissions)\n")
            else:
                # Step 2: Enrich and save
                print("\n" + "=" * 60)
                print("STEP 2: ENRICHING WITH METADATA")
                print("=" * 60)
                history_path = enrich_and_save_history(df_new, drive_path, enrich_limit)
                results["history_path"] = history_path
                
                # Step 3: Deduplicate
                clean_path = deduplicate_and_save_clean(history_path, drive_path)
                results["clean_path"] = clean_path

    # Step 4: Harmonize data
    print("\n" + "=" * 60)
    print("STEP 3: HARMONIZING DATA SOURCES")
    print("=" * 60)
    merger = LeetCodeMergeValidator(drive_path)
    merger.run()
    results["master_path"] = os.path.join(drive_path, "leetcode_master_merged_raw.csv")

    # Step 5: Generate recommendations with review_percentage
    print("\n" + "=" * 60)
    print("STEP 4: GENERATING RECOMMENDATIONS")
    print("=" * 60)
    recommender = LeetCodeSmartRecommender(drive_path, review_percentage=review_percentage)
    recommender.run(target_candidates=target_candidates, top_k=top_k)

    # Step 6: Save workout plan
    workout_path = save_workout_plan(recommender, drive_path, top_n=top_k)
    results["workout_path"] = workout_path

    # Step 7: Generate analytics
    print("\n" + "=" * 60)
    print("STEP 5: GENERATING ANALYTICS")
    print("=" * 60)
    analytics_path = generate_analytics_jpeg(drive_path)
    results["analytics_path"] = analytics_path

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE!")
    print("=" * 60)
    for key, path in results.items():
        if path:
            print(f"   {key}: {os.path.basename(path)}")

    return results
