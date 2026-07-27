"""End-to-end pipeline tests — the whole fetch -> extract -> validate -> store flow, no network.

Two flavors of "no network":
  * a mocked httpx transport (respx) serving fixture HTML — exercises the real HttpxFetcher +
    ResilientClient path;
  * a hand-written FakeFetcher — proves the seam: the pipeline depends on the Fetcher protocol,
    so any object with ``async fetch()`` drives it. This is where dependency injection pays off.

The whole module runs in well under the 2s budget the plan sets.
"""

from __future__ import annotations

from httpx import Response

from scrapekit.config import Settings
from scrapekit.fetchers.base import FetchResult
from scrapekit.fetchers.httpx_fetcher import HttpxFetcher
from scrapekit.pipeline import run
from scrapekit.storage import JsonlStore
from targets.quotes import QUOTES


def _fast_settings() -> Settings:
    return Settings(max_attempts=1, requests_per_second=1000.0)


async def test_pipeline_over_mocked_transport(load_html, tmp_path, respx_mock):
    """Real fetcher + client, but the transport is mocked — 10 quotes extracted and stored."""
    respx_mock.get(QUOTES.url).mock(return_value=Response(200, text=load_html("quotes_page1.html")))
    out = tmp_path / "quotes.jsonl"

    async with HttpxFetcher(settings=_fast_settings(), enforce_robots=False) as fetcher:
        with JsonlStore(out, dedup_fields=QUOTES.dedup_fields) as store:
            report = await run(QUOTES, fetcher=fetcher, store=store)

    assert (report.valid, report.invalid, report.stored, report.duplicates) == (10, 0, 10, 0)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 10


async def test_pipeline_dedups_across_runs(load_html, tmp_path, respx_mock):
    """Running twice into the same store: the second run stores nothing (idempotent)."""
    respx_mock.get(QUOTES.url).mock(return_value=Response(200, text=load_html("quotes_page1.html")))
    out = tmp_path / "quotes.jsonl"

    async def one_run():
        async with HttpxFetcher(settings=_fast_settings(), enforce_robots=False) as fetcher:
            with JsonlStore(out, dedup_fields=QUOTES.dedup_fields) as store:
                return await run(QUOTES, fetcher=fetcher, store=store)

    first = await one_run()
    second = await one_run()

    assert first.stored == 10
    assert (second.stored, second.duplicates) == (0, 10)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 10  # no dup rows on disk


class FakeFetcher:
    """Serves a fixed HTML string — the pipeline can't tell it from a real fetcher."""

    def __init__(self, html: str) -> None:
        self._html = html

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(url=url, final_url=url, status=200, html=self._html, elapsed=0.0)

    async def aclose(self) -> None:
        return None


async def test_pipeline_collects_invalid_records(load_html, tmp_path):
    """The broken fixture drives the pipeline: 2 valid stored, 2 invalid counted, none raised."""
    out = tmp_path / "quotes.jsonl"
    with JsonlStore(out, dedup_fields=QUOTES.dedup_fields) as store:
        report = await run(
            QUOTES, fetcher=FakeFetcher(load_html("quotes_broken.html")), store=store
        )

    assert report.records_extracted == 4
    assert (report.valid, report.invalid, report.stored) == (2, 2, 2)
