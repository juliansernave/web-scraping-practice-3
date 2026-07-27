"""HTTP-behavior tests: inject fault sequences at the transport layer with respx.

Most scraper authors never test the failure paths — that's the differentiator. respx returns
scripted responses (``[429, 429, 200]``, ``[500]*6``, a transport error) so the retry, backoff,
and rate-limiting machinery is asserted *deterministically*, with zero network.
"""

from __future__ import annotations

import time

import httpx
import pytest
from httpx import Response

from scrapekit.config import Settings
from scrapekit.http.client import ResilientClient, RetryableStatusError

URL = "https://example.test/data"


def _fast_settings(**overrides) -> Settings:
    """Settings tuned so retries are near-instant — we test the logic, not the wall clock."""
    base = dict(
        max_attempts=5,
        backoff_initial_seconds=0.001,
        backoff_max_seconds=0.005,
        requests_per_second=1000.0,
        timeout_seconds=5.0,
    )
    base.update(overrides)
    return Settings(**base)


async def test_retries_then_succeeds(respx_mock):
    """[429, 429, 200] -> three attempts, final success."""
    route = respx_mock.get(URL).mock(
        side_effect=[Response(429), Response(429), Response(200, text="ok")]
    )
    async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
        resp = await client.fetch(URL)
    assert resp.status_code == 200
    assert resp.text == "ok"
    assert route.call_count == 3


async def test_gives_up_after_max_attempts(respx_mock):
    """A permanently-500 endpoint gives up after exactly max_attempts and re-raises."""
    route = respx_mock.get(URL).mock(return_value=Response(500))
    async with ResilientClient(
        settings=_fast_settings(max_attempts=5), enforce_robots=False
    ) as client:
        with pytest.raises(RetryableStatusError):
            await client.fetch(URL)
    assert route.call_count == 5


async def test_does_not_retry_404(respx_mock):
    """404 is a client error — retrying can't help, so it fails on the first attempt."""
    route = respx_mock.get(URL).mock(return_value=Response(404))
    async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch(URL)
    assert route.call_count == 1


async def test_retries_transport_error(respx_mock):
    """A connection error is transient (network blip) — retry it, then succeed."""
    route = respx_mock.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), Response(200, text="ok")]
    )
    async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
        resp = await client.fetch(URL)
    assert resp.status_code == 200
    assert route.call_count == 2


async def test_honors_retry_after_header(respx_mock):
    """When the server sends Retry-After, we take that path (0s here to keep the test instant)."""
    route = respx_mock.get(URL).mock(
        side_effect=[Response(429, headers={"Retry-After": "0"}), Response(200, text="ok")]
    )
    async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
        resp = await client.fetch(URL)
    assert resp.status_code == 200
    assert route.call_count == 2


async def test_rate_limiter_spaces_requests(respx_mock):
    """AsyncLimiter(2, 1): the first two requests burst, the third waits ~0.5s for a refill."""
    respx_mock.get(URL).mock(return_value=Response(200, text="x"))
    settings = _fast_settings(requests_per_second=2.0, max_attempts=1)
    async with ResilientClient(settings=settings, enforce_robots=False) as client:
        start = time.perf_counter()
        for _ in range(3):
            await client.fetch(URL)
        elapsed = time.perf_counter() - start
    # Lower-bound assertion only (CI can be slower, never faster): proves the limiter throttled.
    assert elapsed >= 0.4
