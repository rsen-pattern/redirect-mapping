"""Shared URL canonicalisation, content-type and slug helpers.

Central home for URL normalisation so ingest, scoring and the domain-swap
matcher all agree on what "the same URL" means.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Shopify-style content-type segments used for content-type-aware slug keys.
# Order matters: a collection-context product URL contains both "collections"
# and "products", and should classify as a product.
_CONTENT_TYPE_SEGMENTS = ("products", "collections", "blogs", "pages")

_NUMERIC_RE = re.compile(r"^\d+$")


def _split_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _safe_parse(url: str):
    try:
        return urlparse(str(url))
    except Exception:
        return None


def canonicalize_url(url: str) -> str:
    """Return a canonical form of a URL for matching and indexing.

    - drops the query string and fragment (e.g. ``?section_id=``,
      ``?preview_theme_id=`` theme-preview noise)
    - collapses Shopify collection-context product URLs
      (``/collections/<x>/products/<slug>`` -> ``/products/<slug>``)
    - removes a trailing slash (except the root ``/``)

    Scheme and host are preserved. Bare paths (no host) are handled too.
    """
    raw = str(url).strip()
    if not raw:
        return raw

    parsed = _safe_parse(raw)
    if parsed is None:
        return raw

    segs = _split_segments(parsed.path or "/")

    # Collapse /collections/<x>/products/<slug>[/...] -> /products/<slug>[/...]
    if "products" in segs and "collections" in segs:
        segs = segs[segs.index("products"):]

    new_path = "/" + "/".join(segs) if segs else "/"
    # Drop params, query and fragment.
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


def content_type_of(url: str) -> str:
    """Classify a URL into a Shopify-style content type, or '' if unknown."""
    parsed = _safe_parse(url)
    path = parsed.path if parsed is not None else str(url)
    segs = [s.lower() for s in _split_segments(path)]
    for ct in _CONTENT_TYPE_SEGMENTS:
        if ct in segs:
            return ct
    return ""


def _last_meaningful_slug(segments: list[str]) -> str:
    """Last non-empty, non-numeric path segment (strips Shopify numeric IDs)."""
    for seg in reversed(segments):
        if seg and not _NUMERIC_RE.match(seg):
            return seg.lower()
    return segments[-1].lower() if segments else ""


def slug_key(url: str) -> str:
    """Content-type-aware slug key: ``<type>/<slug>`` or just ``<slug>``.

    Strips standalone numeric segments (e.g. Shopify product IDs), so
    ``/products/32040728166434/ascorbic-acid-capsules`` keys as
    ``products/ascorbic-acid-capsules``. URLs whose content type is unknown
    fall back to the bare last slug, matching the previous behaviour.
    """
    parsed = _safe_parse(url)
    path = parsed.path if parsed is not None else str(url)
    slug = _last_meaningful_slug(_split_segments(path))
    if not slug:
        return ""
    ct = content_type_of(url)
    return f"{ct}/{slug}" if ct else slug


def _domain_root(domain: str) -> str:
    """Normalise a user-supplied domain to ``scheme://netloc`` (no trailing slash)."""
    d = str(domain).strip().rstrip("/")
    if not d:
        return ""
    if "://" not in d:
        d = "https://" + d
    parsed = _safe_parse(d)
    if parsed is None or not parsed.netloc:
        return ""
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}"


def swap_domain(url: str, from_domain: str, to_domain: str) -> str:
    """Swap ``from_domain`` for ``to_domain`` on ``url``, preserving the path.

    Matches on host only (scheme-insensitive). If the URL's host does not match
    ``from_domain`` the URL is returned unchanged.
    """
    to_root = _domain_root(to_domain)
    from_root = _domain_root(from_domain)
    if not to_root or not from_root:
        return str(url)

    parsed = _safe_parse(url)
    if parsed is None or not parsed.netloc:
        return str(url)

    from_netloc = urlparse(from_root).netloc
    if parsed.netloc.lower() != from_netloc.lower():
        return str(url)

    to_parsed = urlparse(to_root)
    return urlunparse(
        (to_parsed.scheme, to_parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
