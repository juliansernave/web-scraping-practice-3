"""Drift detection: mutate a fixture's class names and assert the alert fires.

Selector drift is the #1 silent failure of production scrapers. These tests are the production
assertion: a healthy page yields a high extraction rate (no alert); a page whose markup has
"drifted" (renamed classes) yields a low rate (alert). Two drift modes are covered — the item
selector breaking, and a field selector breaking — because they fail differently but must both
be caught.
"""

from __future__ import annotations

from dataclasses import replace

from scrapekit.fetchers.base import FetchResult
from scrapekit.pipeline import run
from scrapekit.storage import JsonlStore
from targets.books import BOOKS


class FakeFetcher:
    """Serves one fixed HTML string regardless of URL — no browser, no network."""

    def __init__(self, html: str) -> None:
        self._html = html

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(url=url, final_url=url, status=200, html=self._html, elapsed=0.0)

    async def aclose(self) -> None:
        return None


def _single_page_books():
    """The books target scoped to one page, so a single fixture drives the whole crawl."""
    return replace(BOOKS, page_urls=(BOOKS.url,))


async def _run(html: str, tmp_path):
    with JsonlStore(tmp_path / "books.jsonl", dedup_fields=("url",)) as store:
        return await run(_single_page_books(), fetcher=FakeFetcher(html), store=store)


async def test_no_drift_on_healthy_page(load_html, tmp_path):
    report = await _run(load_html("books_page1.html"), tmp_path)
    assert report.valid == 20
    assert report.extraction_rate == 1.0
    assert report.drift_alert is False


async def test_drift_alert_when_item_class_renamed(load_html, tmp_path):
    """Rename the item container class: the selector now matches nothing -> rate 0 -> alert."""
    html = load_html("books_page1.html").replace("product_pod", "product_moved")
    report = await _run(html, tmp_path)
    assert report.records_extracted == 0
    assert report.extraction_rate == 0.0
    assert report.drift_alert is True


async def test_drift_alert_when_field_class_renamed(load_html, tmp_path):
    """Items still match, but the price node is gone -> every item fails to parse -> alert."""
    html = load_html("books_page1.html").replace("price_color", "price_moved")
    report = await _run(html, tmp_path)
    assert report.records_extracted == 20  # the item selector still matches
    assert report.valid == 0  # ...but none validate
    assert report.drift_alert is True
