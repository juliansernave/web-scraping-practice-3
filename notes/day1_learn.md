# Day 1 — Learn: Resilient HTTP

Study notes for the Day 1 concepts, each grounded in the code we built
(`src/scrapekit/config.py`, `src/scrapekit/http/`). Theory ↔ implementation.

**One-sentence synthesis (for the pitch):**
> *"Retries recover from **their** transient failures; rate limiting respects **their**
> infrastructure; robots.txt enforces **their** contract — and jitter is what keeps my
> recovery from becoming their next outage."*

---

## 1. Which failures are retryable — and which aren't

Core insight: **retrying only helps if the failure is transient.** Retrying a permanent
failure wastes time, hammers the server, and delays the inevitable error.

| Status | Retryable? | Why |
|---|---|---|
| **429** Too Many Requests | ✅ Yes | *You* went too fast. Back off and it'll succeed. |
| **500, 502, 503, 504** | ✅ Yes | Server hiccup, overloaded, deploying. Usually transient. |
| **Timeouts / connection resets** | ✅ Yes | Network blip, dropped packet, TLS renegotiation. |
| **404** Not Found | ❌ No | The resource isn't there. It won't appear on attempt 3. |
| **403** Forbidden | ❌ No | You're blocked / unauthorized. Retrying looks like an attack. |
| **401** Unauthorized | ❌ No | Bad credentials. Fix the auth, don't retry. |
| **400** Bad Request | ❌ No | *Your* request is malformed. Same request = same error. |

Mental model: **retry the server's problems and the network's problems; never retry your
own problems.** A 4xx (except 429) is almost always *your* fault.

**In our code** (`http/client.py`):
```python
RETRYABLE_STATUS = frozenset({429, *range(500, 600)})
_RETRYABLE_EXC = (httpx.TransportError,)   # timeouts + connection errors subclass this
```
The control flow in `_do_request` encodes the table:
```python
if resp.status_code in RETRYABLE_STATUS:
    raise RetryableStatusError(resp)   # tenacity catches this → retries
resp.raise_for_status()                # any *other* 4xx/5xx → HTTPStatusError → NOT retried
return resp
```
`RetryableStatusError` is in tenacity's retry set; `httpx.HTTPStatusError` is not. That single
distinction makes a 500 loop and a 404 fail instantly (verified: 404 → `attempts=1`).

> **TAE angle:** same as test triage — *flaky* (retry) vs *deterministic failure* (real bug,
> don't retry).

---

## 2. Exponential backoff **with jitter** — and the thundering herd

**Backoff** = wait longer between retries (0.5s, 1s, 2s, 4s… doubling = *exponential*). A
struggling server needs breathing room.

**Why jitter matters:** if 1,000 clients all hit a 503 at the same instant and use *pure*
exponential backoff, they all retry at t+0.5s, t+1.5s, t+3.5s… in lockstep —
**synchronized retry waves that re-overload the server the moment it recovers.** That's the
**thundering herd**. Jitter adds randomness so the herd smears into a survivable trickle.

**Full jitter** (the variant we use): wait a random amount in `[0, base]`:
```
sleep = random.uniform(0, min(cap, initial * 2 ** attempt))
```
Exponential term keeps the *ceiling* growing; `random.uniform(0, …)` decorrelates clients.
(From the AWS "Exponential Backoff and Jitter" article — worth reading.)

**In our code**, `ResilientClient._wait` delegates to tenacity's `wait_random_exponential`:
```python
wait_random_exponential(
    multiplier=self.settings.backoff_initial_seconds,  # 0.5
    max=self.settings.backoff_max_seconds,             # 30 cap
)
```
Live smoke sleeps were `0.023, 0.938, 0.542`: growing *ceiling*, non-monotonic because each
is a random draw under that rising cap. The non-monotonicity **is** the jitter working.

---

## 3. Honoring `Retry-After`

Sometimes the server tells you how long to wait via the `Retry-After` header (common on 429
and 503). Two forms:
- **delta-seconds:** `Retry-After: 120` → wait 120s.
- **HTTP-date:** `Retry-After: Wed, 22 Jul 2026 16:30:00 GMT` → wait until that instant.

**Rule: if the server tells you, obey it** — it knows its own recovery window. Your
exponential guess is the fallback for when it *doesn't* tell you.

**In our code**, `RetryableStatusError.__init__` parses the header eagerly; `_wait` gives it
precedence:
```python
if isinstance(exc, RetryableStatusError) and exc.retry_after is not None:
    return min(exc.retry_after, self.settings.backoff_max_seconds)
# else: fall through to jittered exponential
```
`_parse_retry_after` handles both forms (`.isdigit()` for seconds,
`email.utils.parsedate_to_datetime` for the date). We still clamp to `backoff_max_seconds`:
a server sending `Retry-After: 999999` shouldn't hang the crawler for 11 days.
**Trust, but bound.**

---

## 4. Token-bucket rate limiting vs `sleep(1)` — policy, not a pause

Problems with `sleep(1)`:
- It's a **fixed pause**, not a **rate** — `sleep(1)` after a 3s request = 1 request / 4s;
  you don't actually know your rate.
- It **doesn't compose with concurrency** — 10 async requests each `sleep(1)` still leave at once.
- It's **not per-host** — scraping two sites punishes the fast one for the slow one.

A **token bucket** is a *policy*: a bucket holds N tokens, refills at a steady rate, each
request spends one, empty bucket = wait for refill. `AsyncLimiter(10, 1)` = "10 tokens / 1s":
- True **rate ceiling** regardless of request duration.
- **Burst tolerance** — idle → full bucket → a quick burst is allowed, then settles.
- Correct **under concurrency** — 50 coroutines sharing one limiter still emit at 10/s total.

**In our code**, `ResilientClient._limiter_for` keeps **one bucket per host**:
```python
self._limiters: dict[str, AsyncLimiter] = {}   # host → its own bucket
...
async with limiter:                             # blocks until a token frees
    resp = await self._client.request(...)
```
It's also the seam where a robots.txt `Crawl-delay` widens the bucket:
```python
if crawl_rps < rps:
    limiter = AsyncLimiter(1, delay)   # Crawl-delay: 30 → 1 request / 30s
```

> Framing (also a Day 4 talking point): **the rate limiter is *their* politeness policy; the
> concurrency semaphore [Day 4] is *your* resource control.** Two different knobs.

---

## 5. robots.txt as a parsed, enforced contract

Upgrade from "mentioned in a comment" to **an executable contract enforced before every
request.**

- Advisory, but honoring it is the ethical + legal-safety baseline (and a PoC talking point).
- Correct parsing is **non-trivial**: wildcards (`Disallow: /*.pdf$`), longest-match
  precedence, per-user-agent groups, `Allow` overriding `Disallow`, `Crawl-delay`. We use
  **protego** — the same parser **Scrapy** uses.
- **Enforce pre-flight** — check before sending, so a disallowed URL costs zero traffic
  (verified: robots-disallow → `requests_made=0`).

**What to do when robots.txt itself fails** (`_fetch_rules`, RFC-9309 convention):
```python
if resp.status_code >= 500:   → disallow all   # can't learn the rules → stay conservative
if resp.status_code >= 400:   → allow all      # 404 = "no robots file" = nothing forbidden
network error:                → disallow all   # same as 5xx: unknown → conservative
```
Asymmetry is deliberate: **"no file" means permission; "can't reach the file" means abstain.**
Naive scrapers treat every robots failure as "allow all" — wrong when the server is just down.

Also: **cached per origin** (`self._cache` + per-origin `asyncio.Lock`, fetch once even under
concurrency); uses **its own non-retrying client** so fetching the rulebook isn't subject to
the machinery it governs (avoids chicken-and-egg).

> **TAE angle:** robots enforcement is a **precondition assertion** — like
> `assert user.is_authorized` at the top of a test. Fail fast, before doing work.
