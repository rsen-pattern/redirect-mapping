"""Tests for core/matchers.py."""

from pathlib import Path

import pandas as pd
import pytest

from core.ingest import apply_mapping, auto_map_columns, filter_html_200, read_crawl
from core.matchers import (
    match_h1,
    match_h2,
    match_inlinks,
    match_path,
    match_slug,
    match_tfidf,
    match_title,
)

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def _load_fixture(name: str) -> pd.DataFrame:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"Fixture {name} not found")
    with open(path, "rb") as f:
        df = read_crawl(f)
    mapping, _ = auto_map_columns(df)
    df = apply_mapping(df, mapping)
    return filter_html_200(df)


@pytest.fixture
def legacy_df():
    return _load_fixture("legacy_sample.csv")


@pytest.fixture
def new_df():
    return _load_fixture("new_sample.csv")


@pytest.fixture
def inlinks_df():
    path = FIXTURES / "inlinks_sample.csv"
    if not path.exists():
        pytest.skip("inlinks fixture not found")
    with open(path, "rb") as f:
        from core.ingest import read_crawl as rc
        return rc(f)


def _check_output_schema(df: pd.DataFrame) -> None:
    assert set(["legacy_url", "candidate_url", "score", "method"]).issubset(df.columns)


def _check_scores_in_range(df: pd.DataFrame) -> None:
    if not df.empty:
        assert (df["score"] >= 0.0).all(), "Scores must be >= 0"
        assert (df["score"] <= 1.0).all(), "Scores must be <= 1"


def test_match_path_schema(legacy_df, new_df):
    result = match_path(legacy_df, new_df)
    _check_output_schema(result)


def test_match_path_scores_in_range(legacy_df, new_df):
    result = match_path(legacy_df, new_df)
    _check_scores_in_range(result)


def test_match_path_top_k(legacy_df, new_df):
    result = match_path(legacy_df, new_df, top_k=3)
    if not result.empty:
        counts = result.groupby("legacy_url")["candidate_url"].count()
        assert (counts <= 3).all()


def test_match_path_exact_score():
    """Identical paths should score 1.0."""
    leg = pd.DataFrame({"address": ["https://a.com/shoes/pegasus"]})
    new = pd.DataFrame({"address": ["https://b.com/shoes/pegasus", "https://b.com/other"]})
    result = match_path(leg, new, top_k=5)
    top = result[result["legacy_url"] == "https://a.com/shoes/pegasus"].sort_values("score", ascending=False)
    assert top.iloc[0]["score"] == pytest.approx(1.0)


def test_match_slug_schema(legacy_df, new_df):
    result = match_slug(legacy_df, new_df)
    _check_output_schema(result)


def test_match_slug_scores_in_range(legacy_df, new_df):
    result = match_slug(legacy_df, new_df)
    _check_scores_in_range(result)


def test_match_slug_exact_score():
    """Identical slugs should score 1.0."""
    leg = pd.DataFrame({"address": ["https://a.com/old/pegasus-40"]})
    new = pd.DataFrame({"address": ["https://b.com/new/pegasus-40", "https://b.com/other"]})
    result = match_slug(leg, new, top_k=5)
    top = result.sort_values("score", ascending=False)
    assert top.iloc[0]["score"] == pytest.approx(1.0)
    assert top.iloc[0]["candidate_url"] == "https://b.com/new/pegasus-40"


def test_match_tfidf_schema(legacy_df, new_df):
    result = match_tfidf(legacy_df, new_df, "h1", "h1")
    _check_output_schema(result)


def test_match_tfidf_scores_in_range(legacy_df, new_df):
    result = match_tfidf(legacy_df, new_df, "h1", "h1")
    _check_scores_in_range(result)


def test_match_tfidf_identical_text_scores_high():
    """Identical H1s should score very high (near 1.0)."""
    leg = pd.DataFrame({"address": ["https://a.com/p1"], "h1": ["Nike Pegasus 40"]})
    new = pd.DataFrame({"address": ["https://b.com/p1", "https://b.com/p2"], "h1": ["Nike Pegasus 40", "Hoka Speedgoat"]})
    result = match_tfidf(leg, new, "h1", "h1", top_k=5)
    top = result.sort_values("score", ascending=False)
    assert top.iloc[0]["score"] > 0.90
    assert top.iloc[0]["candidate_url"] == "https://b.com/p1"


def test_match_tfidf_empty_h1_doesnt_crash(legacy_df, new_df):
    leg = legacy_df.copy()
    leg["h1"] = ""
    result = match_h1(leg, new_df)
    # Should return empty df without crashing
    assert isinstance(result, pd.DataFrame)


def test_match_h1_schema(legacy_df, new_df):
    result = match_h1(legacy_df, new_df)
    _check_output_schema(result)


def test_match_h2_schema(legacy_df, new_df):
    result = match_h2(legacy_df, new_df)
    _check_output_schema(result)


def test_match_title_schema(legacy_df, new_df):
    result = match_title(legacy_df, new_df)
    _check_output_schema(result)


def test_match_tfidf_top_k_respected(legacy_df, new_df):
    result = match_h1(legacy_df, new_df, top_k=2)
    if not result.empty:
        counts = result.groupby("legacy_url")["candidate_url"].count()
        assert (counts <= 2).all()


def test_match_inlinks_schema(legacy_df, new_df, inlinks_df):
    result = match_inlinks(legacy_df, new_df, inlinks_df)
    _check_output_schema(result)


def test_match_inlinks_scores_in_range(legacy_df, new_df, inlinks_df):
    result = match_inlinks(legacy_df, new_df, inlinks_df)
    _check_scores_in_range(result)


def test_match_inlinks_no_inlinks_returns_empty(legacy_df, new_df):
    empty_inlinks = pd.DataFrame(columns=["Source", "Destination"])
    result = match_inlinks(legacy_df, new_df, empty_inlinks)
    assert result.empty


def test_match_path_empty_inputs():
    empty = pd.DataFrame(columns=["address"])
    leg = pd.DataFrame({"address": ["https://a.com/page"]})
    result = match_path(empty, leg)
    assert result.empty
    result2 = match_path(leg, empty)
    assert result2.empty
