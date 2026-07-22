"""httpx AsyncClient wrapper: tenacity retry (exponential jitter, honors Retry-After),
per-host aiolimiter token bucket, explicit timeouts.

Day 1. Retry ONLY retryable failures (429, 5xx, timeouts) — never 404/403.
Smoke-test against httpbin.org/status/429, /status/500, /delay/3.

The two throttles compose but mean different things (PLAN.md, Day 4):
  * the per-host ``AsyncLimiter`` is *their* politeness policy — a token bucket that paces
    every request, widened to honor a robots.txt ``Crawl-delay``;
  * the tenacity retry is *our* recovery from transient failure — exponential backoff with
    full jitter, capped, and overridden by a server's ``Retry-After`` when present.
"""

from __future__ import annotations

import email.utils
from types import TracebackType
from urllib.parse import urlsplit

import httpx
import structlog
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from scrapekit.config import Settings, get_settings
from scrapekit.http.headers import HeaderPool
from scrapekit.http.robots import RobotsPolicy

log = structlog.get_logger(__name__)

# 429 (rate limited) and any 5xx are transient — worth retrying. Everything else in the 4xx
# range (404 gone, 403 forbidden, 401 unauthorized) is a client error: retrying won't help.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, *range(500, 600)})

# httpx transport failures worth retrying: timeouts, connection resets, protocol errors.
# httpx.TimeoutException and httpx.ConnectError both subclass httpx.TransportError.
_RETRYABLE_EXC: tuple[type[Exception], ...] = (httpx.TransportError,)


class RetryableStatusError(Exception):
    """Wraps a response whose status is transient (429/5xx) so tenacity treats it as a retry.

    Carries the parsed ``Retry-After`` (seconds) when the server supplied one.
    """

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.status_code = response.status_code
        self.retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        super().__init__(
            f"retryable status {response.status_code} for "
            f"{response.request.method} {response.request.url}"
        )


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header: either delta-seconds or an HTTP-date. None if absent."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    # HTTP-date form: compute seconds until that instant.
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(tz=parsed.tzinfo or _dt.timezone.utc)
    return max(0.0, (parsed - now).total_seconds())


class ResilientClient:
    """A polite, self-healing async HTTP client.

    Usage::

        async with ResilientClient() as client:
            resp = await client.fetch("https://books.toscrape.com/")

    Retryable failures back off with jittered exponential delay (or the server's Retry-After);
    non-retryable ones (404/403, robots-disallowed) surface immediately.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        robots: RobotsPolicy | None = None,
        enforce_robots: bool = True,
        header_pool: HeaderPool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.header_pool = header_pool or HeaderPool()

        # A single robots user-agent token; real UA rotation is for the request headers.
        self.enforce_robots = enforce_robots
        self._robots = robots
        if self.enforce_robots and self._robots is None:
            self._robots = RobotsPolicy(
                user_agent="scrapekit", timeout=self.settings.timeout_seconds
            )

        timeout = httpx.Timeout(self.settings.timeout_seconds)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            proxy=self.settings.proxy_url,
            transport=transport,
        )
        self._limiters: dict[str, AsyncLimiter] = {}

    # --- lifecycle -------------------------------------------------------------------------
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

    # --- rate limiting ---------------------------------------------------------------------
    async def _limiter_for(self, url: str) -> AsyncLimiter:
        """One token bucket per host, widened to the robots.txt Crawl-delay if it's stricter."""
        host = urlsplit(url).netloc
        limiter = self._limiters.get(host)
        if limiter is not None:
            return limiter

        rps = self.settings.requests_per_second
        if self._robots is not None:
            delay = await self._robots.crawl_delay(url)
            if delay and delay > 0:
                # Crawl-delay of N seconds => at most 1 request per N seconds. Honor it if it
                # is more polite (slower) than our default rate.
                crawl_rps = 1.0 / delay
                if crawl_rps < rps:
                    limiter = AsyncLimiter(1, delay)
                    log.info("ratelimit.crawl_delay", host=host, crawl_delay=delay)

        if limiter is None:
            limiter = AsyncLimiter(rps, 1)
        self._limiters[host] = limiter
        return limiter

    # --- backoff ---------------------------------------------------------------------------
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
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        log.warning(
            "http.retry",
            attempt=retry_state.attempt_number,
            sleep=round(retry_state.next_action.sleep, 3) if retry_state.next_action else None,
            error=str(exc) if exc else None,
        )

    # --- the request -----------------------------------------------------------------------
    async def _do_request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._robots is not None:
            await self._robots.raise_if_disallowed(url)

        limiter = await self._limiter_for(url)
        headers = {**self.header_pool.next_headers(), **(kwargs.pop("headers", None) or {})}

        async with limiter:
            resp = await self._client.request(method, url, headers=headers, **kwargs)

        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableStatusError(resp)
        # Non-retryable 4xx (404/403/401/...) raise here and are NOT retried.
        resp.raise_for_status()
        return resp

    async def fetch(self, url: str, *, method: str = "GET", **kwargs: object) -> httpx.Response:
        """Fetch ``url`` with retries, rate limiting, and robots enforcement applied.

        Raises :class:`RobotsDisallowedError` for off-limits URLs, ``httpx.HTTPStatusError``
        for non-retryable 4xx, or the last :class:`RetryableStatusError`/transport error once
        ``max_attempts`` is exhausted.
        """
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type((RetryableStatusError, *_RETRYABLE_EXC)),
            before_sleep=self._log_retry,
            reraise=True,
        )
        bound = log.bind(url=url, method=method)
        bound.debug("http.fetch")
        async for attempt in retryer:
            with attempt:
                return await self._do_request(method, url, **kwargs)
        raise AssertionError("unreachable: AsyncRetrying always returns or raises")
