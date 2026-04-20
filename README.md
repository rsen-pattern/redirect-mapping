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

---

## User Guide

This guide walks through both modes end-to-end. It assumes you know what a 301 redirect is and have access to Screaming Frog (free tier crawls up to 500 URLs; paid licence required beyond that).

### Before you start — preparing your crawls

Both modes consume Screaming Frog exports. Get this part right and everything else works; get it wrong and you'll spend half the session debugging bad data.

**Crawl settings**

In Screaming Frog, before hitting Start:

1. **Configuration → Spider → Rendering** — set to "JavaScript" if your site is client-rendered (any modern SPA, most Shopify themes, most Webflow sites). Static HTML sites can stay on "Text Only" for a faster crawl.
2. **Configuration → Spider → Crawl** — tick "Crawl All Subdomains" only if you genuinely want to include them. Usually you don't.
3. **Configuration → Exclude** — add patterns for faceted search, paginated category pages, and tracking-parameter variants. These are noise for redirect mapping and they'll balloon your URL count.
4. **Configuration → URL Rewriting → Remove Parameters** — tick "Remove all parameters" unless query-stringed URLs are meaningfully different pages on your site (they usually aren't, for redirect-map purposes).

**Exports you'll need**

For **Mode A** (site migration):

- **Legacy site crawl** — Internal → HTML → Export. This is the site as it exists before migration.
- **New site crawl** — same, but against the new site. If you're pre-launch, crawl a staging environment with staging.example.com URLs rewritten to the production domain (Screaming Frog has a URL-rewriting feature for exactly this).
- **Optional — All Inlinks** — Bulk Export → All Inlinks. Only needed if you enable the inlink-overlap signal in the sidebar.

For **Mode B** (product retirement):

- **Site crawl** — Internal → HTML → Export. This is your current (live) site.
- **All Inlinks** — Bulk Export → All Inlinks. **Required** for Mode B.
- **Retired URL list** — a plain `.txt` file with one dead URL per line, or a CSV with a `url` column plus any metadata you want to keep (retirement reason, date, SKU, etc.).
- **Optional — Segments CSV** — if you've manually tagged collection pages in Screaming Frog, export the segments.

**Size and encoding**

The tool reads `utf-8-sig` with `encoding_errors='replace'`, which handles the Windows-1252 variant Screaming Frog sometimes emits. If your URLs contain garbled characters after upload, re-export from Screaming Frog with UTF-8 encoding forced.

Default Streamlit upload cap is 500MB (set in `.streamlit/config.toml`). A 10k-URL HTML export is usually under 20MB; the inlinks export for the same site can be 100-300MB.

---

### Mode A — Site Migration

**When to use it**

You're moving from old URLs to new URLs on roughly the same site. The canonical cases:

- Platform migration (Magento → Shopify, WordPress → Webflow, custom → Contentful)
- URL structure change (`/product-name` → `/products/product-name`)
- Domain change (`.com.au` → `.com/au/`)
- IA overhaul where categories restructure but content is mostly preserved

Not the right mode if: most of your old URLs are being deleted outright (that's Mode B), or the new site is a completely different product where content doesn't carry over.

**Step-by-step**

**1. Upload both crawls.**

The app auto-detects Screaming Frog's standard column names. If it can't auto-detect (rare — usually only if you've renamed columns in Excel), a column mapper appears with dropdowns for the required fields: `address`, `title`, `h1`.

After upload, you'll see a preview of the first 5 rows and a count of HTML-200 URLs. If that count looks wrong, your crawl probably included redirects or non-HTML assets — re-filter in Screaming Frog before re-exporting.

**2. Configure the sidebar.**

Defaults are sensible for most migrations. What to touch:

- **Exact-slug pre-pass** — leave on. This is your fast lane: any old URL whose last path segment uniquely matches a new URL's last segment gets resolved at 1.0 confidence and skips the expensive matchers. On an e-commerce migration this typically handles 50-80% of URLs instantly.
- **Signal weights** — the default weights give H1 the heaviest vote (0.30), then title (0.25), path/slug/H2 (0.15 each). Tune these only if you have a specific reason:
  - If your H1s are templated and unreliable ("Shop All", "Products", "Category"), drop H1 weight and push title or slug up.
  - If URL structure is deliberately consistent between old and new, push path/slug weight up.
  - The sliders auto-normalise to sum to 1.0, so you don't need to do the arithmetic yourself.
- **Include inlink overlap** — enable only if you uploaded the inlinks export. This adds a 6th signal that helps catch content whose title/H1 changed but whose inlink pattern stayed similar.
- **Enable AI tiebreak** — off by default. Turn on if your first run shows a lot of rows in the "Needs review" bucket; AI will resolve the ambiguous ones.

**3. Click Run mechanical matching.**

On a 10k × 10k dataset this should complete in under 2 minutes. On the Nickscali test data (550 × 1297) it's ~15 seconds.

**4. Read the results.**

Five metric cards up top:

- **Pre-pass resolved** — URLs resolved by exact-slug match. These are 1.0 confidence; don't review them.
- **High confidence (≥0.90)** — safe to redirect. Review spot-checked, not row-by-row.
- **Needs review (0.70–0.90)** — manual eyeballs required. This is where the AI tiebreak earns its keep.
- **No match (<0.70)** — probably no good target exists. These are candidates for 410 Gone or a redirect to a category/homepage.
- **Ambiguous (AI-eligible)** — rows where the top two candidates are too close to call, regardless of absolute score.

The results table shows every old URL with its winning new URL, the combined score, the tier, and which matchers contributed. Sort by score ascending to see your worst matches first.

**5. (Optional) Run AI tiebreak.**

If Ambiguous count is above zero and AI is enabled, click Run AI tiebreak. The tool sends each ambiguous row plus its top 5 mechanical candidates to Claude Haiku 4.5 (by default) and asks which is the best match. Runs with concurrency 5, so 100 ambiguous rows completes in about 30 seconds.

Cost estimate shown in the sidebar. At $0.001/call it's trivial even for thousands of rows.

After AI runs, the method column for resolved rows changes from e.g. "h1,title" to "AI (claude-haiku-4-5)", and the reasoning is stored for the export.

**6. Export.**

Three format options:

- **Full review XLSX** — multi-sheet workbook. Use this for client review. Sheets: Best Matches (everything), High Confidence, Needs Review, No Match, one sheet per matcher, AI Decisions (if AI ran).
- **High-confidence CSV** — two columns, `source_url` and `destination_url`, filtered to ≥0.90 only. Feed this into your htaccess, nginx config, Cloudflare redirect rules, or CMS redirect table.
- **Full JSON** — for scripting further. Includes all results, AI reasoning, second-place candidates.

**What to do with the output**

1. Load the high-confidence CSV into whatever system handles your redirects. Test 10 random rows by curl — confirm 301s resolve to 200s.
2. Work through the Needs Review sheet manually.
3. Decide on No Match rows individually: redirect to parent category, redirect to homepage, or 410 Gone.
4. Keep the full JSON in your project archive — the reasoning is in there for later audit.

**Troubleshooting Mode A**

- **Pre-pass resolved count is 0** — URL structures are completely different. Fine — mechanical matchers will still work.
- **High-confidence count is very low (<30% of URLs)** — check crawl filtering, or H1s may be templated. Drop H1 weight to 0.10 and push title up to 0.45.
- **Results dominated by path/slug matches** — title/H1 columns probably didn't map correctly on upload. Re-check the column mapper.

---

### Mode B — Product Retirement

**When to use it**

You have dead URLs on a live site. You want each dead URL to go to the closest living parent — usually a category or collection page.

The canonical case: e-commerce. Products go out of stock, seasonal items retire, discontinued SKUs linger in Google's index.

**The key concept: collection detection**

Mode B's quality depends on correctly identifying which of your URLs are "collection pages" (valid redirect targets). Three methods — use one or combine:

**Method 1: URL patterns.** Glob-style patterns against URL paths:

```
/category/*
/collections/*
/shop/*/
```

Any URL whose path matches any pattern is tagged as a collection.

**Method 2: Segment upload.** Export Screaming Frog segments as CSV with `url` and `segment` columns. Specify which segment name means "collection" (default: `"collection"`).

**Method 3: Auto-detect.** Heuristic — a URL qualifies as a collection if:

- Outlinks count is in the top 10% across the site
- Inlinks count is ≥ the median
- Crawl depth is ≤ median + 1

Preview the detected set before running; deselect false positives.

**Step-by-step**

1. Upload the site crawl (same flow as Mode A).
2. Upload the inlinks export. Large file — the tool streams it in 50k-row chunks.
3. Upload retired URLs (plain text or CSV with `url` column).
4. Define collection pages using one or more detection methods. Preview the detected set.
5. Click **Run Mode B matching**.
6. (Optional) Run AI tiebreak.
7. Export.

**How scoring works**

For each retired URL, every collection is scored on four signals:

| Signal | Default weight | What it measures |
|---|---|---|
| Inlink overlap (Jaccard) | 0.40 | Shared inlink sources between retired and collection |
| URL ancestor | 0.30 | Whether collection is a path ancestor of the retired URL |
| Title/H1 TF-IDF | 0.20 | Semantic similarity of page text |
| Breadcrumb | 0.10 | Whether collection URL appears in retired page's breadcrumb |

Inlink overlap is the most important signal. If your inlinks data is sparse, Mode B quality drops sharply.

**Troubleshooting Mode B**

- **Auto-detect found 0 collections** — inlinks/outlinks data is empty in the crawl. Re-export with full fields, or use URL patterns instead.
- **Auto-detect found thousands of collections** — a large-outlinks outlier (homepage, sitemap) is skewing the threshold. Use URL patterns instead.
- **Most retired URLs in No Match** — inlinks data may be too sparse, especially if retired URLs are already returning 404s. Use URL patterns + title similarity as a workaround.

---

### Quick reference: defaults

| Setting | Default | When to change |
|---|---|---|
| Exact-slug pre-pass | On | Off only to benchmark matcher performance |
| Mode A: H1 weight | 0.30 | Drop if H1s are templated/repeated |
| Mode A: Title weight | 0.25 | Push up if H1 is unreliable |
| Mode A: Path/Slug weight | 0.15 each | Push up if URL structure is deliberately preserved |
| Mode B: Inlink overlap | 0.40 | Don't drop below 0.30 |
| Mode B: URL ancestor | 0.30 | Push up if URL hierarchy is very clean |
| High-confidence threshold | 0.90 | Don't lower this — tune signals, not thresholds |

### Quick reference: which mode when

| Scenario | Mode |
|---|---|
| Replatform (Magento → Shopify) | A |
| URL structure change, same content | A |
| Domain change | A |
| Products out of stock on a live site | B |
| Seasonal/discontinued products | B |
| Blog archive cleanup | B (with URL patterns for category pages) |
| "We deleted 80% of the site" | Both — B for deletions, A for survivors |
| Merging two sites into one | A, in two passes |