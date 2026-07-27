"""JS-rendering fetcher (async Playwright) for pages httpx can't see, e.g. quotes.toscrape.com/js/.

Day 4. Reuse your existing Playwright skills; same Fetcher protocol, same downstream pipeline.
Requires: uv run playwright install chromium

This is the A/B payoff of the Fetcher seam: on ``quotes.toscrape.com/js/`` the quotes are built
by JavaScript, so the httpx fetcher sees an empty page and extracts 0. This fetcher drives a
real browser that runs the JS, so ``div.quote`` exists by the time we read the DOM — and the
extractor and schema downstream are byte-for-byte unchanged. Only the fetcher swapped.
"""

from __future__ import annotations

import time
from types import TracebackType

from scrapekit.fetchers.base import FetchResult
from scrapekit.logging import get_logger

log = get_logger(__name__)


class PlaywrightFetcher:
    """Fetch fully-rendered HTML via a headless browser.

    The browser is launched lazily on first fetch and reused across fetches (launching is the
    expensive part); ``aclose`` tears it down. No retry/rate-limit layer here — that lives in
    the httpx client; a browser fetch is already heavy and slow enough to be self-pacing.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        wait_until: str = "networkidle",
        timeout: float = 30.0,
    ) -> None:
        self._headless = headless
        self._wait_until = wait_until
        self._timeout_ms = int(timeout * 1000)
        self._pw = None
        self._browser = None
        self.retries = 0  # no retry layer; present so the RunReport can read it uniformly

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)
            log.info("playwright.launched", headless=self._headless)

    async def fetch(self, url: str) -> FetchResult:
        await self._ensure_browser()
        assert self._browser is not None
        page = await self._browser.new_page()
        start = time.perf_counter()
        try:
            resp = await page.goto(url, wait_until=self._wait_until, timeout=self._timeout_ms)
            html = await page.content()  # the rendered DOM, after JS has run
            final_url = page.url
            status = resp.status if resp is not None else 0
        finally:
            await page.close()
        elapsed = time.perf_counter() - start
        log.info("playwright.fetch", url=url, status=status, elapsed=round(elapsed, 3))
        return FetchResult(url=url, final_url=final_url, status=status, html=html, elapsed=elapsed)

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> PlaywrightFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
