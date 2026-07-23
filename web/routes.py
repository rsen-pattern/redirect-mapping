"""Flask routes for the SEO Redirect Mapper."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd
from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from core.ingest import load_retired_urls
from core.scoring import DEFAULT_WEIGHTS_MODE_A, DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS
from utils.bifrost import get_api_key
from web.logic import (
    ai_fallback_message,
    apply_ingest,
    build_collections_df,
    default_config,
    export_file,
    ingest_upload,
    load_inlinks_from_bytes,
    load_models_config,
    results_summary,
    run_ai_tiebreak,
    run_mode_a_matching,
    run_mode_b_matching,
)
from web.store import clear_results, get_bucket

bp = Blueprint("mapper", __name__)

RESULTS_PREVIEW_LIMIT = 2000


def _session_id() -> str:
    if "sid" not in session:
        import secrets

        session["sid"] = secrets.token_hex(16)
    return session["sid"]


def _bucket() -> dict[str, Any]:
    return get_bucket(_session_id())


def _cfg() -> dict[str, Any]:
    if "cfg" not in session:
        session["cfg"] = default_config()
    return session["cfg"]


def _retirement_settings() -> dict[str, Any]:
    if "retirement" not in session:
        session["retirement"] = {
            "patterns": "",
            "detection_methods": ["Auto-detect"],
            "segment_name": "collection",
        }
    return session["retirement"]


def _save_cfg(cfg: dict[str, Any]) -> None:
    session["cfg"] = cfg
    session.modified = True


def _df_preview(df: pd.DataFrame | None, n: int = 5) -> list[dict]:
    if df is None or df.empty:
        return []
    cols = [c for c in ["address", "title", "h1", "url"] if c in df.columns]
    if not cols:
        cols = list(df.columns[:5])
    return df[cols].head(n).fillna("").to_dict(orient="records")


def _results_table(df: pd.DataFrame | None) -> tuple[list[dict], int]:
    if df is None or df.empty:
        return [], 0
    total = len(df)
    cols = [
        c
        for c in [
            "legacy_url",
            "candidate_url",
            "combined_score",
            "tier",
            "methods_contributed",
            "is_ambiguous",
        ]
        if c in df.columns
    ]
    out = df[cols].head(RESULTS_PREVIEW_LIMIT).copy()
    if "combined_score" in out.columns:
        out["combined_score"] = out["combined_score"].round(3)
    return out.fillna("").to_dict(orient="records"), total


def _collection_preview(bucket: dict[str, Any], cfg: dict[str, Any]) -> list[dict]:
    if cfg.get("mode") != "retirement":
        return []
    site_df = bucket.get("legacy_df")
    if site_df is None:
        return []

    settings = _retirement_settings()
    methods = settings.get("detection_methods", ["Auto-detect"])
    collections_df = build_collections_df(
        site_df,
        bucket,
        settings.get("patterns", ""),
        bucket.get("segment_bytes"),
        settings.get("segment_name", "collection"),
        use_patterns="URL patterns" in methods,
        use_segment="Segment upload" in methods,
        use_auto="Auto-detect" in methods,
    )
    if collections_df is None:
        return []
    bucket["collections_preview_df"] = collections_df
    return _df_preview(collections_df, 50)


@bp.route("/")
def index():
    cfg = _cfg()
    bucket = _bucket()
    models_config = load_models_config()
    results_df = bucket.get("results_df")
    results, results_total = _results_table(results_df)
    summary = results_summary(results_df)
    retirement = _retirement_settings()
    collection_preview = _collection_preview(bucket, cfg)
    preview_df = bucket.get("collections_preview_df")
    collection_count = len(preview_df) if preview_df is not None else 0

    return render_template(
        "index.html",
        cfg=cfg,
        models=models_config["models"],
        default_model=models_config["default"],
        api_key_set=bool(get_api_key()),
        legacy_count=len(bucket["legacy_df"]) if bucket.get("legacy_df") is not None else None,
        new_count=len(bucket["new_df"]) if bucket.get("new_df") is not None else None,
        site_count=len(bucket["legacy_df"]) if bucket.get("legacy_df") is not None and cfg.get("mode") == "retirement" else None,
        retired_count=len(bucket["retired_df"]) if bucket.get("retired_df") is not None else None,
        inlinks_loaded=bool(bucket.get("inlinks_map")),
        inlinks_count=len(bucket.get("inlinks_map") or {}),
        segment_uploaded=bool(bucket.get("segment_bytes")),
        legacy_preview=_df_preview(bucket.get("legacy_df")),
        new_preview=_df_preview(bucket.get("new_df")),
        site_preview=_df_preview(bucket.get("legacy_df")) if cfg.get("mode") == "retirement" else [],
        retired_preview=_df_preview(bucket.get("retired_df")),
        collection_preview=collection_preview,
        collection_count=collection_count,
        retirement=retirement,
        results=results,
        results_total=results_total,
        results_truncated=results_total > RESULTS_PREVIEW_LIMIT,
        summary=summary,
        pending_mapping=bucket.get("pending_mapping"),
        ai_cost_estimate=round(summary["ambiguous"] * 0.001, 2),
        ai_fallback_message=ai_fallback_message(bucket.get("ai_df")),
    )


@bp.route("/set-mode/<mode>", methods=["POST"])
def set_mode(mode: str):
    if mode not in {"migration", "retirement"}:
        flash("Unknown mode.", "error")
        return redirect(url_for("mapper.index"))

    cfg = _cfg()
    if cfg.get("mode") != mode:
        cfg["mode"] = mode
        _save_cfg(cfg)
        bucket = _bucket()
        clear_results(bucket)
        flash(f"Switched to {'Site Migration' if mode == 'migration' else 'Product Retirement'}.", "success")
    return redirect(url_for("mapper.index"))


@bp.route("/config", methods=["POST"])
def update_config():
    cfg = _cfg()
    bucket = _bucket()
    prev_mode = cfg.get("mode")
    prev_use_inlinks = cfg.get("use_inlinks")

    cfg["mode"] = request.form.get("mode", cfg.get("mode", "migration"))
    cfg["exact_slug_enabled"] = request.form.get("exact_slug_enabled") == "on"
    cfg["ai_enabled"] = request.form.get("ai_enabled") == "on"
    cfg["use_inlinks"] = request.form.get("use_inlinks") == "on"
    cfg["max_workers"] = int(request.form.get("max_workers", cfg.get("max_workers", 5)))
    cfg["model"] = request.form.get("model", cfg.get("model"))

    if cfg["use_inlinks"] != prev_use_inlinks:
        base = DEFAULT_WEIGHTS_MODE_A_WITH_INLINKS if cfg["use_inlinks"] else DEFAULT_WEIGHTS_MODE_A
        cfg["weights"] = dict(base)

    weight_names = request.form.getlist("weight_name")
    weight_values = request.form.getlist("weight_value")
    if weight_names and weight_values and len(weight_names) == len(weight_values):
        raw = {n: float(v) for n, v in zip(weight_names, weight_values)}
        total = sum(raw.values())
        if total > 0:
            cfg["weights"] = {k: v / total for k, v in raw.items()}

    api_key_input = request.form.get("bifrost_api_key", "").strip()
    if api_key_input:
        session["bifrost_api_key"] = api_key_input
    elif request.form.get("clear_bifrost_key") == "on":
        session.pop("bifrost_api_key", None)

    if cfg["mode"] != prev_mode:
        clear_results(bucket)

    _save_cfg(cfg)
    flash("Configuration saved.", "success")
    return redirect(url_for("mapper.index"))


@bp.route("/apply-mapping", methods=["POST"])
def apply_mapping():
    bucket = _bucket()
    pending = bucket.get("pending_mapping")
    if not pending:
        flash("No pending column mapping.", "error")
        return redirect(url_for("mapper.index"))

    mapping = dict(pending.get("mapping", {}))
    for key, value in request.form.items():
        if key.startswith("map_") and value and value != "(skip)":
            canonical = key[4:]
            mapping[value] = canonical

    still_missing = [c for c in pending["missing"] if c not in mapping.values()]
    if still_missing:
        pending["mapping"] = mapping
        pending["missing"] = still_missing
        flash(f"Still missing columns: {', '.join(still_missing)}", "warning")
        return redirect(url_for("mapper.index"))

    try:
        df = apply_ingest(pending["raw_df"], mapping)
        if df.empty:
            flash("No HTML-200 rows found after filtering.", "error")
            return redirect(url_for("mapper.index"))

        upload_type = pending["upload_type"]
        if upload_type == "legacy":
            bucket["legacy_df"] = df
            bucket["legacy_mapping"] = mapping
        elif upload_type == "new":
            bucket["new_df"] = df
            bucket["new_mapping"] = mapping
        elif upload_type == "site":
            bucket["legacy_df"] = df
            bucket["site_mapping"] = mapping
            bucket["collection_set"] = None

        bucket.pop("pending_mapping", None)
        clear_results(bucket)
        flash(f"Loaded {len(df):,} HTML-200 rows.", "success")
    except Exception as exc:
        flash(f"Mapping failed: {exc}", "error")

    return redirect(url_for("mapper.index"))


@bp.route("/upload", methods=["POST"])
def upload():
    upload_type = request.form.get("upload_type", "")
    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("mapper.index"))

    file_bytes = file.read()
    bucket = _bucket()

    try:
        if upload_type == "retired":
            retired_df = load_retired_urls(io.BytesIO(file_bytes))
            bucket["retired_df"] = retired_df
            flash(f"Loaded {len(retired_df):,} retired URLs.", "success")
            return redirect(url_for("mapper.index"))

        if upload_type == "inlinks":
            inlinks_map, inlinks_df = load_inlinks_from_bytes(file_bytes)
            bucket["inlinks_map"] = inlinks_map
            bucket["inlinks_df"] = inlinks_df
            flash(f"Inlinks index built: {len(inlinks_map):,} destination URLs.", "success")
            return redirect(url_for("mapper.index"))

        if upload_type == "segment":
            bucket["segment_bytes"] = file_bytes
            flash("Segment file uploaded.", "success")
            return redirect(url_for("mapper.index"))

        raw_df, mapping, missing_required = ingest_upload(file_bytes, file.filename)
        extra_mapping = {}
        for key, value in request.form.items():
            if key.startswith("map_") and value and value != "(skip)":
                canonical = key[4:]
                extra_mapping[value] = canonical

        mapping.update(extra_mapping)
        still_missing = [c for c in missing_required if c not in mapping.values()]

        if still_missing:
            bucket["pending_mapping"] = {
                "upload_type": upload_type,
                "raw_df": raw_df,
                "mapping": mapping,
                "missing": still_missing,
                "columns": list(raw_df.columns),
            }
            flash(f"Map required columns: {', '.join(still_missing)}", "warning")
            return redirect(url_for("mapper.index"))

        df = apply_ingest(raw_df, mapping)
        if df.empty:
            flash("No HTML-200 rows found after filtering.", "error")
            return redirect(url_for("mapper.index"))

        if upload_type == "legacy":
            bucket["legacy_df"] = df
            bucket["legacy_mapping"] = mapping
        elif upload_type == "new":
            bucket["new_df"] = df
            bucket["new_mapping"] = mapping
        elif upload_type == "site":
            bucket["legacy_df"] = df
            bucket["site_mapping"] = mapping
            bucket["collection_set"] = None

        bucket.pop("pending_mapping", None)
        clear_results(bucket)
        flash(f"Loaded {len(df):,} HTML-200 rows.", "success")
    except Exception as exc:
        flash(f"Upload failed: {exc}", "error")

    return redirect(url_for("mapper.index"))


@bp.route("/preview-collections", methods=["POST"])
def preview_collections():
    bucket = _bucket()
    site_df = bucket.get("legacy_df")
    if site_df is None:
        flash("Upload the site crawl first.", "error")
        return redirect(url_for("mapper.index"))

    methods = request.form.getlist("detection_methods") or ["Auto-detect"]
    settings = _retirement_settings()
    settings["patterns"] = request.form.get("patterns", "")
    settings["detection_methods"] = methods
    settings["segment_name"] = request.form.get("segment_name", "collection")
    session["retirement"] = settings
    session.modified = True

    collections_df = build_collections_df(
        site_df,
        bucket,
        settings["patterns"],
        bucket.get("segment_bytes"),
        settings["segment_name"],
        use_patterns="URL patterns" in methods,
        use_segment="Segment upload" in methods,
        use_auto="Auto-detect" in methods,
    )
    if collections_df is None or collections_df.empty:
        flash("No collection pages detected with current settings.", "warning")
    else:
        bucket["collections_preview_df"] = collections_df
        flash(f"Collection pool: {len(collections_df):,} pages.", "success")

    return redirect(url_for("mapper.index"))


@bp.route("/auto-detect-collections", methods=["POST"])
def auto_detect_collections():
    bucket = _bucket()
    site_df = bucket.get("legacy_df")
    if site_df is None:
        flash("Upload the site crawl first.", "error")
        return redirect(url_for("mapper.index"))

    from core.collections import detect_collections_auto

    auto_set = detect_collections_auto(site_df)
    bucket["collection_set"] = auto_set
    flash(f"Auto-detected {len(auto_set):,} collection pages.", "success")
    return redirect(url_for("mapper.index"))


@bp.route("/run-migration", methods=["POST"])
def run_migration():
    bucket = _bucket()
    cfg = _cfg()
    legacy_df = bucket.get("legacy_df")
    new_df = bucket.get("new_df")
    if legacy_df is None or new_df is None:
        flash("Upload both legacy and new crawls first.", "error")
        return redirect(url_for("mapper.index"))

    try:
        winners_df, combined_df = run_mode_a_matching(
            legacy_df,
            new_df,
            cfg,
            bucket.get("inlinks_df"),
        )
        bucket["results_df"] = winners_df
        bucket["combined_df"] = combined_df
        bucket["ai_df"] = None
        flash("Matching complete.", "success")
    except Exception as exc:
        flash(f"Matching failed: {exc}", "error")

    return redirect(url_for("mapper.index"))


@bp.route("/run-retirement", methods=["POST"])
def run_retirement():
    bucket = _bucket()
    cfg = _cfg()
    site_df = bucket.get("legacy_df")
    retired_df = bucket.get("retired_df")
    if site_df is None:
        flash("Upload the site crawl first.", "error")
        return redirect(url_for("mapper.index"))
    if retired_df is None:
        flash("Upload the retired URL list first.", "error")
        return redirect(url_for("mapper.index"))

    methods = request.form.getlist("detection_methods") or ["Auto-detect"]
    settings = _retirement_settings()
    settings["patterns"] = request.form.get("patterns", "")
    settings["detection_methods"] = methods
    settings["segment_name"] = request.form.get("segment_name", "collection")
    session["retirement"] = settings
    session.modified = True

    collections_df = build_collections_df(
        site_df,
        bucket,
        settings["patterns"],
        bucket.get("segment_bytes"),
        settings["segment_name"],
        use_patterns="URL patterns" in methods,
        use_segment="Segment upload" in methods,
        use_auto="Auto-detect" in methods,
    )
    if collections_df is None or collections_df.empty:
        flash("Define at least one collection detection method.", "error")
        return redirect(url_for("mapper.index"))

    bucket["collections_df"] = collections_df
    inlinks_map = bucket.get("inlinks_map") or {}

    try:
        winners_df, combined_df = run_mode_b_matching(
            retired_df,
            site_df,
            collections_df,
            inlinks_map,
        )
        bucket["results_df"] = winners_df
        bucket["combined_df"] = combined_df
        bucket["ai_df"] = None
        flash("Mode B matching complete.", "success")
    except Exception as exc:
        flash(f"Matching failed: {exc}", "error")

    return redirect(url_for("mapper.index"))


@bp.route("/run-ai", methods=["POST"])
def run_ai():
    bucket = _bucket()
    cfg = _cfg()
    results_df = bucket.get("results_df")
    combined_df = bucket.get("combined_df")
    if results_df is None or results_df.empty:
        flash("Run matching first.", "error")
        return redirect(url_for("mapper.index"))

    api_key = get_api_key()
    if not api_key:
        flash("Enter a Bi Frost API key in the sidebar or set BIFROST_API_KEY in .env.", "error")
        return redirect(url_for("mapper.index"))

    mode = "retirement" if cfg.get("mode") == "retirement" else "migration"
    try:
        results_merged, ai_df = run_ai_tiebreak(
            api_key,
            mode,
            results_df,
            combined_df if combined_df is not None else pd.DataFrame(),
            cfg.get("max_workers", 5),
        )
        bucket["results_df"] = results_merged
        bucket["ai_df"] = ai_df
        fallback_msg = ai_fallback_message(ai_df)
        flash("AI tiebreak complete.", "success")
        if fallback_msg:
            flash(fallback_msg, "warning")
    except Exception as exc:
        flash(f"AI tiebreak failed: {exc}", "error")

    return redirect(url_for("mapper.index"))


@bp.route("/export/<export_format>")
def download(export_format: str):
    bucket = _bucket()
    cfg = _cfg()
    results_df = bucket.get("results_df")
    if results_df is None or results_df.empty:
        flash("No results to export.", "error")
        return redirect(url_for("mapper.index"))

    if export_format not in {"xlsx", "csv", "json"}:
        flash("Unknown export format.", "error")
        return redirect(url_for("mapper.index"))

    mode = "retirement" if cfg.get("mode") == "retirement" else "migration"
    data, filename, mime = export_file(export_format, results_df, bucket.get("ai_df"), mode)
    return Response(
        data,
        mimetype=mime,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/reset", methods=["POST"])
def reset_session():
    bucket = _bucket()
    bucket.clear()
    session.pop("cfg", None)
    session.pop("retirement", None)
    session.pop("bifrost_api_key", None)
    flash("Session cleared.", "success")
    return redirect(url_for("mapper.index"))
