"""Tests for core/ingest.py."""

import io
from pathlib import Path

import pandas as pd
import pytest

from core.ingest import (
    apply_mapping,
    auto_map_columns,
    filter_html_200,
    load_retired_urls,
    read_crawl,
)
from core.schema import REQUIRED_COLUMNS, SCREAMING_FROG_ALIASES

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def _make_sf_df(**overrides) -> pd.DataFrame:
    """Build a minimal DataFrame with all Screaming Frog default headers."""
    base = {
        "Address": ["https://example.com/page"],
        "Status Code": [200],
        "Content Type": ["text/html; charset=utf-8"],
        "Indexability": ["Indexable"],
        "Title 1": ["Page Title"],
        "Meta Description 1": ["A description."],
        "H1-1": ["Page Heading"],
        "H1-2": [""],
        "H2-1": ["Sub Heading"],
        "H2-2": [""],
        "Word Count": [500],
        "Crawl Depth": [2],
        "Inlinks": [10],
        "Unique Inlinks": [8],
        "Outlinks": [5],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_auto_map_screaming_frog_headers():
    df = _make_sf_df()
    mapping, missing = auto_map_columns(df)
    assert missing == []
    assert mapping.get("Address") == "address"
    assert mapping.get("H1-1") == "h1"
    assert mapping.get("Title 1") == "title"
    assert mapping.get("Meta Description 1") == "meta_description"


def test_auto_map_missing_required_returns_list():
    df = _make_sf_df()
    df = df.drop(columns=["Address"])
    _mapping, missing = auto_map_columns(df)
    assert "address" in missing


def test_auto_map_all_required_present():
    df = _make_sf_df()
    _mapping, missing = auto_map_columns(df)
    for req in REQUIRED_COLUMNS:
        assert req not in missing


def test_filter_html_200_keeps_only_html_200():
    rows = [
        {"address": "https://example.com/a", "status_code": 200, "content_type": "text/html"},
        {"address": "https://example.com/b", "status_code": 301, "content_type": "text/html"},
        {"address": "https://example.com/c", "status_code": 200, "content_type": "text/xml"},
        {"address": "https://example.com/d", "status_code": 200, "content_type": "text/html; charset=utf-8"},
    ]
    df = pd.DataFrame(rows)
    result = filter_html_200(df)
    assert len(result) == 2
    assert all(result["status_code"] == 200)
    assert all(result["content_type"].str.contains("html"))


def test_filter_html_200_skips_missing_status_code():
    df = pd.DataFrame([{"address": "https://example.com/", "content_type": "text/html"}])
    result = filter_html_200(df)
    assert len(result) == 1


def test_filter_html_200_skips_missing_content_type():
    df = pd.DataFrame([{"address": "https://example.com/", "status_code": 200}])
    result = filter_html_200(df)
    assert len(result) == 1


def test_apply_mapping_fills_optional_text_cols():
    df = _make_sf_df()
    mapping, _ = auto_map_columns(df)
    result = apply_mapping(df, mapping)
    assert "h2" in result.columns
    assert result["h2"].iloc[0] == "" or isinstance(result["h2"].iloc[0], str)


def test_load_retired_urls_plain_text():
    content = "https://example.com/old-page-1\nhttps://example.com/old-page-2\n"
    fake_file = io.BytesIO(content.encode("utf-8"))
    fake_file.name = "urls.txt"
    df = load_retired_urls(fake_file)
    assert "url" in df.columns
    assert len(df) == 2
    assert "https://example.com/old-page-1" in df["url"].values


def test_load_retired_urls_csv_with_url_column():
    content = "url,reason,retired_date\nhttps://example.com/old,discontinued,2024-01-01\n"
    fake_file = io.BytesIO(content.encode("utf-8"))
    fake_file.name = "retired.csv"
    df = load_retired_urls(fake_file)
    assert "url" in df.columns
    assert len(df) == 1
    assert "reason" in df.columns


def test_read_crawl_from_synthetic_fixture():
    legacy_path = FIXTURES / "legacy_sample.csv"
    if not legacy_path.exists():
        pytest.skip("Synthetic fixture not found")
    with open(legacy_path, "rb") as f:
        df = read_crawl(f)
    assert len(df) > 0
    assert "Address" in df.columns or "address" in df.columns


def test_encoding_error_handled_gracefully(tmp_path):
    bad_bytes = b"Address,Status Code,Content Type\nhttps://example.com/caf\xe9,200,text/html\n"
    p = tmp_path / "bad.csv"
    p.write_bytes(bad_bytes)
    with open(p, "rb") as f:
        df = read_crawl(f)
    assert len(df) >= 1


def test_read_crawl_xlsx_via_bytesio(tmp_path):
    """XLSX uploaded through BytesIO (Streamlit path) must be read correctly.

    BytesIO has no .name attribute, so read_crawl requires an explicit filename
    to detect the XLSX format. This test exercises that path.
    """
    import io as _io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ["Address", "Status Code", "Content Type", "H1-1", "Title 1"]
    ws.append(headers)
    ws.append(["https://example.com/page", 200, "text/html", "Test H1", "Test Title"])

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    df = read_crawl(buf, filename="crawl.xlsx")
    assert "Address" in df.columns
    assert len(df) == 1
    assert df["Address"].iloc[0] == "https://example.com/page"
