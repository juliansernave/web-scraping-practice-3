"""Pytest root config.

Living at the repo root has two effects:
  1. pytest puts this directory on ``sys.path``, so tests can ``import targets`` (the config
     package lives at the repo root, outside the packaged ``src/scrapekit`` — see targets/).
  2. it's the natural home for fixtures shared across every test module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_HTML = Path(__file__).parent / "tests" / "fixtures" / "html"


@pytest.fixture(scope="session")
def html_fixtures() -> Path:
    """Directory holding frozen HTML fixtures — the crawler's contract with each site."""
    return FIXTURES_HTML


@pytest.fixture
def load_html(html_fixtures: Path):
    """Return a reader ``load_html(name) -> str`` for a fixture file, e.g. 'quotes_page1.html'."""

    def _load(name: str) -> str:
        return (html_fixtures / name).read_text(encoding="utf-8")

    return _load
