# SEO Redirect Mapper

A Streamlit app that generates production-ready SEO redirect maps from Screaming Frog crawl exports. Supports two modes: site migration (old URLs → new URLs) and product retirement (dead product pages → parent collection pages). Includes an optional AI disambiguation layer via Pattern's Bi Frost LLM gateway.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

## Bi Frost credentials

The AI tiebreak layer calls Pattern's Bi Frost gateway. You need an API key to use it. Three ways to supply it (first found wins):

1. **Sidebar** — paste the key into the "Bi Frost API Key" field at runtime (not persisted).
2. **Secrets file** — create `.streamlit/secrets.toml`:
   ```toml
   BIFROST_API_KEY = "your-key-here"
   ```
3. **Environment variable** — `export BIFROST_API_KEY=your-key-here`

The AI tiebreak is **off by default**. Mechanical matching resolves the majority of URLs without it.

## Mode A — Site Migration

1. Upload your **legacy site crawl** (Screaming Frog Internal > HTML export, CSV or XLSX).
2. Upload your **new site crawl** (same format).
3. Optionally upload an **All Inlinks** export for inlink-overlap scoring.
4. Click **Run mechanical matching**.
5. If AI is enabled, click **Run AI tiebreak** to resolve ambiguous rows.
6. Select export formats and download.

## Mode B — Product Retirement

1. Upload your **site crawl**.
2. Upload the **All Inlinks** export (required for Mode B).
3. Upload your **retired URL list** (plain text, one URL per line, or CSV with a `url` column).
4. Define collection pages using URL patterns, a Segments CSV, or auto-detection.
5. Click **Run Mode B matching**.
6. Export results.

## Testing

```bash
# Fast unit tests (no fixtures required)
pytest tests/ -m 'not integration' -v

# Integration test (requires Nickscali fixture CSVs in tests/fixtures/integration/)
pytest tests/ -m integration -v
```

The integration test expects:
- `tests/fixtures/integration/NS_AU_OLD_internal_html.csv`
- `tests/fixtures/integration/NS_AU_New_internal_html.csv`

## Troubleshooting

**Encoding errors on upload** — Screaming Frog exports use Windows-1252 encoding on some systems. The app reads with `utf-8-sig` + `encoding_errors='replace'` which handles this silently. If columns are garbled, re-export from Screaming Frog with UTF-8 encoding.

**Bi Frost 401** — API key is wrong or expired. Check the sidebar / secrets.toml / env var.

**Bi Frost 404** — Model ID not recognised. Check `config/models.json` against Pattern's current model catalogue.

**"No HTML-200 rows found"** — Your export may contain only non-HTML pages or redirects. In Screaming Frog, use Internal > HTML filter before exporting.