"""httpx AsyncClient wrapper: tenacity retry (exponential jitter, honors Retry-After),
per-host aiolimiter token bucket, explicit timeouts.

Day 1. Retry ONLY retryable failures (429, 5xx, timeouts) — never 404/403.
Smoke-test against httpbin.org/status/429, /status/500, /delay/3.
"""

# TODO(Day 1): ResilientClient with async fetch(url) -> httpx.Response.
