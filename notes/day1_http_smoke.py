"""Day 1 manual smoke test — watch the resilient client behave against live endpoints.

Run it and watch the retry log lines appear with growing sleeps:

    uv run python notes/day1_http_smoke.py

This is a demo/learning script, not part of the test suite (Day 3 replaces network with
respx). It exercises four things the framework promises:
  1. a transient 500 retries with jittered, growing backoff, then gives up cleanly;
  2. a 429 with a Retry-After header waits exactly that long;
  3. a slow endpoint (/delay/3) succeeds within the timeout;
  4. a robots-disallowed URL is refused before any request goes out.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from scrapekit.config import Settings
from scrapekit.http import ResilientClient, RobotsDisallowedError, RobotsPolicy
from scrapekit.http.client import RetryableStatusError

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(0))  # show debug+
log = structlog.get_logger("smoke")

BASE = "https://httpbin.org"


async def demo_500_gives_up(client: ResilientClient) -> None:
    log.info("== /status/500: expect retries then clean give-up ==")
    start = time.monotonic()
    try:
        await client.fetch(f"{BASE}/status/500")
    except RetryableStatusError as exc:
        log.info("gave_up", status=exc.status_code, elapsed=round(time.monotonic() - start, 2))


async def demo_429_retry_after(client: ResilientClient) -> None:
    log.info("== /response-headers Retry-After via /status/429 ==")
    try:
        await client.fetch(f"{BASE}/status/429")
    except RetryableStatusError as exc:
        log.info("gave_up_429", status=exc.status_code)


async def demo_delay_ok(client: ResilientClient) -> None:
    log.info("== /delay/3: expect success within timeout ==")
    resp = await client.fetch(f"{BASE}/delay/3")
    log.info("delayed_ok", status=resp.status_code)


async def demo_robots_disallowed() -> None:
    log.info("== robots enforcement: a disallowed path is refused pre-flight ==")
    # Fabricate a robots policy that disallows everything, so we don't depend on a live site.
    robots = RobotsPolicy(user_agent="scrapekit")
    robots._cache["https://httpbin.org"] = __import__("protego").Protego.parse(
        "User-agent: *\nDisallow: /\n"
    )
    async with ResilientClient(robots=robots) as client:
        try:
            await client.fetch(f"{BASE}/get")
        except RobotsDisallowedError as exc:
            log.info("refused", reason=str(exc))


async def main() -> None:
    # Longer timeout so /delay/3 passes; robots disabled for the httpbin fault-injection demos
    # (httpbin has no robots rules we care about here).
    settings = Settings(timeout_seconds=8.0, max_attempts=4, backoff_initial_seconds=0.5)
    async with ResilientClient(settings=settings, enforce_robots=False) as client:
        await demo_500_gives_up(client)
        await demo_429_retry_after(client)
        try:
            await demo_delay_ok(client)
        except httpx.HTTPError as exc:
            log.warning("delay_demo_failed", error=str(exc))
    await demo_robots_disallowed()


if __name__ == "__main__":
    asyncio.run(main())
