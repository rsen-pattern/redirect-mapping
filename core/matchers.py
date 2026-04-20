"""All mechanical matchers for Mode A (site migration) and Mode B (product retirement)."""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from core.inlinks import jaccard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_path(url: str) -> str:
    """Extract URL path, stripping protocol and domain."""
    try:
        return urlparse(str(url)).path or "/"
    except Exception:
        return str(url)


def _get_slug(url: str) -> str:
    """Last non-empty path segment, lowercased."""
    path = _get_path(url)
    segments = [s for s in path.split("/") if s]
    return segments[-1].lower() if segments else ""


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["legacy_url", "candidate_url", "score", "method"])


# ---------------------------------------------------------------------------
# Mode A matchers
# ---------------------------------------------------------------------------

def match_path(legacy: pd.DataFrame, new: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """Match by URL path similarity (rapidfuzz ratio). Score normalised 0–1."""
    if legacy.empty or new.empty:
        return _empty_df()

    new_urls = list(new["address"])
    new_paths = [_get_path(u) for u in new_urls]

    rows: list[dict] = []
    for legacy_url in legacy["address"]:
        legacy_path = _get_path(str(legacy_url))
        if not legacy_path:
            continue
        results = process.extract(legacy_path, new_paths, scorer=fuzz.ratio, limit=top_k)
        for _text, score, idx in results:
            rows.append({
                "legacy_url": legacy_url,
                "candidate_url": new_urls[idx],
                "score": score / 100.0,
                "method": "path",
            })

    return pd.DataFrame(rows) if rows else _empty_df()


def match_slug(legacy: pd.DataFrame, new: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """Match by last URL path segment similarity (rapidfuzz ratio). Score normalised 0–1."""
    if legacy.empty or new.empty:
        return _empty_df()

    new_urls = list(new["address"])
    new_slugs = [_get_slug(u) for u in new_urls]

    rows: list[dict] = []
    for legacy_url in legacy["address"]:
        legacy_slug = _get_slug(str(legacy_url))
        if not legacy_slug:
            continue
        results = process.extract(legacy_slug, new_slugs, scorer=fuzz.ratio, limit=top_k)
        for _text, score, idx in results:
            rows.append({
                "legacy_url": legacy_url,
                "candidate_url": new_urls[idx],
                "score": score / 100.0,
                "method": "slug",
            })

    return pd.DataFrame(rows) if rows else _empty_df()


def match_tfidf(
    legacy: pd.DataFrame,
    new: pd.DataFrame,
    field: str,
    method_name: str,
    top_k: int = 5,
) -> pd.DataFrame:
    """TF-IDF cosine similarity matcher for a text field.

    Fits on union of legacy + new, transforms each side, uses NearestNeighbors for top-K.
    Score = 1 - cosine_distance.
    """
    if legacy.empty or new.empty:
        return _empty_df()

    legacy_texts = (
        legacy[field].fillna("").astype(str).tolist()
        if field in legacy.columns
        else [""] * len(legacy)
    )
    new_texts = (
        new[field].fillna("").astype(str).tolist()
        if field in new.columns
        else [""] * len(new)
    )
    legacy_urls = list(legacy["address"])
    new_urls = list(new["address"])

    # Filter out rows with empty text
    legacy_valid = [(i, t) for i, t in enumerate(legacy_texts) if t.strip()]
    new_valid = [(i, t) for i, t in enumerate(new_texts) if t.strip()]

    if not legacy_valid or not new_valid:
        return _empty_df()

    legacy_indices, legacy_filtered = zip(*legacy_valid)
    new_indices, new_filtered = zip(*new_valid)

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
    vectorizer.fit(list(legacy_filtered) + list(new_filtered))

    legacy_matrix = vectorizer.transform(legacy_filtered)
    new_matrix = vectorizer.transform(new_filtered)

    k = min(top_k, len(new_filtered))
    nn = NearestNeighbors(n_neighbors=k, metric="cosine", algorithm="brute")
    nn.fit(new_matrix)
    distances, indices = nn.kneighbors(legacy_matrix)

    rows: list[dict] = []
    for li, (dists, idxs) in enumerate(zip(distances, indices)):
        orig_legacy_idx = legacy_indices[li]
        for dist, ni in zip(dists, idxs):
            orig_new_idx = new_indices[ni]
            score = max(0.0, 1.0 - float(dist))
            rows.append({
                "legacy_url": legacy_urls[orig_legacy_idx],
                "candidate_url": new_urls[orig_new_idx],
                "score": score,
                "method": method_name,
            })

    return pd.DataFrame(rows) if rows else _empty_df()


def match_title(legacy: pd.DataFrame, new: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """TF-IDF match on page title."""
    return match_tfidf(legacy, new, "title", "title", top_k)


def match_h1(legacy: pd.DataFrame, new: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """TF-IDF match on H1 text."""
    return match_tfidf(legacy, new, "h1", "h1", top_k)


def match_h2(legacy: pd.DataFrame, new: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """TF-IDF match on H2 text."""
    return match_tfidf(legacy, new, "h2", "h2", top_k)


def match_inlinks(
    legacy: pd.DataFrame,
    new: pd.DataFrame,
    inlinks_df: pd.DataFrame,
    top_k: int = 5,
) -> pd.DataFrame:
    """Match by inlink source set Jaccard similarity.

    Builds {url: set(sources)} from inlinks_df and computes Jaccard per pair.
    """
    if legacy.empty or new.empty or inlinks_df.empty:
        return _empty_df()

    from core.inlinks import build_inlinks_from_df
    inlinks_map = build_inlinks_from_df(inlinks_df)

    legacy_urls = list(legacy["address"])
    new_urls = list(new["address"])

    rows: list[dict] = []
    for lu in legacy_urls:
        l_set = inlinks_map.get(str(lu), set())
        if not l_set:
            continue

        scores = np.array([
            jaccard(l_set, inlinks_map.get(str(nu), set()))
            for nu in new_urls
        ])
        top_indices = np.argsort(scores)[::-1][:top_k]

        for idx in top_indices:
            rows.append({
                "legacy_url": lu,
                "candidate_url": new_urls[idx],
                "score": float(scores[idx]),
                "method": "inlinks",
            })

    return pd.DataFrame(rows) if rows else _empty_df()


# ---------------------------------------------------------------------------
# Mode B matchers
# ---------------------------------------------------------------------------

def _url_ancestor_score(legacy_url: str, collection_url: str) -> float:
    """Score how closely collection_url is an ancestor of legacy_url."""
    leg_segs = [s for s in urlparse(legacy_url).path.split("/") if s]
    col_segs = [s for s in urlparse(collection_url).path.split("/") if s]

    if not col_segs:
        return 0.0

    if leg_segs[: len(col_segs)] == col_segs:
        return 1.0

    shared = sum(1 for a, b in zip(leg_segs, col_segs) if a == b)
    if shared >= 2:
        return 0.7
    if shared >= 1:
        return 0.3
    return 0.0


def match_mode_b(
    retired_df: pd.DataFrame,
    collections_df: pd.DataFrame,
    inlinks_map: dict[str, set[str]],
    weights: dict[str, float] | None = None,
    top_k: int = 5,
) -> pd.DataFrame:
    """Score each retired URL against every candidate collection page.

    Signals: inlink overlap (Jaccard), URL ancestor, title/H1 TF-IDF, breadcrumb.
    Returns long DataFrame [legacy_url, candidate_url, score, method].
    """
    if retired_df.empty or collections_df.empty:
        return _empty_df()

    _default_weights = {
        "inlink_overlap": 0.40,
        "url_ancestor": 0.30,
        "title_h1_best": 0.20,
        "breadcrumb": 0.10,
    }
    w = weights or _default_weights

    retired_urls = list(retired_df["address"])
    coll_urls = list(collections_df["address"])

    # Pre-compute TF-IDF scores for title/H1 (best-of)
    title_df = match_tfidf(retired_df, collections_df, "title", "title", top_k=len(coll_urls))
    h1_df = match_tfidf(retired_df, collections_df, "h1", "h1", top_k=len(coll_urls))

    def _tfidf_score(lu: str, cu: str) -> float:
        t = title_df[(title_df["legacy_url"] == lu) & (title_df["candidate_url"] == cu)]
        h = h1_df[(h1_df["legacy_url"] == lu) & (h1_df["candidate_url"] == cu)]
        ts = float(t["score"].iloc[0]) if not t.empty else 0.0
        hs = float(h["score"].iloc[0]) if not h.empty else 0.0
        return max(ts, hs)

    has_breadcrumb = "breadcrumb" in retired_df.columns
    if not has_breadcrumb:
        # Redistribute breadcrumb weight to title_h1_best
        w = dict(w)
        w["title_h1_best"] = w.get("title_h1_best", 0.20) + w.pop("breadcrumb", 0.10)

    rows: list[dict] = []
    for lu in retired_urls:
        l_set = inlinks_map.get(str(lu), set())
        retired_row = retired_df[retired_df["address"] == lu]

        scores: list[tuple[str, float]] = []
        for cu in coll_urls:
            inlink_score = jaccard(l_set, inlinks_map.get(str(cu), set())) if l_set else 0.0
            ancestor_score = _url_ancestor_score(lu, cu)
            tfidf_score = _tfidf_score(lu, cu)

            breadcrumb_score = 0.0
            if has_breadcrumb and not retired_row.empty:
                bc = str(retired_row["breadcrumb"].iloc[0])
                breadcrumb_score = 1.0 if cu in bc else 0.0

            combined = (
                inlink_score * w.get("inlink_overlap", 0.40)
                + ancestor_score * w.get("url_ancestor", 0.30)
                + tfidf_score * w.get("title_h1_best", 0.20)
                + breadcrumb_score * w.get("breadcrumb", 0.10)
            )
            scores.append((cu, combined))

        scores.sort(key=lambda x: x[1], reverse=True)
        for cu, sc in scores[:top_k]:
            rows.append({
                "legacy_url": lu,
                "candidate_url": cu,
                "score": sc,
                "method": "mode_b",
            })

    return pd.DataFrame(rows) if rows else _empty_df()
