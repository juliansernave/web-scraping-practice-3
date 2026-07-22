"""robots.txt as an enforced contract: fetch + parse with protego, refuse disallowed URLs,
feed Crawl-delay into the rate limiter.

Day 1. protego is what Scrapy uses — handles wildcards and Crawl-delay correctly.

Policy on a missing/broken robots.txt follows the RFC-9309 convention: a 4xx (including the
common 404 "no robots file") means *everything is allowed*; a 5xx or network failure means
we could not learn the rules, so we stay conservative and treat the site as *disallowed*
until it recovers. Results are cached per host so we fetch each robots.txt at most once.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
import structlog
from protego import Protego

log = structlog.get_logger(__name__)


class RobotsDisallowedError(Exception):
    """Raised when a URL is disallowed by the target's robots.txt. Never retried."""

    def __init__(self, url: str, user_agent: str) -> None:
        self.url = url
        self.user_agent = user_agent
        super().__init__(f"robots.txt disallows {user_agent!r} from fetching {url}")


def _robots_url(url: str) -> tuple[str, str]:
    """Return ``(origin, robots_url)`` for the host serving ``url``."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return origin, f"{origin}/robots.txt"


class RobotsPolicy:
    """Fetches, caches, and enforces robots.txt per origin.

    Uses its *own* short-timeout, non-retrying httpx client: fetching the rulebook must not
    itself be rate-limited or subjected to the retry storm it governs. Concurrent lookups for
    the same origin share a single fetch via a per-origin lock.
    """

    def __init__(
        self,
        user_agent: str = "*",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.user_agent = user_agent
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._cache: dict[str, Protego] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> RobotsPolicy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._client

    async def _rules_for(self, url: str) -> Protego:
        origin, robots_url = _robots_url(url)
        if origin in self._cache:
            return self._cache[origin]

        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._cache:  # another coroutine fetched while we waited
                return self._cache[origin]
            rules = await self._fetch_rules(robots_url)
            self._cache[origin] = rules
            return rules

    async def _fetch_rules(self, robots_url: str) -> Protego:
        try:
            resp = await self._get_client().get(robots_url)
        except httpx.HTTPError as exc:
            # Couldn't reach robots.txt at all — stay conservative (disallow everything).
            log.warning("robots.fetch_failed", robots_url=robots_url, error=str(exc))
            return Protego.parse("User-agent: *\nDisallow: /\n")

        if resp.status_code >= 500:
            log.warning("robots.server_error", robots_url=robots_url, status=resp.status_code)
            return Protego.parse("User-agent: *\nDisallow: /\n")
        if resp.status_code >= 400:
            # No robots.txt (or forbidden) — RFC convention is "allow all".
            log.info("robots.absent", robots_url=robots_url, status=resp.status_code)
            return Protego.parse("User-agent: *\nAllow: /\n")

        log.info("robots.loaded", robots_url=robots_url, status=resp.status_code)
        return Protego.parse(resp.text)

    async def is_allowed(self, url: str) -> bool:
        """True if ``self.user_agent`` may fetch ``url`` per the host's robots.txt."""
        rules = await self._rules_for(url)
        return rules.can_fetch(url, self.user_agent)

    async def crawl_delay(self, url: str) -> float | None:
        """The ``Crawl-delay`` (seconds) this host requests for us, or None if unspecified."""
        rules = await self._rules_for(url)
        return rules.crawl_delay(self.user_agent)

    async def raise_if_disallowed(self, url: str) -> None:
        """Raise :class:`RobotsDisallowedError` if ``url`` is off-limits."""
        if not await self.is_allowed(url):
            raise RobotsDisallowedError(url, self.user_agent)
