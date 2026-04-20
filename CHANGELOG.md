# Changelog

## v0.1.0 — Initial release

- Mode A (Site Migration): exact-slug pre-pass + 5 mechanical matchers (path, slug, title, H1, H2) with optional inlink overlap
- Mode B (Product Retirement): collection-page detection (URL patterns, segment upload, auto-detect) + inlink-overlap scoring
- AI tiebreak via Bi Frost (Claude Haiku 4.5 by default, full fallback chain)
- Export: multi-sheet XLSX, htaccess-ready CSV, full JSON
- Streamlit UI with sidebar weight controls, AI toggle, and per-tier metric cards
