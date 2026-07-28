"""Resilient HTTP client (Pattern 4 in references/patterns.md): retry transient failures
only, jittered backoff honoring Retry-After, per-host rate limiting widened by
robots.txt Crawl-delay, honest rotating headers, robots.txt enforcement.

Genericized from a real scraper-framework build. Uses httpx + tenacity + aiolimiter +
protego as concrete libraries — swap for your stack's equivalents (e.g. axios +
p-retry + bottleneck + robots-parser in Node; requests + tenacity + a token-bucket lib
in sync Python). The libraries are interchangeable; the shape below — what gets
retried, what gets rate-limited, what gets enforced — is the reusable part.
"""

from __future__ import annotations

import email.utils
import itertools
from dataclasses import dataclass
from types import TracebackType
from urllib.parse import urlsplit

import httpx
from aiolimiter import AsyncLimiter
from protego import Protego
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# --- Configuration knobs ------------------------------------------------------------------
# Pull these from your project's settings/config, not hardcoded constants like this —
# shown as module-level defaults here purely so the file is runnable standalone.


@dataclass
class ClientSettings:
    max_attempts: int = 5
    backoff_initial_seconds: float = 0.5
    backoff_max_seconds: float = 20.0
    requests_per_second: float = 2.0
    timeout_seconds: float = 15.0
    proxy_url: str | None = None


# --- Header rotation: honest variety, not evasion ----------------------------------------

# A small pool of current, real browser UA strings. Kept short on purpose — this is
# polite variety (some sites vary markup/rate limits by UA), not fingerprint spoofing.
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
)

BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Deliberately no hardcoded Accept-Encoding: let your HTTP library advertise only
    # the encodings it can actually decode. Advertising "br" you can't decode gets you
    # garbage responses when a server takes you up on it.
    "Connection": "keep-alive",
}


class HeaderPool:
    """Round-robins user agents and merges in the honest base headers.

    Deterministic round-robin (not random.choice) so tests can pin a single UA and
    assert rotation order.
    """

    def __init__(
        self,
        user_agents: tuple[str, ...] = USER_AGENTS,
        base_headers: dict[str, str] | None = None,
    ) -> None:
        if not user_agents:
            raise ValueError("HeaderPool needs at least one user agent")
        self._base = dict(base_headers if base_headers is not None else BASE_HEADERS)
        self._cycle = itertools.cycle(user_agents)

    def next_headers(self) -> dict[str, str]:
        return {**self._base, "User-Agent": next(self._cycle)}


# --- robots.txt enforcement ---------------------------------------------------------------


class RobotsDisallowedError(Exception):
    """Raised when a URL is disallowed by the target's robots.txt. Never retried."""

    def __init__(self, url: str, user_agent: str) -> None:
        self.url = url
        self.user_agent = user_agent
        super().__init__(f"robots.txt disallows {user_agent!r} from fetching {url}")


class RobotsPolicy:
    """Fetches, caches, and enforces robots.txt per origin.

    Policy on a missing/broken robots.txt follows RFC 9309 convention: a 4xx (including
    the common 404 "no robots file") means everything is allowed; a 5xx or network
    failure means we couldn't learn the rules, so we stay conservative and disallow
    until it recovers. Uses its own short-timeout, non-retrying client — fetching the
    rulebook must not itself be rate-limited or subjected to the retry logic it governs.
    """

    def __init__(self, user_agent: str = "*", *, timeout: float = 10.0) -> None:
        self.user_agent = user_agent
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, Protego] = {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._client

    async def _rules_for(self, url: str) -> Protego:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._cache:
            return self._cache[origin]

        robots_url = f"{origin}/robots.txt"
        try:
            resp = await self._get_client().get(robots_url)
        except httpx.HTTPError:
            rules = Protego.parse("User-agent: *\nDisallow: /\n")  # can't verify -> conservative
        else:
            if resp.status_code >= 500:
                rules = Protego.parse("User-agent: *\nDisallow: /\n")
            elif resp.status_code >= 400:
                rules = Protego.parse("User-agent: *\nAllow: /\n")  # no robots.txt -> allow all
            else:
                rules = Protego.parse(resp.text)

        self._cache[origin] = rules
        return rules

    async def is_allowed(self, url: str) -> bool:
        rules = await self._rules_for(url)
        return rules.can_fetch(url, self.user_agent)

    async def crawl_delay(self, url: str) -> float | None:
        rules = await self._rules_for(url)
        return rules.crawl_delay(self.user_agent)

    async def raise_if_disallowed(self, url: str) -> None:
        if not await self.is_allowed(url):
            raise RobotsDisallowedError(url, self.user_agent)


# --- Retry-worthy failures -----------------------------------------------------------------

# 429 (rate limited) and any 5xx are transient — worth retrying. Everything else in the
# 4xx range (404, 403, 401) is a client error: retrying won't help and just adds load.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, *range(500, 600)})
_RETRYABLE_EXC: tuple[type[Exception], ...] = (httpx.TransportError,)


class RetryableStatusError(Exception):
    """Wraps a response whose status is transient (429/5xx) so the retry layer catches it.

    Carries the parsed Retry-After (seconds) when the server supplied one.
    """

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.status_code = response.status_code
        self.retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        super().__init__(f"retryable status {response.status_code} for {response.request.url}")


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(tz=parsed.tzinfo or _dt.timezone.utc)
    return max(0.0, (parsed - now).total_seconds())


# --- The client ------------------------------------------------------------------------


class ResilientClient:
    """A polite, self-healing async HTTP client.

        async with ResilientClient() as client:
            resp = await client.fetch("https://example.test/")

    Retryable failures back off with jittered exponential delay (or the server's
    Retry-After); non-retryable ones (404/403, robots-disallowed) surface immediately.
    """

    def __init__(
        self,
        *,
        settings: ClientSettings | None = None,
        robots: RobotsPolicy | None = None,
        enforce_robots: bool = True,
        header_pool: HeaderPool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or ClientSettings()
        self.header_pool = header_pool or HeaderPool()

        self.enforce_robots = enforce_robots
        self._robots = robots
        if self.enforce_robots and self._robots is None:
            self._robots = RobotsPolicy(user_agent="scraper", timeout=self.settings.timeout_seconds)

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.timeout_seconds),
            follow_redirects=True,
            proxy=self.settings.proxy_url,
            transport=transport,  # inject a mock transport in tests — see test_harness_example.py
        )
        self._limiters: dict[str, AsyncLimiter] = {}
        self.retries = 0  # cumulative retries this client absorbed — surface in a run report

    async def __aenter__(self) -> ResilientClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._robots is not None:
            await self._robots.aclose()

    async def _limiter_for(self, url: str) -> AsyncLimiter:
        """One token bucket per host, widened to the robots.txt Crawl-delay if it's stricter."""
        host = urlsplit(url).netloc
        limiter = self._limiters.get(host)
        if limiter is not None:
            return limiter

        rps = self.settings.requests_per_second
        if self._robots is not None:
            delay = await self._robots.crawl_delay(url)
            if delay and delay > 0 and (1.0 / delay) < rps:
                limiter = AsyncLimiter(1, delay)  # site's stated preference overrides our default

        if limiter is None:
            limiter = AsyncLimiter(rps, 1)
        self._limiters[host] = limiter
        return limiter

    def _wait(self, retry_state: RetryCallState) -> float:
        """Full-jitter exponential backoff, unless the server told us exactly how long to wait."""
        outcome = retry_state.outcome
        if outcome is not None:
            exc = outcome.exception()
            if isinstance(exc, RetryableStatusError) and exc.retry_after is not None:
                return min(exc.retry_after, self.settings.backoff_max_seconds)
        jittered = wait_random_exponential(
            multiplier=self.settings.backoff_initial_seconds,
            max=self.settings.backoff_max_seconds,
        )
        return jittered(retry_state)

    def _log_retry(self, retry_state: RetryCallState) -> None:
        self.retries += 1  # fires once per retry attempt — count for the run report

    async def _do_request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._robots is not None:
            await self._robots.raise_if_disallowed(url)

        limiter = await self._limiter_for(url)
        headers = {**self.header_pool.next_headers(), **(kwargs.pop("headers", None) or {})}

        async with limiter:
            resp = await self._client.request(method, url, headers=headers, **kwargs)

        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableStatusError(resp)
        resp.raise_for_status()  # non-retryable 4xx (404/403/401/...) raise and are NOT retried
        return resp

    async def fetch(self, url: str, *, method: str = "GET", **kwargs: object) -> httpx.Response:
        """Fetch `url` with retries, rate limiting, and robots enforcement applied.

        Raises RobotsDisallowedError for off-limits URLs, httpx.HTTPStatusError for
        non-retryable 4xx, or the last RetryableStatusError/transport error once
        max_attempts is exhausted.
        """
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type((RetryableStatusError, *_RETRYABLE_EXC)),
            before_sleep=self._log_retry,
            reraise=True,
        )
        async for attempt in retryer:
            with attempt:
                return await self._do_request(method, url, **kwargs)
        raise AssertionError("unreachable: AsyncRetrying always returns or raises")
