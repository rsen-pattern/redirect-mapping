"""Integration test — Nickscali real migration data.

Requires NS_AU_OLD_internal_html.csv and NS_AU_New_internal_html.csv in
tests/fixtures/integration/. Drop them there before running.
"""

import time
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "integration"
OLD_CSV = FIXTURES / "NS_AU_OLD_internal_html.csv"
NEW_CSV = FIXTURES / "NS_AU_New_internal_html.csv"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def check_fixtures():
    if not OLD_CSV.exists() or not NEW_CSV.exists():
        pytest.skip(
            "Nickscali integration fixtures not found. "
            "Drop NS_AU_OLD_internal_html.csv and NS_AU_New_internal_html.csv "
            "into tests/fixtures/integration/ to run this test."
        )


def test_nickscali_migration_end_to_end():
    from core.ingest import apply_mapping, auto_map_columns, filter_html_200, read_crawl
    from core.matchers import match_h1, match_h2, match_path, match_slug, match_title
    from core.scoring import (
        DEFAULT_WEIGHTS_MODE_A,
        combine_matcher_results,
        exact_slug_prepass,
        pick_winners,
    )

    t0 = time.perf_counter()

    # --- Load and filter ---
    with open(OLD_CSV, "rb") as f:
        old_raw = read_crawl(f)
    old_mapping, _ = auto_map_columns(old_raw)
    old_df = apply_mapping(old_raw, old_mapping)
    old_df = filter_html_200(old_df)

    with open(NEW_CSV, "rb") as f:
        new_raw = read_crawl(f)
    new_mapping, _ = auto_map_columns(new_raw)
    new_df = apply_mapping(new_raw, new_mapping)
    new_df = filter_html_200(new_df)

    assert len(old_df) >= 500, f"Expected ≥500 old HTML-200 rows, got {len(old_df)}"
    assert len(new_df) >= 1200, f"Expected ≥1200 new HTML-200 rows, got {len(new_df)}"

    # --- Exact-slug pre-pass ---
    resolved_df, remaining_legacy, _ = exact_slug_prepass(old_df, new_df)
    n_prepass = len(resolved_df)
    assert n_prepass >= 350, (
        f"Exact-slug pre-pass resolved {n_prepass} URLs — expected ≥350. "
        "Check that slugs are preserved in the new crawl."
    )

    # --- Mechanical matchers ---
    matcher_dfs = []
    if not remaining_legacy.empty:
        matcher_dfs.append(match_path(remaining_legacy, new_df))
        matcher_dfs.append(match_slug(remaining_legacy, new_df))
        matcher_dfs.append(match_title(remaining_legacy, new_df))
        matcher_dfs.append(match_h1(remaining_legacy, new_df))
        matcher_dfs.append(match_h2(remaining_legacy, new_df))

    combined_df = combine_matcher_results(matcher_dfs, DEFAULT_WEIGHTS_MODE_A)
    winners_df = pick_winners(combined_df)

    # Merge pre-pass
    if not resolved_df.empty:
        pre_pass_winners = resolved_df.rename(columns={"score": "combined_score"})
        pre_pass_winners["second_score"] = 0.0
        pre_pass_winners["methods_contributed"] = "exact_slug"
        pre_pass_winners["is_ambiguous"] = False
        pre_pass_winners["tier"] = "high"
        import pandas as _pd
        winners_df = _pd.concat([pre_pass_winners, winners_df], ignore_index=True)

    n_high = int((winners_df["tier"] == "high").sum()) if "tier" in winners_df.columns else 0
    n_total = len(winners_df)
    elapsed = time.perf_counter() - t0

    print(f"\nNickscali results:")
    print(f"  Old HTML-200 rows:     {len(old_df)}")
    print(f"  New HTML-200 rows:     {len(new_df)}")
    print(f"  Pre-pass resolved:     {n_prepass}")
    print(f"  Total matched:         {n_total}")
    print(f"  High confidence ≥0.90: {n_high}")
    print(f"  Wall-clock time:       {elapsed:.1f}s")

    assert n_high >= 450, (
        f"High-confidence matches: {n_high} — expected ≥450. "
        "Weights may need tuning or the pre-pass count is too low."
    )

    assert elapsed < 90, (
        f"Integration test took {elapsed:.1f}s — expected under 90s. "
        "Profile the TF-IDF vectoriser and NearestNeighbors fit."
    )
