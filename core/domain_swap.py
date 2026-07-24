"""Domain-swap matcher and coverage gap report.

When the new site mirrors the old one at a different host (e.g. production ->
staging), the most reliable match is not slug/content similarity but a direct
domain swap: transform each legacy URL onto the new host and confirm it exists
in the new crawl. This resolves the bulk of a like-for-like migration exactly,
with no ambiguity.

Design lesson baked in: a crawl is a *confirmation signal, not a source of
truth*. A legacy URL whose swapped form is absent from the new crawl is left in
``remaining`` (so the mechanical matchers still get a shot) rather than dropped.
"""

from __future__ import annotations

import re

import pandas as pd

from core.urls import canonicalize_url, content_type_of, swap_domain

_RESOLVED_COLS = ["legacy_url", "candidate_url", "score", "method"]

# Junk filters for the gap report.
_PAGINATION_RE = re.compile(r"/page-\d+/?$", re.I)
_ASSET_RE = re.compile(
    r"\.(?:css|js|json|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|map|xml|txt|pdf)$",
    re.I,
)
_UTILITY_RE = re.compile(r"/(?:cart|checkout|account|api)(?:/|$)", re.I)


def _empty_resolved() -> pd.DataFrame:
    return pd.DataFrame(columns=_RESOLVED_COLS)


def domain_swap_match(
    legacy: pd.DataFrame,
    new: pd.DataFrame,
    from_domain: str,
    to_domain: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve legacy URLs by swapping the domain and confirming in the new crawl.

    Returns ``(resolved_df, remaining_legacy_df)``. Resolved rows score ``1.0``
    with method ``"domain_swap"``. Legacy URLs whose swapped form is not present
    in the new crawl stay in ``remaining_legacy_df`` (never dropped).
    """
    if legacy is None or legacy.empty:
        return _empty_resolved(), (legacy.copy() if legacy is not None else _empty_resolved())
    if new is None or new.empty or not from_domain or not to_domain:
        return _empty_resolved(), legacy.copy()

    # Canonical index of the new crawl: canonical URL -> original address.
    new_index: dict[str, str] = {}
    for addr in new["address"]:
        new_index.setdefault(canonicalize_url(addr), str(addr))

    resolved_rows: list[dict] = []
    remaining_indices: list[int] = []

    for idx, row in legacy.iterrows():
        swapped = canonicalize_url(swap_domain(str(row["address"]), from_domain, to_domain))
        target = new_index.get(swapped)
        if target is not None:
            resolved_rows.append({
                "legacy_url": row["address"],
                "candidate_url": target,
                "score": 1.0,
                "method": "domain_swap",
            })
        else:
            remaining_indices.append(idx)

    resolved = pd.DataFrame(resolved_rows) if resolved_rows else _empty_resolved()
    remaining = legacy.loc[remaining_indices].reset_index(drop=True)
    return resolved, remaining


def build_gap_report(
    legacy: pd.DataFrame,
    matched_legacy_urls,
) -> pd.DataFrame:
    """Live legacy URLs with no redirect target, minus obvious junk.

    ``matched_legacy_urls`` is the set of legacy URLs that received a confident
    target. Everything in the legacy crawl that is not matched — and is not
    pagination, an asset, or a utility path (cart/checkout/account/api) — is
    reported as a coverage gap needing a manual decision.

    Returns a DataFrame ``[legacy_url, content_type]``.
    """
    cols = ["legacy_url", "content_type"]
    if legacy is None or legacy.empty or "address" not in legacy.columns:
        return pd.DataFrame(columns=cols)

    matched = {canonicalize_url(u) for u in (matched_legacy_urls or set())}

    rows: list[dict] = []
    for addr in legacy["address"]:
        url = str(addr)
        canon = canonicalize_url(url)
        if canon in matched:
            continue
        if _PAGINATION_RE.search(canon) or _ASSET_RE.search(canon) or _UTILITY_RE.search(canon):
            continue
        rows.append({"legacy_url": url, "content_type": content_type_of(url) or "other"})

    return pd.DataFrame(rows, columns=cols)
