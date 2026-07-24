"""Redirect mapper business logic (shared by Flask routes)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.ai_layer import disambiguate_batch
from core.collections import (
    combine_collection_sets,
    detect_collections_auto,
    detect_collections_by_pattern,
    detect_collections_by_segment,
)
from core.export import build_high_confidence_csv, build_json, build_review_xlsx
from core.ingest import (
    apply_mapping,
    auto_map_columns,
    canonicalize_crawl,
    filter_html_200,
    load_retired_urls,
    read_crawl,
)
from core.audit import run_audit
from core.inlinks import load_inlinks
from core.matchers import (
    match_h1,
    match_h2,
    match_inlinks,
    match_mode_b,
    match_path,
    match_slug,
    match_title,
)
from core.scoring import (
    DEFAULT_WEIGHTS_MODE_A,
    DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS,
    assign_tier,
    combine_matcher_results,
    exact_slug_prepass,
    pick_winners,
)


def load_models_config() -> dict:
    with open(Path(__file__).parent.parent / "config" / "models.json") as f:
        return json.load(f)


def default_config(use_inlinks: bool = False) -> dict[str, Any]:
    weights = DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS if use_inlinks else DEFAULT_WEIGHTS_MODE_A
    return {
        "mode": "migration",
        "exact_slug_enabled": True,
        "ai_enabled": False,
        "max_workers": 5,
        "use_inlinks": use_inlinks,
        "weights": dict(weights),
        "model": load_models_config()["default"],
    }


def ingest_upload(file_bytes: bytes, filename: str) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    raw_df = read_crawl(io.BytesIO(file_bytes), filename=filename)
    mapping, missing_required = auto_map_columns(raw_df)
    return raw_df, mapping, missing_required


def apply_ingest(
    raw_df: pd.DataFrame,
    mapping: dict[str, str],
) -> pd.DataFrame:
    df = apply_mapping(raw_df, mapping)
    df = filter_html_200(df)
    return canonicalize_crawl(df)


def load_inlinks_from_bytes(file_bytes: bytes) -> tuple[dict[str, set[str]], pd.DataFrame]:
    inlinks_map = load_inlinks(io.BytesIO(file_bytes))
    inlinks_df = pd.read_csv(
        io.BytesIO(file_bytes),
        encoding="utf-8-sig",
        encoding_errors="replace",
        usecols=lambda c: c in ["Source", "Destination"],
        low_memory=False,
    )
    return inlinks_map, inlinks_df


def run_mode_a_matching(
    legacy_df: pd.DataFrame,
    new_df: pd.DataFrame,
    cfg: dict[str, Any],
    inlinks_df: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    weights = cfg["weights"]
    pre_pass_df = pd.DataFrame()
    remaining_legacy = legacy_df

    if cfg.get("exact_slug_enabled", True):
        pre_pass_df, remaining_legacy, _ = exact_slug_prepass(legacy_df, new_df)

    matcher_dfs = []
    if not remaining_legacy.empty:
        matcher_dfs.append(match_path(remaining_legacy, new_df))
        matcher_dfs.append(match_slug(remaining_legacy, new_df))
        matcher_dfs.append(match_title(remaining_legacy, new_df))
        matcher_dfs.append(match_h1(remaining_legacy, new_df))
        matcher_dfs.append(match_h2(remaining_legacy, new_df))

        if cfg.get("use_inlinks") and inlinks_df is not None and not inlinks_df.empty:
            matcher_dfs.append(match_inlinks(remaining_legacy, new_df, inlinks_df))

    combined_df = combine_matcher_results(matcher_dfs, weights)
    winners_df = pick_winners(combined_df)

    if not pre_pass_df.empty:
        pre_pass_winners = pre_pass_df.rename(columns={"score": "combined_score"})
        pre_pass_winners["second_score"] = 0.0
        pre_pass_winners["methods_contributed"] = "exact_slug"
        pre_pass_winners["is_ambiguous"] = False
        pre_pass_winners["tier"] = "high"
        winners_df = pd.concat([pre_pass_winners, winners_df], ignore_index=True)

    winners_df = run_audit(winners_df, legacy_df, new_df)

    return winners_df, combined_df


def build_collections_df(
    site_df: pd.DataFrame,
    bucket: dict[str, Any],
    patterns_text: str,
    segment_bytes: bytes | None,
    segment_name: str,
    use_patterns: bool,
    use_segment: bool,
    use_auto: bool,
) -> pd.DataFrame | None:
    collection_sets: list[set[str]] = []

    if use_patterns and patterns_text.strip():
        patterns = [p.strip() for p in patterns_text.splitlines() if p.strip()]
        collection_sets.append(detect_collections_by_pattern(site_df, patterns))

    if use_segment and segment_bytes:
        seg_df = pd.read_csv(io.BytesIO(segment_bytes), encoding="utf-8-sig", encoding_errors="replace")
        collection_sets.append(detect_collections_by_segment(seg_df, segment_name))

    if use_auto:
        auto_set = bucket.get("collection_set")
        if auto_set is None:
            auto_set = detect_collections_auto(site_df)
            bucket["collection_set"] = auto_set
        collection_sets.append(auto_set)

    if not collection_sets:
        return None

    combined_set = combine_collection_sets(*collection_sets)
    collections_df = site_df[site_df["address"].isin(combined_set)].reset_index(drop=True)
    return collections_df if not collections_df.empty else None


def ai_fallback_message(ai_df: pd.DataFrame | None) -> str | None:
    if ai_df is None or ai_df.empty or "fallback_fired" not in ai_df.columns:
        return None
    fallback_rows = ai_df[ai_df["fallback_fired"]]
    if fallback_rows.empty:
        return None
    models_used = fallback_rows["model_used"].unique().tolist()
    return f"Fell back to {models_used} on {len(fallback_rows)} rows."


def run_mode_b_matching(
    retired_df: pd.DataFrame,
    site_df: pd.DataFrame,
    collections_df: pd.DataFrame,
    inlinks_map: dict[str, set[str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    retired_enriched = (
        retired_df.merge(
            site_df[["address", "title", "h1", "meta_description"]],
            left_on="url",
            right_on="address",
            how="left",
        )
        .drop(columns=["address"])
        .rename(columns={"url": "address"})
    )
    long_df = match_mode_b(retired_enriched, collections_df, inlinks_map)
    combined_df = long_df.rename(columns={"score": "combined_score"})
    combined_df["methods"] = "mode_b"
    winners_df = pick_winners(combined_df)
    return winners_df, combined_df


def run_ai_tiebreak(
    api_key: str,
    mode: str,
    results_df: pd.DataFrame,
    combined_df: pd.DataFrame,
    max_workers: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ambiguous_df = results_df[results_df["is_ambiguous"]].copy()
    ai_df = disambiguate_batch(
        api_key=api_key,
        mode=mode,
        ambiguous_df=ambiguous_df,
        results_df=results_df,
        combined_df=combined_df,
        max_workers=max_workers,
    )

    results_merged = results_df.copy()
    if not ai_df.empty:
        for _, ai_row in ai_df.iterrows():
            mask = results_merged["legacy_url"] == ai_row["legacy_url"]
            results_merged.loc[mask, "candidate_url"] = ai_row["winner_url"]
            results_merged.loc[mask, "methods_contributed"] = f"AI ({ai_row['model_used']})"
            results_merged.loc[mask, "is_ambiguous"] = False
            if "tier" in results_merged.columns:
                results_merged.loc[mask, "tier"] = assign_tier(float(ai_row["confidence"]))

    return results_merged, ai_df


def results_summary(results_df: pd.DataFrame) -> dict[str, int]:
    if results_df is None or results_df.empty:
        return {
            "pre_pass": 0,
            "high": 0,
            "review": 0,
            "no_match": 0,
            "ambiguous": 0,
        }

    methods = results_df.get("methods_contributed", pd.Series(dtype=str))
    tiers = results_df.get("tier", pd.Series(dtype=str))
    return {
        "pre_pass": int((methods == "exact_slug").sum()) if "methods_contributed" in results_df.columns else 0,
        "high": int((tiers == "high").sum()) if "tier" in results_df.columns else 0,
        "review": int((tiers == "review").sum()) if "tier" in results_df.columns else 0,
        "no_match": int((tiers == "no_match").sum()) if "tier" in results_df.columns else 0,
        "ambiguous": int(results_df["is_ambiguous"].sum()) if "is_ambiguous" in results_df.columns else 0,
    }


def export_file(
    export_format: str,
    results_df: pd.DataFrame,
    ai_df: pd.DataFrame | None,
    mode: str,
) -> tuple[bytes, str, str]:
    if export_format == "xlsx":
        return (
            build_review_xlsx(results_df, ai_df, mode),
            "redirect_map.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if export_format == "csv":
        return (
            build_high_confidence_csv(results_df),
            "high_confidence_redirects.csv",
            "text/csv",
        )
    return (
        build_json(results_df, ai_df),
        "redirect_map.json",
        "application/json",
    )
