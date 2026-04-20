"""Canonical column names and Screaming Frog header aliases."""

CANONICAL_COLUMNS = [
    "address",
    "status_code",
    "content_type",
    "indexability",
    "title",
    "meta_description",
    "h1",
    "h1_2",
    "h2",
    "h2_2",
    "word_count",
    "crawl_depth",
    "inlinks",
    "unique_inlinks",
    "outlinks",
]

SCREAMING_FROG_ALIASES: dict[str, str] = {
    "Address": "address",
    "Status Code": "status_code",
    "Content Type": "content_type",
    "Indexability": "indexability",
    "Title 1": "title",
    "Meta Description 1": "meta_description",
    "H1-1": "h1",
    "H1-2": "h1_2",
    "H2-1": "h2",
    "H2-2": "h2_2",
    "Word Count": "word_count",
    "Crawl Depth": "crawl_depth",
    "Inlinks": "inlinks",
    "Unique Inlinks": "unique_inlinks",
    "Outlinks": "outlinks",
}

REQUIRED_COLUMNS: list[str] = ["address", "title", "h1"]

# Columns filled with empty string when absent
_OPTIONAL_TEXT_COLS = [
    "content_type", "indexability", "meta_description", "h1_2", "h2", "h2_2",
]
# Columns filled with 0 when absent
_OPTIONAL_NUMERIC_COLS = [
    "status_code", "word_count", "crawl_depth", "inlinks", "unique_inlinks", "outlinks",
]
