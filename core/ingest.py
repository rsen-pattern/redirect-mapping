"""Ingest Screaming Frog crawl exports and retired URL lists."""

import io

import pandas as pd

from core.schema import (
    CANONICAL_COLUMNS,
    REQUIRED_COLUMNS,
    SCREAMING_FROG_ALIASES,
    _OPTIONAL_NUMERIC_COLS,
    _OPTIONAL_TEXT_COLS,
)


def read_crawl(file) -> pd.DataFrame:
    """Read a Screaming Frog crawl CSV or XLSX upload.

    Accepts a Streamlit UploadedFile or any file-like object / path string.
    Preserves original column names — call auto_map_columns + apply_mapping to normalise.
    """
    name = getattr(file, "name", "") or ""
    if hasattr(file, "read"):
        content = file.read()
    else:
        with open(file, "rb") as fh:
            content = fh.read()

    if name.lower().endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(content), engine="openpyxl")

    return pd.read_csv(
        io.BytesIO(content),
        encoding="utf-8-sig",
        encoding_errors="replace",
        low_memory=False,
    )


def auto_map_columns(df: pd.DataFrame) -> tuple[dict, list[str]]:
    """Auto-map Screaming Frog column headers to canonical names.

    Returns (mapping dict, list of missing required canonical columns).
    """
    stripped = {col.strip(): col for col in df.columns}
    mapping: dict[str, str] = {}
    for raw, canonical in SCREAMING_FROG_ALIASES.items():
        matched_original = stripped.get(raw)
        if matched_original is not None:
            mapping[matched_original] = canonical

    mapped_canonical = set(mapping.values())
    missing_required = [r for r in REQUIRED_COLUMNS if r not in mapped_canonical]
    return mapping, missing_required


def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Rename columns per mapping, keep only mapped canonical columns.

    Fills missing optional text columns with '' and numeric columns with 0.
    """
    df = df.rename(columns=mapping)
    mapped = list(mapping.values())
    keep = [c for c in mapped if c in df.columns]
    df = df[keep].copy()

    for col in _OPTIONAL_TEXT_COLS:
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].fillna("").astype(str)

    for col in _OPTIONAL_NUMERIC_COLS:
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    for col in ["address", "title", "h1", "h1_2", "h2", "h2_2", "meta_description"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df.reset_index(drop=True)


def filter_html_200(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows where content_type contains 'html' and status_code == 200.

    Skips each filter silently if the respective column is absent.
    """
    if "status_code" in df.columns:
        df = df[pd.to_numeric(df["status_code"], errors="coerce") == 200]
    if "content_type" in df.columns:
        df = df[df["content_type"].astype(str).str.contains("html", case=False, na=False)]
    return df.reset_index(drop=True)


def load_retired_urls(file) -> pd.DataFrame:
    """Load a retired URL list from plain text (one URL per line) or CSV.

    CSV: finds a column named 'url' (case-insensitive) and keeps all columns.
    Plain text: returns a single-column DataFrame with column 'url'.
    """
    if hasattr(file, "read"):
        content = file.read()
    else:
        with open(file, "rb") as fh:
            content = fh.read()

    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    # Try CSV first
    try:
        df = pd.read_csv(io.StringIO(content))
        url_col = next(
            (c for c in df.columns if c.strip().lower() == "url"),
            None,
        )
        if url_col is not None:
            df = df.rename(columns={url_col: "url"})
            return df
    except Exception:
        pass

    # Fall back to plain-text list
    urls = [line.strip() for line in content.splitlines() if line.strip()]
    return pd.DataFrame({"url": urls})
