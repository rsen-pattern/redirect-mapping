"""Tests for core/audit.py — redirect-map SEO audits."""

import pandas as pd

from core.audit import add_priority, build_asset_report, run_audit


def _winners(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# run_audit — flags
# ---------------------------------------------------------------------------

def test_audit_flags_loop():
    winners = _winners([
        {"legacy_url": "https://x.com/a", "candidate_url": "https://x.com/a", "tier": "high"},
    ])
    out = run_audit(winners)
    assert "loop" in out.iloc[0]["flags"]


def test_audit_flags_chain():
    # target /b is itself a source in the map -> chain
    winners = _winners([
        {"legacy_url": "https://x.com/a", "candidate_url": "https://x.com/b", "tier": "high"},
        {"legacy_url": "https://x.com/b", "candidate_url": "https://x.com/c", "tier": "high"},
    ])
    out = run_audit(winners)
    assert "chain" in out.iloc[0]["flags"]
    # /b -> /c : /c is not a source, no chain
    assert "chain" not in out.iloc[1]["flags"]


def test_audit_flags_homepage_redirect():
    winners = _winners([
        {"legacy_url": "https://x.com/a", "candidate_url": "https://new.com/", "tier": "high"},
    ])
    out = run_audit(winners)
    assert "homepage_redirect" in out.iloc[0]["flags"]


def test_audit_flags_many_to_one():
    rows = [
        {"legacy_url": f"https://x.com/p{i}", "candidate_url": "https://new.com/hub", "tier": "high"}
        for i in range(12)
    ]
    out = run_audit(_winners(rows), many_to_one_threshold=10)
    assert out.iloc[0]["flags"].startswith("many_to_one:12") or "many_to_one:12" in out.iloc[0]["flags"]


def test_audit_no_false_flags_on_clean_map():
    winners = _winners([
        {"legacy_url": "https://old.com/a", "candidate_url": "https://new.com/a", "tier": "high"},
        {"legacy_url": "https://old.com/b", "candidate_url": "https://new.com/b", "tier": "high"},
    ])
    out = run_audit(winners)
    assert (out["flags"] == "").all()


def test_audit_target_not_in_new_crawl():
    winners = _winners([
        {"legacy_url": "https://old.com/a", "candidate_url": "https://new.com/ghost", "tier": "high"},
    ])
    new = pd.DataFrame({"address": ["https://new.com/real"]})
    out = run_audit(winners, new_df=new)
    assert "target_not_in_new_crawl" in out.iloc[0]["flags"]


def test_audit_high_value_unmatched():
    winners = _winners([
        {"legacy_url": "https://old.com/star", "candidate_url": "https://new.com/x", "tier": "no_match"},
        {"legacy_url": "https://old.com/minor", "candidate_url": "https://new.com/y", "tier": "no_match"},
    ])
    legacy = pd.DataFrame({
        "address": ["https://old.com/star", "https://old.com/minor"],
        "unique_inlinks": [500, 1],
    })
    out = run_audit(winners, legacy_df=legacy)
    star = out[out["legacy_url"] == "https://old.com/star"].iloc[0]
    assert "high_value_unmatched" in star["flags"]


def test_audit_empty():
    out = run_audit(pd.DataFrame())
    assert out.empty
    assert "flags" in out.columns and "priority" in out.columns


# ---------------------------------------------------------------------------
# add_priority
# ---------------------------------------------------------------------------

def test_add_priority_from_inlinks():
    winners = _winners([{"legacy_url": "https://x.com/a", "candidate_url": "https://y.com/a"}])
    legacy = pd.DataFrame({"address": ["https://x.com/a"], "unique_inlinks": [42]})
    out = add_priority(winners, legacy)
    assert out.iloc[0]["priority"] == 42


def test_add_priority_no_inlink_data():
    winners = _winners([{"legacy_url": "https://x.com/a", "candidate_url": "https://y.com/a"}])
    out = add_priority(winners, pd.DataFrame({"address": ["https://x.com/a"]}))
    assert out.iloc[0]["priority"] == 0


# ---------------------------------------------------------------------------
# build_asset_report
# ---------------------------------------------------------------------------

def test_asset_report_keeps_non_html_200():
    df = pd.DataFrame({
        "address": ["https://x.com/a.pdf", "https://x.com/page", "https://x.com/img.png"],
        "status_code": [200, 200, 200],
        "content_type": ["application/pdf", "text/html", "image/png"],
    })
    report = build_asset_report(df)
    assert set(report["address"]) == {"https://x.com/a.pdf", "https://x.com/img.png"}


def test_asset_report_excludes_non_200():
    df = pd.DataFrame({
        "address": ["https://x.com/gone.pdf"],
        "status_code": [404],
        "content_type": ["application/pdf"],
    })
    assert build_asset_report(df).empty


def test_asset_report_empty_input():
    assert build_asset_report(pd.DataFrame()).empty
