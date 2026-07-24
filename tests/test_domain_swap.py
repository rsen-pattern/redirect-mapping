"""Tests for core/domain_swap.py — domain-swap matcher and gap report."""

import pandas as pd

from core.domain_swap import build_gap_report, domain_swap_match


def _df(*urls) -> pd.DataFrame:
    return pd.DataFrame({"address": list(urls)})


# ---------------------------------------------------------------------------
# domain_swap_match
# ---------------------------------------------------------------------------

def test_domain_swap_resolves_when_present_in_new():
    legacy = _df("https://old.com/products/foo", "https://old.com/products/bar")
    new = _df("https://new.com/products/foo", "https://new.com/products/bar")
    resolved, remaining = domain_swap_match(legacy, new, "https://old.com", "https://new.com")
    assert len(resolved) == 2
    assert set(resolved["method"]) == {"domain_swap"}
    assert (resolved["score"] == 1.0).all()
    assert remaining.empty


def test_domain_swap_leaves_missing_in_remaining_not_dropped():
    legacy = _df("https://old.com/products/foo", "https://old.com/blogs/news/unpublished")
    new = _df("https://new.com/products/foo")
    resolved, remaining = domain_swap_match(legacy, new, "https://old.com", "https://new.com")
    assert len(resolved) == 1
    assert len(remaining) == 1
    assert remaining.iloc[0]["address"] == "https://old.com/blogs/news/unpublished"


def test_domain_swap_matches_through_canonicalisation():
    # legacy has a query string; new is clean — canonicalisation makes them match
    legacy = _df("https://old.com/products/foo?variant=1")
    new = _df("https://new.com/products/foo")
    resolved, _ = domain_swap_match(legacy, new, "https://old.com", "https://new.com")
    assert len(resolved) == 1
    assert resolved.iloc[0]["candidate_url"] == "https://new.com/products/foo"


def test_domain_swap_missing_domains_noop():
    legacy = _df("https://old.com/x")
    new = _df("https://new.com/x")
    resolved, remaining = domain_swap_match(legacy, new, "", "")
    assert resolved.empty
    assert len(remaining) == 1


def test_domain_swap_empty_inputs():
    resolved, remaining = domain_swap_match(_df(), _df(), "a.com", "b.com")
    assert resolved.empty
    assert remaining.empty


# ---------------------------------------------------------------------------
# build_gap_report
# ---------------------------------------------------------------------------

def test_gap_report_excludes_matched():
    legacy = _df("https://old.com/products/foo", "https://old.com/products/bar")
    matched = {"https://old.com/products/foo"}
    gap = build_gap_report(legacy, matched)
    assert list(gap["legacy_url"]) == ["https://old.com/products/bar"]


def test_gap_report_filters_pagination_assets_and_utility():
    legacy = _df(
        "https://old.com/collections/all/page-35",
        "https://old.com/theme.css",
        "https://old.com/cart",
        "https://old.com/api/blog/article",
        "https://old.com/collections/gastrointestinal",
    )
    gap = build_gap_report(legacy, set())
    assert list(gap["legacy_url"]) == ["https://old.com/collections/gastrointestinal"]


def test_gap_report_labels_content_type():
    legacy = _df("https://old.com/products/foo", "https://old.com/random/thing")
    gap = build_gap_report(legacy, set())
    types = dict(zip(gap["legacy_url"], gap["content_type"]))
    assert types["https://old.com/products/foo"] == "products"
    assert types["https://old.com/random/thing"] == "other"


def test_gap_report_empty_legacy():
    gap = build_gap_report(_df(), set())
    assert gap.empty
