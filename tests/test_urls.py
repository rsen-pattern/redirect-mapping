"""Tests for core/urls.py — canonicalisation, content-type and slug helpers."""

from core.urls import (
    canonicalize_url,
    content_type_of,
    slug_key,
    swap_domain,
)


# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------

def test_canonicalize_strips_query_string():
    assert (
        canonicalize_url("https://x.com/products/foo?section_id=123&preview_theme_id=9")
        == "https://x.com/products/foo"
    )


def test_canonicalize_strips_fragment():
    assert canonicalize_url("https://x.com/a/b#section") == "https://x.com/a/b"


def test_canonicalize_removes_trailing_slash():
    assert canonicalize_url("https://x.com/collections/all/") == "https://x.com/collections/all"


def test_canonicalize_keeps_root_slash():
    assert canonicalize_url("https://x.com/") == "https://x.com/"


def test_canonicalize_collapses_collection_context_product():
    assert (
        canonicalize_url("https://x.com/collections/best-sellers/products/pegasus-40")
        == "https://x.com/products/pegasus-40"
    )


def test_canonicalize_idempotent():
    once = canonicalize_url("https://x.com/products/foo/?a=1")
    assert canonicalize_url(once) == once


def test_canonicalize_blank():
    assert canonicalize_url("") == ""


# ---------------------------------------------------------------------------
# content_type_of
# ---------------------------------------------------------------------------

def test_content_type_products():
    assert content_type_of("https://x.com/products/foo") == "products"


def test_content_type_collections():
    assert content_type_of("https://x.com/collections/gastro") == "collections"


def test_content_type_unknown_returns_empty():
    assert content_type_of("https://x.com/shoes/pegasus-40") == ""


def test_content_type_product_wins_over_collection():
    # collection-context product URL classifies as product
    assert content_type_of("https://x.com/collections/x/products/y") == "products"


# ---------------------------------------------------------------------------
# slug_key
# ---------------------------------------------------------------------------

def test_slug_key_strips_shopify_numeric_id():
    assert (
        slug_key("https://x.com/products/32040728166434/ascorbic-acid-capsules")
        == "products/ascorbic-acid-capsules"
    )


def test_slug_key_content_type_aware():
    # same last slug, different content type -> different keys
    assert slug_key("https://x.com/products/foo") != slug_key("https://x.com/collections/foo")


def test_slug_key_unknown_type_falls_back_to_bare_slug():
    assert slug_key("https://x.com/shoes/pegasus-40") == "pegasus-40"


def test_slug_key_case_insensitive():
    assert slug_key("https://x.com/products/Foo") == "products/foo"


# ---------------------------------------------------------------------------
# swap_domain
# ---------------------------------------------------------------------------

def test_swap_domain_replaces_host_preserves_path():
    assert (
        swap_domain(
            "https://smartq.pureforyou.com/products/foo",
            "https://smartq.pureforyou.com",
            "https://pfy-native-horizon.myshopify.com",
        )
        == "https://pfy-native-horizon.myshopify.com/products/foo"
    )


def test_swap_domain_accepts_bare_domain():
    assert (
        swap_domain("https://a.com/x", "a.com", "b.com")
        == "https://b.com/x"
    )


def test_swap_domain_no_match_returns_unchanged():
    url = "https://other.com/x"
    assert swap_domain(url, "a.com", "b.com") == url


def test_swap_domain_scheme_insensitive_match():
    assert (
        swap_domain("http://a.com/x", "https://a.com", "https://b.com")
        == "https://b.com/x"
    )
