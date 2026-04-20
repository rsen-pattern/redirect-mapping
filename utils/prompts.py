"""Prompt template loader."""

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template from prompts/{name} and return it as a string."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs) -> str:
    """Load and format a prompt template with the given keyword arguments."""
    return load_prompt(name).format(**kwargs)
