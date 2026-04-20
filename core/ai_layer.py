"""AI disambiguation layer — routes ambiguous matches to Bi Frost for tiebreaking."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from typing import Callable

import pandas as pd
from pydantic import BaseModel, ValidationError

from utils.bifrost import call_with_fallback
from utils.prompts import render_prompt

logger = logging.getLogger(__name__)

_PROMPT_FILE = "disambiguate_redirect.txt"


class AiDecision(BaseModel):
    winner_url: str
    confidence: float
    reasoning: str
    model_used: str = ""
    fallback_fired: bool = False


def disambiguate_one(
    api_key: str,
    mode: str,
    source_row: pd.Series,
    candidates: pd.DataFrame,
    top_k: int = 5,
) -> AiDecision:
    """Disambiguate a single ambiguous legacy URL using Bi Frost.

    On JSON parse failure returns top mechanical candidate with confidence=0.5.
    """
    top_candidates = candidates.sort_values("combined_score", ascending=False).head(top_k)

    candidates_json = json.dumps(
        [
            {
                "url": row["candidate_url"],
                "score": round(float(row["combined_score"]), 4),
                "title": row.get("candidate_title", ""),
                "h1": row.get("candidate_h1", ""),
            }
            for _, row in top_candidates.iterrows()
        ],
        indent=2,
    )

    prompt_text = render_prompt(
        _PROMPT_FILE,
        mode=mode,
        source_url=str(source_row.get("legacy_url", "")),
        source_title=str(source_row.get("title", "")),
        source_h1=str(source_row.get("h1", "")),
        source_meta=str(source_row.get("meta_description", "")),
        candidates_json=candidates_json,
    )

    messages = [{"role": "user", "content": prompt_text}]

    fallback_winner = (
        str(top_candidates.iloc[0]["candidate_url"]) if not top_candidates.empty else ""
    )

    try:
        content, model_used, fallback_fired = call_with_fallback(
            messages,
            api_key=api_key,
            response_format={"type": "json_object"},
        )
        decision = AiDecision.model_validate_json(content)
        decision.model_used = model_used
        decision.fallback_fired = fallback_fired
        return decision
    except (json.JSONDecodeError, ValidationError, Exception) as e:
        logger.warning("AI parse failure for %s: %s", source_row.get("legacy_url"), e)
        return AiDecision(
            winner_url=fallback_winner,
            confidence=0.5,
            reasoning="AI parse failed, used top mechanical",
            model_used="",
            fallback_fired=False,
        )


def disambiguate_batch(
    api_key: str,
    mode: str,
    ambiguous_df: pd.DataFrame,
    results_df: pd.DataFrame,
    combined_df: pd.DataFrame,
    top_k: int = 5,
    max_workers: int = 5,
    progress_callback: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """Fan out AI disambiguation over all ambiguous rows in parallel.

    Returns a DataFrame with one row per ambiguous source URL including the AI decision.
    """
    if ambiguous_df.empty:
        return pd.DataFrame(
            columns=["legacy_url", "winner_url", "confidence", "reasoning", "model_used", "fallback_fired"]
        )

    total = len(ambiguous_df)
    output_rows: list[dict] = []

    def _work(row: pd.Series) -> dict:
        legacy_url = str(row["legacy_url"])
        candidates = combined_df[combined_df["legacy_url"] == legacy_url].copy()
        decision = disambiguate_one(api_key, mode, row, candidates, top_k=top_k)
        return {
            "legacy_url": legacy_url,
            "winner_url": decision.winner_url,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "model_used": decision.model_used,
            "fallback_fired": decision.fallback_fired,
        }

    futures_list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_row = {
            executor.submit(_work, row): row
            for _, row in ambiguous_df.iterrows()
        }
        for i, future in enumerate(concurrent.futures.as_completed(future_to_row), 1):
            try:
                output_rows.append(future.result())
            except Exception as e:
                row = future_to_row[future]
                logger.error("AI worker failed for %s: %s", row.get("legacy_url"), e)
            if progress_callback:
                progress_callback(i, total)

    return pd.DataFrame(output_rows) if output_rows else pd.DataFrame(
        columns=["legacy_url", "winner_url", "confidence", "reasoning", "model_used", "fallback_fired"]
    )
