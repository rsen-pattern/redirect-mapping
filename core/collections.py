"""Collection-page detection for Mode B (product retirement)."""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

import pandas as pd


def detect_collections_by_pattern(
    df: pd.DataFrame,
    patterns: list[str],
) -> set[str]:
    """Detect collection URLs matching any glob-style path pattern.

    Patterns operate on the URL path (e.g. '/category/*', '/*/collection/*').
    """
    if not patterns or df.empty:
        return set()

    result: set[str] = set()
    for url in df["address"]:
        url_str = str(url)
        try:
            path = urlparse(url_str).path
        except Exception:
            path = url_str
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern):
                result.add(url_str)
                break

    return result


def detect_collections_by_segment(
    segments_df: pd.DataFrame,
    segment_name: str = "collection",
) -> set[str]:
    """Detect collection URLs from a Screaming Frog Segments CSV.

    Expects columns 'url' and 'segment'. Returns URLs where segment matches segment_name.
    """
    if segments_df.empty:
        return set()

    url_col = next((c for c in segments_df.columns if c.strip().lower() == "url"), None)
    seg_col = next((c for c in segments_df.columns if c.strip().lower() == "segment"), None)
    if url_col is None or seg_col is None:
        return set()

    mask = segments_df[seg_col].astype(str).str.lower() == segment_name.lower()
    return set(segments_df.loc[mask, url_col].astype(str).tolist())


def detect_collections_auto(df: pd.DataFrame) -> set[str]:
    """Auto-detect collection pages by heuristic.

    A URL is a collection candidate if:
    - outlinks >= 90th percentile
    - inlinks >= median
    - crawl_depth <= median crawl_depth + 1
    """
    if df.empty:
        return set()

    result_df = df.copy()

    for col in ("outlinks", "inlinks", "crawl_depth"):
        if col not in result_df.columns:
            result_df[col] = 0
        result_df[col] = pd.to_numeric(result_df[col], errors="coerce").fillna(0)

    outlinks_thresh = result_df["outlinks"].quantile(0.90)
    inlinks_thresh = result_df["inlinks"].median()
    depth_thresh = result_df["crawl_depth"].median() + 1

    mask = (
        (result_df["outlinks"] >= outlinks_thresh)
        & (result_df["inlinks"] >= inlinks_thresh)
        & (result_df["crawl_depth"] <= depth_thresh)
    )

    return set(result_df.loc[mask, "address"].astype(str).tolist())


def combine_collection_sets(*sets: set[str]) -> set[str]:
    """Union of multiple collection URL sets."""
    result: set[str] = set()
    for s in sets:
        result |= s
    return result
