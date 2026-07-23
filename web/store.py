"""In-memory per-session store for uploaded dataframes and results."""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}


def get_bucket(session_id: str) -> dict[str, Any]:
    with _lock:
        if session_id not in _store:
            _store[session_id] = {}
        return _store[session_id]


def clear_results(bucket: dict[str, Any]) -> None:
    for key in ("results_df", "combined_df", "ai_df"):
        bucket.pop(key, None)
