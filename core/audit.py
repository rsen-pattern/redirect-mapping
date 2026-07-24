"""Post-mapping SEO audits for a redirect map.

These operate on a finished ``winners_df`` (columns ``legacy_url``,
``candidate_url``, ``tier`` …) and surface the mistakes the SEO literature
warns about: redirect chains and loops, homepage / soft-404 dumping, forgotten
non-HTML resources, and high-value pages left without a good target.
"""

from __future__ import annotations

import pandas as pd

from core.urls import canonicalize_url

# A target receiving at least this many distinct sources is flagged as a
# many-to-one concentration (soft-404 risk).
DEFAULT_MANY_TO_ONE_THRESHOLD = 10


def _is_root(canonical_url: str) -> bool:
    """True if the canonical URL points at a site root ('' or '/' path)."""
    from urllib.parse import urlparse

    try:
        path = urlparse(canonical_url).path
    except Exception:
        path = canonical_url
    return path in ("", "/")


def _priority_field(legacy_df: pd.DataFrame) -> str | None:
    for col in ("unique_inlinks", "inlinks"):
        if col in legacy_df.columns:
            return col
    return None


def add_priority(winners_df: pd.DataFrame, legacy_df: pd.DataFrame) -> pd.DataFrame:
    """Attach a ``priority`` column to winners from the legacy crawl's inlinks.

    Inlink count is a proxy for internal authority — the pages the old site
    linked to most. No-op (priority 0) when the crawl carries no inlink data.
    """
    df = winners_df.copy()
    field = _priority_field(legacy_df) if legacy_df is not None and not legacy_df.empty else None
    if field is None or "legacy_url" not in df.columns:
        df["priority"] = 0
        return df

    prio_map = dict(
        zip(
            legacy_df["address"].astype(str),
            pd.to_numeric(legacy_df[field], errors="coerce").fillna(0).astype(int),
        )
    )
    df["priority"] = df["legacy_url"].astype(str).map(prio_map).fillna(0).astype(int)
    return df


def run_audit(
    winners_df: pd.DataFrame,
    legacy_df: pd.DataFrame | None = None,
    new_df: pd.DataFrame | None = None,
    many_to_one_threshold: int = DEFAULT_MANY_TO_ONE_THRESHOLD,
) -> pd.DataFrame:
    """Add ``priority`` and ``flags`` columns to a redirect map.

    Flags (``;``-joined per row):
      - ``loop``                    — target equals the source URL
      - ``chain``                   — target is itself a source in the map
                                      (``/old → /new → /newer``)
      - ``target_not_in_new_crawl`` — target absent from the new crawl (only
                                      checked when ``new_df`` is supplied)
      - ``homepage_redirect``       — target is the site root (soft-404 risk)
      - ``many_to_one:N``           — target receives N ≥ threshold sources
      - ``high_value_unmatched``    — a high-priority page left in review/no_match
    """
    if winners_df is None or winners_df.empty:
        out = winners_df.copy() if winners_df is not None else pd.DataFrame()
        if "priority" not in out.columns:
            out["priority"] = pd.Series(dtype=int)
        if "flags" not in out.columns:
            out["flags"] = pd.Series(dtype=str)
        return out

    df = add_priority(winners_df, legacy_df if legacy_df is not None else pd.DataFrame())

    src_canon = df["legacy_url"].astype(str).map(canonicalize_url)
    tgt_canon = df["candidate_url"].astype(str).map(canonicalize_url) if "candidate_url" in df.columns else pd.Series([""] * len(df))

    source_set = set(src_canon)
    target_counts = tgt_canon[tgt_canon != ""].value_counts().to_dict()
    new_set = (
        {canonicalize_url(u) for u in new_df["address"]}
        if new_df is not None and not new_df.empty and "address" in new_df.columns
        else None
    )

    # High-value threshold: 90th percentile of positive priorities.
    positive = df.loc[df["priority"] > 0, "priority"]
    hv_threshold = float(positive.quantile(0.90)) if not positive.empty else 0.0

    tiers = df["tier"] if "tier" in df.columns else pd.Series([""] * len(df), index=df.index)

    flags: list[str] = []
    for i, (src, tgt) in enumerate(zip(src_canon, tgt_canon)):
        row_flags: list[str] = []
        if tgt:
            if tgt == src:
                row_flags.append("loop")
            elif tgt in source_set:
                row_flags.append("chain")
            if new_set is not None and tgt not in new_set:
                row_flags.append("target_not_in_new_crawl")
            if _is_root(tgt):
                row_flags.append("homepage_redirect")
            count = target_counts.get(tgt, 0)
            if count >= many_to_one_threshold:
                row_flags.append(f"many_to_one:{count}")

        prio = int(df.iloc[i]["priority"])
        tier = str(tiers.iloc[i])
        if hv_threshold > 0 and prio >= hv_threshold and tier in ("review", "no_match"):
            row_flags.append("high_value_unmatched")

        flags.append(";".join(row_flags))

    df["flags"] = flags
    return df


def build_asset_report(mapped_df: pd.DataFrame) -> pd.DataFrame:
    """Non-HTML 200 resources (PDFs, images, …) from a mapped crawl.

    These are dropped by ``filter_html_200`` but still need redirects during a
    migration, so surface them separately. Expects a crawl DataFrame *before*
    the HTML-200 filter (i.e. straight out of ``apply_mapping``).

    Returns ``[address, content_type]``.
    """
    cols = ["address", "content_type"]
    if mapped_df is None or mapped_df.empty or "address" not in mapped_df.columns:
        return pd.DataFrame(columns=cols)

    out = mapped_df
    if "status_code" in out.columns:
        out = out[pd.to_numeric(out["status_code"], errors="coerce") == 200]
    if "content_type" in out.columns:
        out = out[~out["content_type"].astype(str).str.contains("html", case=False, na=False)]
        out = out[out["content_type"].astype(str).str.strip() != ""]
    else:
        return pd.DataFrame(columns=cols)

    return out[["address", "content_type"]].reset_index(drop=True)
