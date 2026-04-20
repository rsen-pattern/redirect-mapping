"""SEO Redirect Mapper — Streamlit entry point."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SEO Redirect Mapper",
    page_icon="🔀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Lazy imports (avoid importing heavy deps before page renders)
# ---------------------------------------------------------------------------
from core.ingest import (
    apply_mapping,
    auto_map_columns,
    filter_html_200,
    load_retired_urls,
    read_crawl,
)
from core.schema import CANONICAL_COLUMNS, REQUIRED_COLUMNS, SCREAMING_FROG_ALIASES
from core.scoring import (
    DEFAULT_WEIGHTS_MODE_A,
    DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS,
    assign_tier,
    combine_matcher_results,
    exact_slug_prepass,
    pick_winners,
)
from core.matchers import (
    match_h1,
    match_h2,
    match_inlinks,
    match_mode_b,
    match_path,
    match_slug,
    match_title,
)
from core.collections import (
    combine_collection_sets,
    detect_collections_auto,
    detect_collections_by_pattern,
    detect_collections_by_segment,
)
from core.inlinks import load_inlinks
from core.ai_layer import disambiguate_batch
from core.export import build_high_confidence_csv, build_json, build_review_xlsx
from utils.bifrost import get_api_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_read_crawl(file_bytes: bytes, filename: str) -> pd.DataFrame:
    # Pass filename explicitly: BytesIO has no .name, so XLSX detection requires it.
    return read_crawl(io.BytesIO(file_bytes), filename=filename)


@st.cache_data(show_spinner=False)
def _cached_load_inlinks(file_bytes: bytes) -> dict:
    import io as _io
    from core.inlinks import load_inlinks as _load
    result = _load(_io.BytesIO(file_bytes))
    # Convert sets to lists for JSON-serialisation compatibility with cache
    return {k: list(v) for k, v in result.items()}


def _inlinks_dict_to_sets(d: dict) -> dict[str, set[str]]:
    return {k: set(v) for k, v in d.items()}


def _load_models_config() -> dict:
    with open(Path(__file__).parent / "config" / "models.json") as f:
        return json.load(f)


def _init_session_state() -> None:
    defaults = {
        "mode": "migration",
        "legacy_df": None,
        "new_df": None,
        "inlinks_df": None,
        "inlinks_map": None,
        "retired_df": None,
        "collection_set": None,
        "results_df": None,
        "combined_df": None,
        "ai_df": None,
        "legacy_mapping": None,
        "new_mapping": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _upload_and_ingest(label: str, key: str, mapping_key: str) -> pd.DataFrame | None:
    """Render a file uploader, run ingest, show column-mapper if needed, return filtered DF."""
    uploaded = st.file_uploader(label, type=["csv", "xlsx"], key=f"uploader_{key}")
    if uploaded is None:
        return None

    file_bytes = uploaded.read()
    raw_df = _cached_read_crawl(file_bytes, uploaded.name)

    mapping, missing_required = auto_map_columns(raw_df)
    st.session_state[mapping_key] = mapping

    if missing_required:
        st.warning(f"Could not auto-detect columns: {missing_required}. Please map them below.")
        with st.expander("Column mapper", expanded=True):
            col_options = ["(skip)"] + list(raw_df.columns)
            for req in missing_required:
                chosen = st.selectbox(
                    f"Map canonical '{req}' to:",
                    col_options,
                    key=f"map_{key}_{req}",
                )
                if chosen != "(skip)":
                    mapping[chosen] = req
            st.session_state[mapping_key] = mapping

    df = apply_mapping(raw_df, mapping)
    df = filter_html_200(df)

    if df.empty:
        st.error("No HTML-200 rows found after filtering. Check your file.")
        return None

    st.success(f"Loaded {len(df):,} HTML-200 rows.")
    st.dataframe(df.head(5), use_container_width=True)
    return df


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar(models_config: dict) -> dict:
    """Render sidebar controls. Returns a config dict."""
    cfg: dict = {}

    st.sidebar.header("⚙️ Configuration")

    api_key_input = st.sidebar.text_input(
        "Bi Frost API Key",
        type="password",
        help="Your Bi Frost API key. Also readable from BIFROST_API_KEY env var or st.secrets.",
    )
    if api_key_input:
        st.session_state["bifrost_api_key"] = api_key_input
    # Fall back to secrets/env so Streamlit Cloud deployments work without sidebar input.
    cfg["api_key"] = st.session_state.get("bifrost_api_key") or get_api_key() or ""

    model_options = [(m["label"], m["id"]) for m in models_config["models"]]
    model_labels = [m[0] for m in model_options]
    default_idx = next(
        (i for i, m in enumerate(model_options) if m[1] == models_config["default"]), 0
    )
    selected_label = st.sidebar.selectbox("Model", model_labels, index=default_idx)
    cfg["model"] = next(m[1] for m in model_options if m[0] == selected_label)

    st.sidebar.divider()

    cfg["exact_slug_enabled"] = st.sidebar.checkbox(
        "Exact-slug pre-pass",
        value=True,
        help="Instantly resolves URLs whose last path segment uniquely matches a new URL slug. "
             "Resolves 50–80% of e-commerce migrations before any expensive matchers run.",
    )

    cfg["ai_enabled"] = st.sidebar.toggle("Enable AI tiebreak", value=False)
    if cfg["ai_enabled"]:
        cfg["max_workers"] = st.sidebar.slider("AI concurrency", 1, 10, 5)
    else:
        cfg["max_workers"] = 5

    cfg["use_inlinks"] = st.sidebar.checkbox(
        "Include inlink overlap (requires inlinks upload)",
        value=False,
    )

    with st.sidebar.expander("Signal weights"):
        base_weights = (
            DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS
            if cfg["use_inlinks"]
            else DEFAULT_WEIGHTS_MODE_A
        )
        raw_weights: dict[str, float] = {}
        for name, default in base_weights.items():
            raw_weights[name] = st.slider(name, 0.0, 1.0, default, 0.05, key=f"w_{name}")

        total = sum(raw_weights.values())
        if total > 0:
            cfg["weights"] = {k: v / total for k, v in raw_weights.items()}
        else:
            cfg["weights"] = base_weights

    return cfg


# ---------------------------------------------------------------------------
# Mode A — Site Migration
# ---------------------------------------------------------------------------

def _render_instructions_mode_a() -> None:
    with st.expander("📖 How to use — Site Migration", expanded=False):
        st.markdown("""
**Use this mode when:** you're moving URLs from an old site to a new one (replatform, URL restructure, domain change).

**Step 1 — Screaming Frog exports**
- Legacy crawl: *Internal → HTML → Export* on the old site
- New crawl: same on the new site (crawl staging if pre-launch)
- Inlinks *(optional)*: *Bulk Export → All Inlinks* — enables a 6th signal for harder cases

**Step 2 — Upload and configure**
- Upload both crawls above. Column names are auto-detected from Screaming Frog defaults.
- Sidebar defaults are sensible for most migrations. Key knobs:
  - **Exact-slug pre-pass** — resolves 50–80% of URLs instantly at 1.0 confidence. Leave on.
  - **Signal weights** — drop H1 weight if your H1s are templated; push path/slug up if URL structure is deliberately preserved.

**Step 3 — Run and read results**
- **Pre-pass resolved** — done, don't touch.
- **High confidence ≥ 0.90** — safe to redirect in bulk; spot-check 10 random rows.
- **Needs review 0.70–0.90** — manual eyeballs required, or run AI tiebreak.
- **No match < 0.70** — likely no good target; consider 410 Gone or a category/homepage redirect.
- **Ambiguous** — top two candidates too close to call; AI tiebreak handles these.

**Step 4 — Export**
- **High-confidence CSV** → feed directly into your redirect system (htaccess, Cloudflare, CMS rules).
- **Full review XLSX** → multi-sheet workbook for client review (per-tier sheets + per-matcher sheets).
- **JSON** → archive or scripting.
        """)


def _render_instructions_mode_b() -> None:
    with st.expander("📖 How to use — Product Retirement", expanded=False):
        st.markdown("""
**Use this mode when:** dead URLs on a live site need to redirect to their closest living parent (e.g. out-of-stock products → category pages).

**Step 1 — Screaming Frog exports**
- Site crawl: *Internal → HTML → Export* on the live site
- Inlinks: *Bulk Export → All Inlinks* — **required** for Mode B scoring
- Retired URL list: plain `.txt` (one URL per line) or CSV with a `url` column

**Step 2 — Define collection pages** *(the redirect targets)*

Pick one or more detection methods:
- **URL patterns** — glob patterns like `/collections/*` or `/category/*`
- **Segment upload** — CSV from Screaming Frog Segments with `url` + `segment` columns
- **Auto-detect** — heuristic: high outlinks + above-median inlinks + shallow depth

Preview the detected set before running; deselect false positives.

**Step 3 — Scoring signals**

Each retired URL is scored against every collection on:

| Signal | Weight | What it measures |
|---|---|---|
| Inlink overlap (Jaccard) | 0.40 | Shared inlink sources |
| URL ancestor | 0.30 | Is collection a path ancestor? |
| Title/H1 TF-IDF | 0.20 | Semantic similarity |
| Breadcrumb | 0.10 | Collection in page breadcrumb |

**Step 4 — Read and export** — same tier logic as Mode A.
High-confidence CSV is directly actionable. Needs Review rows are typically "which of two categories is the right parent" — run AI tiebreak to resolve.
        """)


def _render_mode_a(cfg: dict) -> None:
    st.header("Site Migration — Redirect Map")
    _render_instructions_mode_a()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Legacy site crawl")
        legacy_df = _upload_and_ingest("Upload legacy crawl (CSV/XLSX)", "legacy", "legacy_mapping")
        if legacy_df is not None:
            st.session_state.legacy_df = legacy_df

    with col2:
        st.subheader("New site crawl")
        new_df = _upload_and_ingest("Upload new crawl (CSV/XLSX)", "new", "new_mapping")
        if new_df is not None:
            st.session_state.new_df = new_df

    inlinks_df: pd.DataFrame | None = None
    if cfg["use_inlinks"]:
        st.subheader("Inlinks export (optional)")
        inlinks_file = st.file_uploader(
            "Upload All Inlinks export (Screaming Frog Bulk Export → All Inlinks)",
            type=["csv"],
            key="uploader_inlinks",
        )
        if inlinks_file:
            file_bytes = inlinks_file.read()
            inlinks_map_raw = _cached_load_inlinks(file_bytes)
            st.session_state.inlinks_map = inlinks_map_raw
            st.success(f"Inlinks index built: {len(inlinks_map_raw):,} destination URLs.")
            # Keep a lightweight DataFrame for match_inlinks signature
            inlinks_df = pd.read_csv(
                io.BytesIO(file_bytes),
                encoding="utf-8-sig",
                encoding_errors="replace",
                usecols=lambda c: c in ["Source", "Destination"],
                low_memory=False,
            )
            st.session_state.inlinks_df = inlinks_df

    legacy_df = st.session_state.legacy_df
    new_df = st.session_state.new_df
    inlinks_df = st.session_state.get("inlinks_df")

    if legacy_df is None or new_df is None:
        st.info("Upload both crawl files to continue.")
        return

    # ---- Run matching ----
    if st.button("▶ Run mechanical matching", type="primary"):
        weights = cfg["weights"]
        pre_pass_df = pd.DataFrame()
        remaining_legacy = legacy_df

        with st.spinner("Running matching..."):
            if cfg["exact_slug_enabled"]:
                pre_pass_df, remaining_legacy, _ = exact_slug_prepass(legacy_df, new_df)

            matcher_dfs = []
            if not remaining_legacy.empty:
                matcher_dfs.append(match_path(remaining_legacy, new_df))
                matcher_dfs.append(match_slug(remaining_legacy, new_df))
                matcher_dfs.append(match_title(remaining_legacy, new_df))
                matcher_dfs.append(match_h1(remaining_legacy, new_df))
                matcher_dfs.append(match_h2(remaining_legacy, new_df))

                if cfg["use_inlinks"] and inlinks_df is not None and not inlinks_df.empty:
                    matcher_dfs.append(match_inlinks(remaining_legacy, new_df, inlinks_df))

            combined_df = combine_matcher_results(matcher_dfs, weights)
            st.session_state.combined_df = combined_df

            winners_df = pick_winners(combined_df)

            # Merge pre-pass rows
            if not pre_pass_df.empty:
                pre_pass_winners = pre_pass_df.rename(columns={"score": "combined_score"})
                pre_pass_winners["second_score"] = 0.0
                pre_pass_winners["methods_contributed"] = "exact_slug"
                pre_pass_winners["is_ambiguous"] = False
                pre_pass_winners["tier"] = "high"
                winners_df = pd.concat([pre_pass_winners, winners_df], ignore_index=True)

            st.session_state.results_df = winners_df

        st.success("Matching complete.")

    results_df = st.session_state.get("results_df")
    if results_df is not None and not results_df.empty:
        _render_results(results_df, cfg, mode="migration")


# ---------------------------------------------------------------------------
# Mode B — Product Retirement
# ---------------------------------------------------------------------------

def _render_mode_b(cfg: dict) -> None:
    st.header("Product Retirement — Collection Redirect Map")
    _render_instructions_mode_b()

    st.subheader("1. Site crawl")
    site_df = _upload_and_ingest("Upload site crawl (CSV/XLSX)", "site_b", "site_mapping_b")
    if site_df is not None:
        st.session_state.legacy_df = site_df

    st.subheader("2. Inlinks export")
    inlinks_file = st.file_uploader(
        "Upload All Inlinks (required for Mode B)",
        type=["csv"],
        key="uploader_inlinks_b",
    )
    if inlinks_file:
        file_bytes = inlinks_file.read()
        inlinks_map_raw = _cached_load_inlinks(file_bytes)
        st.session_state.inlinks_map = inlinks_map_raw
        st.success(f"Inlinks index built: {len(inlinks_map_raw):,} destination URLs.")

    st.subheader("3. Retired URLs")
    retired_file = st.file_uploader(
        "Upload retired URL list (plain text or CSV with 'url' column)",
        type=["csv", "txt"],
        key="uploader_retired",
    )
    if retired_file:
        retired_df = load_retired_urls(retired_file)
        st.session_state.retired_df = retired_df
        st.success(f"Loaded {len(retired_df):,} retired URLs.")
        st.dataframe(retired_df.head(5), use_container_width=True)

    site_df = st.session_state.get("legacy_df")
    if site_df is None:
        st.info("Upload the site crawl to continue.")
        return

    # ---- Collection detection ----
    st.subheader("4. Define collection pages")
    detection_methods = st.multiselect(
        "Collection detection methods",
        ["URL patterns", "Segment upload", "Auto-detect"],
        default=["Auto-detect"],
    )

    collection_sets: list[set[str]] = []

    if "URL patterns" in detection_methods:
        patterns_input = st.text_area(
            "URL path patterns (one per line, glob-style)",
            placeholder="/category/*\n/collections/*\n/shop/*/",
        )
        if patterns_input.strip():
            patterns = [p.strip() for p in patterns_input.splitlines() if p.strip()]
            collection_sets.append(detect_collections_by_pattern(site_df, patterns))

    if "Segment upload" in detection_methods:
        seg_file = st.file_uploader("Upload Segments CSV (url, segment)", type=["csv"], key="seg_upload")
        if seg_file:
            seg_df = pd.read_csv(seg_file, encoding="utf-8-sig", encoding_errors="replace")
            seg_name = st.text_input("Segment name to treat as collection", value="collection")
            collection_sets.append(detect_collections_by_segment(seg_df, seg_name))

    if "Auto-detect" in detection_methods:
        if st.button("Run auto-detection"):
            auto_set = detect_collections_auto(site_df)
            st.session_state.collection_set = auto_set
            st.success(f"Auto-detected {len(auto_set):,} collection pages.")
        if st.session_state.get("collection_set"):
            collection_sets.append(st.session_state.collection_set)

    if collection_sets:
        combined_set = combine_collection_sets(*collection_sets)
        collections_df = site_df[site_df["address"].isin(combined_set)].reset_index(drop=True)
        if not collections_df.empty:
            st.info(f"Collection pool: {len(collections_df):,} pages")
            with st.expander("Preview collection pages"):
                selected = st.dataframe(
                    collections_df[["address", "title", "h1"]].head(50),
                    use_container_width=True,
                )
        else:
            collections_df = None
    else:
        collections_df = None

    retired_df = st.session_state.get("retired_df")
    inlinks_map_raw = st.session_state.get("inlinks_map", {})
    inlinks_map = _inlinks_dict_to_sets(inlinks_map_raw) if inlinks_map_raw else {}

    if retired_df is None:
        st.info("Upload retired URL list to continue.")
        return

    if collections_df is None or collections_df.empty:
        st.info("Define at least one collection detection method to continue.")
        return

    # Join retired_df with site_df for title/H1
    retired_enriched = retired_df.merge(
        site_df[["address", "title", "h1", "meta_description"]],
        left_on="url",
        right_on="address",
        how="left",
    ).rename(columns={"url": "address"})
    if "address_x" in retired_enriched.columns:
        retired_enriched = retired_enriched.rename(columns={"address_x": "address"})

    if st.button("▶ Run Mode B matching", type="primary"):
        with st.spinner("Matching retired products to collections..."):
            long_df = match_mode_b(retired_enriched, collections_df, inlinks_map)
            combined_df = long_df.rename(columns={"score": "combined_score"})
            combined_df["methods"] = "mode_b"

            winners_df = pick_winners(combined_df.rename(columns={"methods": "methods_contributed"}))
            st.session_state.results_df = winners_df
            st.session_state.combined_df = combined_df

        st.success("Mode B matching complete.")

    results_df = st.session_state.get("results_df")
    if results_df is not None and not results_df.empty:
        _render_results(results_df, cfg, mode="retirement")


# ---------------------------------------------------------------------------
# Results display
# ---------------------------------------------------------------------------

def _render_results(results_df: pd.DataFrame, cfg: dict, mode: str) -> None:
    st.divider()
    st.subheader("Results")

    pre_pass_count = int((results_df.get("methods_contributed", pd.Series(dtype=str)) == "exact_slug").sum()) if "methods_contributed" in results_df.columns else 0
    high_count = int((results_df.get("tier", pd.Series(dtype=str)) == "high").sum()) if "tier" in results_df.columns else 0
    review_count = int((results_df.get("tier", pd.Series(dtype=str)) == "review").sum()) if "tier" in results_df.columns else 0
    no_match_count = int((results_df.get("tier", pd.Series(dtype=str)) == "no_match").sum()) if "tier" in results_df.columns else 0
    ambiguous_count = int(results_df["is_ambiguous"].sum()) if "is_ambiguous" in results_df.columns else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Pre-pass resolved", f"{pre_pass_count:,}")
    m2.metric("High confidence (≥0.90)", f"{high_count:,}")
    m3.metric("Needs review (0.70–0.90)", f"{review_count:,}")
    m4.metric("No match (<0.70)", f"{no_match_count:,}")
    m5.metric("Ambiguous (AI-eligible)", f"{ambiguous_count:,}")

    # Results table
    display_cols = [c for c in ["legacy_url", "candidate_url", "combined_score", "tier", "methods_contributed", "is_ambiguous"] if c in results_df.columns]
    col_config = {}
    if "combined_score" in results_df.columns:
        col_config["combined_score"] = st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=1, format="%.3f"
        )
    if "legacy_url" in results_df.columns:
        col_config["legacy_url"] = st.column_config.LinkColumn("Legacy URL")
    if "candidate_url" in results_df.columns:
        col_config["candidate_url"] = st.column_config.LinkColumn("Redirect Target")

    st.dataframe(
        results_df[display_cols],
        column_config=col_config,
        use_container_width=True,
    )

    # ---- AI tiebreak ----
    if cfg.get("ai_enabled") and ambiguous_count > 0:
        api_key = cfg.get("api_key", "")
        if not api_key:
            st.warning("Enter a Bi Frost API key in the sidebar to run AI tiebreak.")
        else:
            est_cost = ambiguous_count * 0.001
            st.info(f"AI tiebreak: {ambiguous_count} calls × ~$0.001 ≈ ${est_cost:.2f} (Haiku 4.5 estimate)")

            if st.button("🤖 Run AI tiebreak"):
                ambiguous_df = results_df[results_df["is_ambiguous"]].copy()
                combined_df = st.session_state.get("combined_df", pd.DataFrame())

                progress_bar = st.progress(0)
                status_text = st.empty()

                def _progress(done: int, total: int) -> None:
                    progress_bar.progress(done / total)
                    status_text.text(f"AI: {done}/{total} rows processed")

                ai_df = disambiguate_batch(
                    api_key=api_key,
                    mode=mode,
                    ambiguous_df=ambiguous_df,
                    results_df=results_df,
                    combined_df=combined_df,
                    max_workers=cfg.get("max_workers", 5),
                    progress_callback=_progress,
                )
                st.session_state.ai_df = ai_df

                # Merge AI decisions back into results
                if not ai_df.empty:
                    fallback_rows = ai_df[ai_df["fallback_fired"]]
                    if not fallback_rows.empty:
                        models_used = fallback_rows["model_used"].unique().tolist()
                        st.warning(f"Fell back to {models_used} on {len(fallback_rows)} rows.")

                    results_merged = results_df.copy()
                    for _, ai_row in ai_df.iterrows():
                        mask = results_merged["legacy_url"] == ai_row["legacy_url"]
                        results_merged.loc[mask, "candidate_url"] = ai_row["winner_url"]
                        results_merged.loc[mask, "methods_contributed"] = f"AI ({ai_row['model_used']})"
                        results_merged.loc[mask, "is_ambiguous"] = False
                        if "tier" in results_merged.columns:
                            results_merged.loc[mask, "tier"] = assign_tier(float(ai_row["confidence"]))

                    st.session_state.results_df = results_merged
                    st.success("AI tiebreak complete. Results updated.")
                    st.rerun()

    # ---- Export ----
    st.divider()
    st.subheader("Export")
    export_opts = st.multiselect(
        "Formats",
        ["Full review XLSX", "High-confidence CSV (htaccess-ready)", "Full JSON"],
        default=["Full review XLSX", "High-confidence CSV (htaccess-ready)", "Full JSON"],
    )

    if st.button("📥 Prepare downloads"):
        ai_df = st.session_state.get("ai_df")

        if "Full review XLSX" in export_opts:
            xlsx_bytes = build_review_xlsx(results_df, ai_df, mode)
            st.download_button(
                "Download redirect_map.xlsx",
                data=xlsx_bytes,
                file_name="redirect_map.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if "High-confidence CSV (htaccess-ready)" in export_opts:
            csv_bytes = build_high_confidence_csv(results_df)
            st.download_button(
                "Download high_confidence_redirects.csv",
                data=csv_bytes,
                file_name="high_confidence_redirects.csv",
                mime="text/csv",
            )

        if "Full JSON" in export_opts:
            json_bytes = build_json(results_df, ai_df)
            st.download_button(
                "Download redirect_map.json",
                data=json_bytes,
                file_name="redirect_map.json",
                mime="application/json",
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _init_session_state()
    models_config = _load_models_config()

    st.title("🔀 SEO Redirect Mapper")

    # Mode toggle
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "🔀 Site Migration",
            use_container_width=True,
            type="primary" if st.session_state.mode == "migration" else "secondary",
        ):
            st.session_state.mode = "migration"
            st.session_state.results_df = None
            st.rerun()
    with col2:
        if st.button(
            "🗑️ Product Retirement",
            use_container_width=True,
            type="primary" if st.session_state.mode == "retirement" else "secondary",
        ):
            st.session_state.mode = "retirement"
            st.session_state.results_df = None
            st.rerun()

    cfg = _render_sidebar(models_config)

    if st.session_state.mode == "migration":
        _render_mode_a(cfg)
    else:
        _render_mode_b(cfg)


if __name__ == "__main__":
    main()
