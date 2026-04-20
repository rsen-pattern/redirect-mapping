"""Tests for core/collections.py."""

import pandas as pd
import pytest

from core.collections import (
    combine_collection_sets,
    detect_collections_auto,
    detect_collections_by_pattern,
    detect_collections_by_segment,
)


def _make_crawl_df(rows: list[dict]) -> pd.DataFrame:
    defaults = {"address": "", "outlinks": 0, "inlinks": 0, "crawl_depth": 2, "title": "", "h1": ""}
    full_rows = [{**defaults, **r} for r in rows]
    return pd.DataFrame(full_rows)


# ---------------------------------------------------------------------------
# Pattern-based detection
# ---------------------------------------------------------------------------

def test_pattern_matches_simple_glob():
    df = _make_crawl_df([
        {"address": "https://example.com/category/shoes"},
        {"address": "https://example.com/category/bags"},
        {"address": "https://example.com/product/sneaker-1"},
    ])
    result = detect_collections_by_pattern(df, ["/category/*"])
    assert "https://example.com/category/shoes" in result
    assert "https://example.com/category/bags" in result
    assert "https://example.com/product/sneaker-1" not in result


def test_pattern_multiple_patterns():
    df = _make_crawl_df([
        {"address": "https://example.com/category/shoes"},
        {"address": "https://example.com/collections/bags"},
        {"address": "https://example.com/product/item"},
    ])
    result = detect_collections_by_pattern(df, ["/category/*", "/collections/*"])
    assert len(result) == 2
    assert "https://example.com/product/item" not in result


def test_pattern_no_matches():
    df = _make_crawl_df([{"address": "https://example.com/product/item"}])
    result = detect_collections_by_pattern(df, ["/category/*"])
    assert result == set()


def test_pattern_empty_patterns():
    df = _make_crawl_df([{"address": "https://example.com/category/shoes"}])
    result = detect_collections_by_pattern(df, [])
    assert result == set()


# ---------------------------------------------------------------------------
# Segment-based detection
# ---------------------------------------------------------------------------

def test_segment_detection_basic():
    seg_df = pd.DataFrame([
        {"url": "https://example.com/category/shoes", "segment": "collection"},
        {"url": "https://example.com/product/item", "segment": "product"},
    ])
    result = detect_collections_by_segment(seg_df, "collection")
    assert "https://example.com/category/shoes" in result
    assert "https://example.com/product/item" not in result


def test_segment_detection_case_insensitive():
    seg_df = pd.DataFrame([
        {"url": "https://example.com/category/shoes", "segment": "Collection"},
    ])
    result = detect_collections_by_segment(seg_df, "collection")
    assert "https://example.com/category/shoes" in result


def test_segment_detection_missing_columns():
    df = pd.DataFrame([{"page": "https://example.com/", "cat": "collection"}])
    result = detect_collections_by_segment(df, "collection")
    assert result == set()


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def test_auto_detect_high_outlinks():
    """Pages with very high outlinks, median+ inlinks, shallow depth → detected as collections."""
    # 10 product rows with low outlinks so the 90th pct threshold lands clearly below the 2 collection rows
    rows = [
        {"address": f"https://example.com/product/{i}", "outlinks": 2, "inlinks": 5, "crawl_depth": 3}
        for i in range(10)
    ] + [
        {"address": "https://example.com/category/all", "outlinks": 200, "inlinks": 50, "crawl_depth": 2},
        {"address": "https://example.com/category/shoes", "outlinks": 195, "inlinks": 40, "crawl_depth": 2},
    ]
    df = _make_crawl_df(rows)
    result = detect_collections_auto(df)
    assert "https://example.com/category/all" in result
    assert "https://example.com/category/shoes" in result


def test_auto_detect_empty_df():
    result = detect_collections_auto(pd.DataFrame())
    assert result == set()


def test_auto_detect_missing_outlinks_col():
    df = pd.DataFrame([{"address": "https://example.com/page", "inlinks": 5, "crawl_depth": 2}])
    # Should not crash
    result = detect_collections_auto(df)
    assert isinstance(result, set)


# ---------------------------------------------------------------------------
# combine_collection_sets
# ---------------------------------------------------------------------------

def test_combine_union():
    a = {"https://example.com/cat/1", "https://example.com/cat/2"}
    b = {"https://example.com/cat/2", "https://example.com/cat/3"}
    result = combine_collection_sets(a, b)
    assert result == {"https://example.com/cat/1", "https://example.com/cat/2", "https://example.com/cat/3"}


def test_combine_empty():
    result = combine_collection_sets(set(), set())
    assert result == set()
