"""Fetcher protocol: async fetch(url) -> FetchResult(html, status, final_url, elapsed).

Day 2. Every fetcher implements this seam so it can be swapped and mocked in tests.

The seam is the whole point: the pipeline depends on ``Fetcher``, not on httpx or Playwright.
Day 4 swaps in the Playwright fetcher on the JS quotes site with the extractor and schema
unchanged; Day 3 injects a fake fetcher that serves fixture HTML so the suite runs with zero
network. ``FetchResult`` is a plain dataclass, not a pydantic model — it's *our* transport
output, not untrusted input. Validation lives at exactly one boundary: the extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FetchResult:
    """The raw result of fetching one URL, independent of how it was fetched."""

    url: str  # the URL we requested
    final_url: str  # where we ended up after redirects (base for resolving relative links)
    status: int
    html: str
    elapsed: float  # wall-clock seconds for the fetch, for the run report


@runtime_checkable
class Fetcher(Protocol):
    """Fetches a URL's HTML. Implemented by HttpxFetcher (Day 2) and PlaywrightFetcher (Day 4)."""

    async def fetch(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...
