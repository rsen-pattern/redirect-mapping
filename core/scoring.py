"""Weighted combination, winner selection, ambiguity detection, tier assignment."""

from __future__ import annotations

from urllib.parse import urlparse

import pandas as pd

DEFAULT_WEIGHTS_MODE_A: dict[str, float] = {
    "path": 0.15,
    "slug": 0.15,
    "title": 0.25,
    "h1": 0.30,
    "h2": 0.15,
}

DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS: dict[str, float] = {
    "path": 0.10,
    "slug": 0.15,
    "title": 0.20,
    "h1": 0.25,
    "h2": 0.10,
    "inlinks": 0.20,
}


def _extract_slug(url: str) -> str:
    """Last non-empty path segment, lowercased."""
    try:
        path = urlparse(str(url)).path
    except Exception:
        path = str(url)
    segments = [s for s in path.split("/") if s]
    return segments[-1].lower() if segments else ""


def exact_slug_prepass(
    legacy: pd.DataFrame,
    new: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Resolve legacy URLs whose slug uniquely matches exactly one new URL.

    Returns (resolved_df, remaining_legacy_df, remaining_new_df).
    New URLs are NOT removed from remaining_new — many-to-one redirects are valid.
    Multi-match slugs (ambiguous) are kept in remaining_legacy for mechanical matchers.
    """
    _resolved_cols = ["legacy_url", "candidate_url", "score", "method"]

    if legacy.empty or new.empty:
        return (
            pd.DataFrame(columns=_resolved_cols),
            legacy.copy(),
            new.copy(),
        )

    # Build slug → list[new_url]
    slug_to_new: dict[str, list[str]] = {}
    for nu in new["address"]:
        slug = _extract_slug(str(nu))
        if slug:
            slug_to_new.setdefault(slug, []).append(str(nu))

    resolved_rows: list[dict] = []
    remaining_indices: list[int] = []

    for idx, row in legacy.iterrows():
        slug = _extract_slug(str(row["address"]))
        matches = slug_to_new.get(slug, [])
        if len(matches) == 1:
            resolved_rows.append({
                "legacy_url": row["address"],
                "candidate_url": matches[0],
                "score": 1.0,
                "method": "exact_slug",
            })
        else:
            remaining_indices.append(idx)

    resolved_df = pd.DataFrame(resolved_rows) if resolved_rows else pd.DataFrame(columns=_resolved_cols)
    remaining_legacy = legacy.loc[remaining_indices].reset_index(drop=True)

    return resolved_df, remaining_legacy, new.copy()


def combine_matcher_results(
    matcher_dfs: list[pd.DataFrame],
    weights: dict[str, float],
) -> pd.DataFrame:
    """Combine long candidate DataFrames from multiple matchers into a single weighted score.

    For each (legacy_url, candidate_url) pair: combined_score = Σ(score × weight) across
    all matchers that surfaced that candidate.
    """
    if not matcher_dfs:
        return pd.DataFrame(columns=["legacy_url", "candidate_url", "combined_score", "methods"])

    all_candidates = pd.concat(
        [df for df in matcher_dfs if not df.empty],
        ignore_index=True,
    )
    if all_candidates.empty:
        return pd.DataFrame(columns=["legacy_url", "candidate_url", "combined_score", "methods"])

    # Vectorised: map() broadcasts over the column in C, not Python row-by-row.
    all_candidates["weighted_score"] = (
        all_candidates["score"] * all_candidates["method"].map(weights).fillna(0.0)
    )

    grouped = all_candidates.groupby(["legacy_url", "candidate_url"]).agg(
        combined_score=("weighted_score", "sum"),
        methods=("method", lambda x: ",".join(sorted(set(x)))),
    ).reset_index()

    return grouped


def pick_winners(combined: pd.DataFrame) -> pd.DataFrame:
    """For each legacy_url, select the highest-scoring candidate.

    Adds columns: second_score, methods_contributed, is_ambiguous, tier.
    """
    if combined.empty:
        return pd.DataFrame(columns=[
            "legacy_url", "candidate_url", "combined_score", "second_score",
            "methods_contributed", "is_ambiguous", "tier",
        ])

    rows: list[dict] = []
    for legacy_url, group in combined.groupby("legacy_url"):
        sorted_group = group.sort_values("combined_score", ascending=False)
        top = sorted_group.iloc[0]
        second_score = float(sorted_group.iloc[1]["combined_score"]) if len(sorted_group) > 1 else 0.0
        top_score = float(top["combined_score"])

        methods = str(top.get("methods", ""))
        is_ambiguous = top_score < 0.90 or (top_score - second_score) < 0.05

        rows.append({
            "legacy_url": legacy_url,
            "candidate_url": top["candidate_url"],
            "combined_score": top_score,
            "second_score": second_score,
            "methods_contributed": methods,
            "is_ambiguous": is_ambiguous,
            "tier": assign_tier(top_score),
        })

    return pd.DataFrame(rows)


def assign_tier(score: float) -> str:
    """Assign a confidence tier label to a match score."""
    if score >= 0.90:
        return "high"
    if score >= 0.70:
        return "review"
    return "no_match"
