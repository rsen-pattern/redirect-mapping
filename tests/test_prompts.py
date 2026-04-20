"""Defensive tests for prompt template rendering.

The disambiguate_redirect.txt template contains literal JSON braces escaped as {{...}}.
If someone edits the template and forgets the doubling, render_prompt raises KeyError
at runtime — far from the edit site. This test catches that class of breakage.
"""

import json

import pytest

from utils.prompts import render_prompt


_RENDER_KWARGS = dict(
    mode="migration",
    source_url="https://old.example.com/shoes/pegasus-40",
    source_title="Nike Pegasus 40",
    source_h1="Nike Pegasus 40 Running Shoes",
    source_meta="Lightweight and responsive.",
    candidates_json=json.dumps([
        {"url": "https://new.example.com/product/pegasus-40", "score": 0.95},
    ]),
)


def test_disambiguate_prompt_renders_without_error():
    """Template must format without raising KeyError (broken brace escaping)."""
    rendered = render_prompt("disambiguate_redirect.txt", **_RENDER_KWARGS)
    assert isinstance(rendered, str)
    assert len(rendered) > 50


def test_disambiguate_prompt_contains_json_object_literal():
    """Rendered output must contain the literal JSON object the AI must echo."""
    rendered = render_prompt("disambiguate_redirect.txt", **_RENDER_KWARGS)
    assert '"winner_url"' in rendered
    assert '"confidence"' in rendered
    assert '"reasoning"' in rendered


def test_disambiguate_prompt_contains_all_placeholders():
    """All six placeholder values must appear in the rendered output."""
    rendered = render_prompt("disambiguate_redirect.txt", **_RENDER_KWARGS)
    assert _RENDER_KWARGS["source_url"] in rendered
    assert _RENDER_KWARGS["source_h1"] in rendered
    assert _RENDER_KWARGS["mode"] in rendered
