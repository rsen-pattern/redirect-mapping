"""Inlink graph operations — builds URL→sources index from Screaming Frog bulk inlinks export."""

from collections import defaultdict

import pandas as pd


def load_inlinks(file) -> dict[str, set[str]]:
    """Stream-read a Screaming Frog All Inlinks export.

    Returns {destination_url: set(source_urls)}.
    Reads in 50k-row chunks to handle 1M+ row exports without loading into RAM at once.
    """
    if hasattr(file, "read"):
        content = file.read()
        import io
        buf = io.BytesIO(content)
    else:
        buf = file

    index: dict[str, set[str]] = defaultdict(set)

    for chunk in pd.read_csv(
        buf,
        encoding="utf-8-sig",
        encoding_errors="replace",
        low_memory=False,
        chunksize=50_000,
    ):
        src_col = _find_col(chunk.columns, ["Source", "source"])
        dst_col = _find_col(chunk.columns, ["Destination", "destination"])
        if src_col is None or dst_col is None:
            continue
        for src, dst in zip(chunk[src_col], chunk[dst_col]):
            if pd.notna(src) and pd.notna(dst):
                index[str(dst)].add(str(src))

    return dict(index)


def build_inlinks_from_df(df: pd.DataFrame) -> dict[str, set[str]]:
    """Build inlink index from an already-loaded DataFrame.

    Useful in tests where the full file is already in memory.
    """
    index: dict[str, set[str]] = defaultdict(set)
    src_col = _find_col(df.columns, ["Source", "source"])
    dst_col = _find_col(df.columns, ["Destination", "destination"])
    if src_col is None or dst_col is None:
        return {}
    for src, dst in zip(df[src_col], df[dst_col]):
        if pd.notna(src) and pd.notna(dst):
            index[str(dst)].add(str(src))
    return dict(index)


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union)


def _find_col(columns, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    return None
