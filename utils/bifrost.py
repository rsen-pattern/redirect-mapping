"""Bi Frost API client — thin wrapper over the OpenAI-compatible chat completions endpoint."""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

import openai

logger = logging.getLogger(__name__)

_MODELS_PATH = Path(__file__).parent.parent / "config" / "models.json"


@lru_cache(maxsize=1)
def _load_models_config() -> dict:
    with open(_MODELS_PATH) as f:
        return json.load(f)


def get_api_key() -> str | None:
    """Resolve Bi Frost API key. Priority: session_state → st.secrets → env vars."""
    # 1. Streamlit session state (set by sidebar widget)
    try:
        import streamlit as st
        key = st.session_state.get("bifrost_api_key")
        if key:
            return key
    except Exception:
        pass

    # 2. Streamlit secrets
    try:
        import streamlit as st
        if "BIFROST_API_KEY" in st.secrets:
            return st.secrets["BIFROST_API_KEY"]
        if "BIFROST_KEY" in st.secrets:
            return st.secrets["BIFROST_KEY"]
    except Exception:
        pass

    # 3. Environment variables
    return os.environ.get("BIFROST_API_KEY") or os.environ.get("BIFROST_KEY")


def get_client(api_key: str) -> openai.OpenAI:
    """Create an OpenAI-compatible client pointed at Bi Frost."""
    base_url = "https://bifrost.pattern.com/v1"
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def call(client: openai.OpenAI, model: str, messages: list[dict], **kwargs) -> str:
    """Single chat completion call via Bi Frost. Never use responses.create."""
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return response.choices[0].message.content


def call_with_fallback(
    messages: list[dict],
    api_key: str | None = None,
    **kwargs,
) -> tuple[str, str, bool]:
    """Call Bi Frost with automatic model fallback.

    Returns (content, model_used, fallback_fired).
    """
    if api_key is None:
        api_key = get_api_key()
    if not api_key:
        raise ValueError("No Bi Frost API key available.")

    client = get_client(api_key)
    config = _load_models_config()
    default_model = config["default"]
    chain = config["fallback_chain"]

    models_to_try = [default_model] + [m for m in chain if m != default_model]
    last_error: Exception | None = None

    for i, model in enumerate(models_to_try):
        try:
            content = call(client, model, messages, **kwargs)
            fallback_fired = i > 0
            if fallback_fired:
                _maybe_warn(
                    f"Fell back to {model} — {models_to_try[i - 1]} was unavailable."
                )
            return content, model, fallback_fired
        except openai.APIStatusError as e:
            logger.warning("Model %s failed (%s), trying next.", model, e.status_code)
            last_error = e
        except Exception as e:
            logger.warning("Model %s raised unexpected error: %s", model, e)
            last_error = e

    raise RuntimeError(f"All Bi Frost models failed. Last error: {last_error}") from last_error


def _maybe_warn(msg: str) -> None:
    """Show a Streamlit warning if running inside Streamlit; otherwise log."""
    try:
        import streamlit as st
        st.warning(msg)
    except Exception:
        logger.warning(msg)
