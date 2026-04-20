"""Tests for core/scoring.py."""

import pandas as pd
import pytest

from core.scoring import (
    assign_tier,
    combine_matcher_results,
    exact_slug_prepass,
    pick_winners,
)


def _make_legacy(*urls) -> pd.DataFrame:
    return pd.DataFrame({"address": list(urls)})


def _make_new(*urls) -> pd.DataFrame:
    return pd.DataFrame({"address": list(urls)})


def _make_candidate(legacy_url, candidate_url, score, method) -> dict:
    return {"legacy_url": legacy_url, "candidate_url": candidate_url, "score": score, "method": method}


# ---------------------------------------------------------------------------
# exact_slug_prepass
# ---------------------------------------------------------------------------

def test_exact_slug_unique_match_resolves():
    legacy = _make_legacy("https://old.com/product/pegasus-40")
    new = _make_new("https://new.com/shoes/pegasus-40", "https://new.com/other-thing")
    resolved, remaining, _ = exact_slug_prepass(legacy, new)
    assert len(resolved) == 1
    assert resolved.iloc[0]["candidate_url"] == "https://new.com/shoes/pegasus-40"
    assert resolved.iloc[0]["score"] == 1.0
    assert resolved.iloc[0]["method"] == "exact_slug"
    assert remaining.empty


def test_exact_slug_no_match_stays_in_remaining():
    legacy = _make_legacy("https://old.com/product/unique-item-xyz")
    new = _make_new("https://new.com/shoes/pegasus-40", "https://new.com/running")
    resolved, remaining, _ = exact_slug_prepass(legacy, new)
    assert resolved.empty
    assert len(remaining) == 1


def test_exact_slug_multiple_matches_stays_in_remaining():
    """Ambiguous slug (appears in multiple new URLs) must NOT be resolved."""
    legacy = _make_legacy("https://old.com/product/pegasus-40")
    new = _make_new(
        "https://new.com/shoes/pegasus-40",
        "https://new.com/archive/pegasus-40",
    )
    resolved, remaining, _ = exact_slug_prepass(legacy, new)
    assert resolved.empty
    assert len(remaining) == 1


def test_exact_slug_case_insensitive():
    legacy = _make_legacy("https://old.com/product/Pegasus-40")
    new = _make_new("https://new.com/shoes/pegasus-40")
    resolved, remaining, _ = exact_slug_prepass(legacy, new)
    assert len(resolved) == 1


def test_exact_slug_new_not_removed_for_other_legacy():
    """Same new URL can be matched by multiple legacy URLs."""
    legacy = _make_legacy(
        "https://old.com/category/pegasus-40",
        "https://old.com/product/pegasus-40",
    )
    new = _make_new("https://new.com/all/pegasus-40")
    resolved, remaining, remaining_new = exact_slug_prepass(legacy, new)
    assert len(resolved) == 2
    # Both resolve to the same new URL
    assert set(resolved["candidate_url"]) == {"https://new.com/all/pegasus-40"}
    assert len(remaining_new) == 1  # new not removed


def test_exact_slug_empty_inputs():
    legacy = pd.DataFrame(columns=["address"])
    new = pd.DataFrame(columns=["address"])
    resolved, remaining, _ = exact_slug_prepass(legacy, new)
    assert resolved.empty
    assert remaining.empty


# ---------------------------------------------------------------------------
# combine_matcher_results
# ---------------------------------------------------------------------------

def test_combine_single_matcher():
    df = pd.DataFrame([_make_candidate("https://a.com/", "https://b.com/", 0.8, "h1")])
    weights = {"h1": 0.30}
    combined = combine_matcher_results([df], weights)
    assert len(combined) == 1
    assert combined.iloc[0]["combined_score"] == pytest.approx(0.8 * 0.30)


def test_combine_multiple_matchers_same_candidate():
    df1 = pd.DataFrame([_make_candidate("https://a.com/", "https://b.com/", 0.8, "h1")])
    df2 = pd.DataFrame([_make_candidate("https://a.com/", "https://b.com/", 0.6, "title")])
    weights = {"h1": 0.30, "title": 0.25}
    combined = combine_matcher_results([df1, df2], weights)
    assert len(combined) == 1
    expected = 0.8 * 0.30 + 0.6 * 0.25
    assert combined.iloc[0]["combined_score"] == pytest.approx(expected)


def test_combine_empty_list():
    result = combine_matcher_results([], {})
    assert result.empty


# ---------------------------------------------------------------------------
# pick_winners
# ---------------------------------------------------------------------------

def test_pick_winners_selects_highest():
    combined = pd.DataFrame([
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/1", "combined_score": 0.95, "methods": "h1"},
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/2", "combined_score": 0.70, "methods": "title"},
    ])
    winners = pick_winners(combined)
    assert len(winners) == 1
    assert winners.iloc[0]["candidate_url"] == "https://b.com/1"


def test_pick_winners_low_score_is_ambiguous():
    combined = pd.DataFrame([
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/1", "combined_score": 0.60, "methods": "h1"},
    ])
    winners = pick_winners(combined)
    assert winners.iloc[0]["is_ambiguous"]


def test_pick_winners_close_gap_is_ambiguous():
    combined = pd.DataFrame([
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/1", "combined_score": 0.92, "methods": "h1"},
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/2", "combined_score": 0.89, "methods": "title"},
    ])
    winners = pick_winners(combined)
    # gap = 0.03 < 0.05 → ambiguous
    assert winners.iloc[0]["is_ambiguous"]


def test_pick_winners_high_unambiguous():
    combined = pd.DataFrame([
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/1", "combined_score": 0.95, "methods": "h1"},
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/2", "combined_score": 0.70, "methods": "title"},
    ])
    winners = pick_winners(combined)
    assert not winners.iloc[0]["is_ambiguous"]


def test_pick_winners_exact_slug_never_ambiguous():
    combined = pd.DataFrame([
        {"legacy_url": "https://a.com/", "candidate_url": "https://b.com/1", "combined_score": 1.0, "methods": "exact_slug"},
    ])
    winners = pick_winners(combined)
    assert not winners.iloc[0]["is_ambiguous"]


# ---------------------------------------------------------------------------
# assign_tier
# ---------------------------------------------------------------------------

def test_assign_tier_high():
    assert assign_tier(0.95) == "high"
    assert assign_tier(0.90) == "high"


def test_assign_tier_review():
    assert assign_tier(0.85) == "review"
    assert assign_tier(0.70) == "review"


def test_assign_tier_no_match():
    assert assign_tier(0.69) == "no_match"
    assert assign_tier(0.0) == "no_match"
