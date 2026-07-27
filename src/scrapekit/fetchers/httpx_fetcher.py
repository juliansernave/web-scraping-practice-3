"""Static-HTML fetcher built on http/client.py (retries + rate limiting come free). (Day 2)

This fetcher is a thin adapter: it wraps the Day-1 :class:`ResilientClient` in the
:class:`Fetcher` protocol so the pipeline gets retries, backoff, per-host rate limiting, and
robots enforcement without knowing any of that machinery exists.
"""

from __future__ import annotations

from types import TracebackType

from scrapekit.fetchers.base import FetchResult
from scrapekit.http.client import ResilientClient
from scrapekit.logging import get_logger

log = get_logger(__name__)


class HttpxFetcher:
    """Fetch static HTML over the resilient httpx client.

    Accepts an injected :class:`ResilientClient` (so tests can pass one wired to a mock
    transport); otherwise builds and owns its own. Extra kwargs are forwarded to the client
    it constructs (e.g. ``enforce_robots=False`` for a fixture host).
    """

    def __init__(self, client: ResilientClient | None = None, **client_kwargs: object) -> None:
        self._client = client or ResilientClient(**client_kwargs)  # type: ignore[arg-type]
        self._owns_client = client is None

    @property
    def retries(self) -> int:
        """Cumulative transient-failure retries the underlying client has performed."""
        return self._client.retries

    async def fetch(self, url: str) -> FetchResult:
        resp = await self._client.fetch(url)
        return FetchResult(
            url=url,
            final_url=str(resp.url),
            status=resp.status_code,
            html=resp.text,
            elapsed=resp.elapsed.total_seconds(),
        )

    async def aclose(self) -> None:
        # Only close what we created; an injected client is the caller's to close.
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpxFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
