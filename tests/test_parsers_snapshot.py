"""Snapshot tests: run the CSS extractor over frozen fixture HTML and snapshot the result.

Like UI snapshot testing — the fixture is the site's frozen contract, the snapshot is the
expected extracted dataset. When a parser or selector changes, the diff is *reviewed*, not
silently accepted. Regenerate intentionally with ``uv run pytest --snapshot-update``.
"""

from __future__ import annotations

from scrapekit.extractors.css import CssExtractor
from scrapekit.models.quote import Quote
from targets.quotes import parse_quote


def _extract(html: str):
    return CssExtractor(Quote, "div.quote", parse_quote).extract(html)


def test_quotes_page1_snapshot(load_html, snapshot):
    result = _extract(load_html("quotes_page1.html"))
    assert result.valid_count == 10
    assert [q.model_dump(mode="json") for q in result.records] == snapshot


def test_quotes_page2_snapshot(load_html, snapshot):
    result = _extract(load_html("quotes_page2.html"))
    assert result.valid_count == 10
    assert [q.model_dump(mode="json") for q in result.records] == snapshot


def test_quotes_broken_snapshot(load_html, snapshot):
    """The malformed fixture: snapshot BOTH the survivors and the collected errors."""
    result = _extract(load_html("quotes_broken.html"))
    assert result.valid_count == 2
    assert result.invalid_count == 2
    summary = {
        "valid": [q.model_dump(mode="json") for q in result.records],
        "errors": [{"index": e.index, "error": e.error} for e in result.errors],
    }
    assert summary == snapshot
