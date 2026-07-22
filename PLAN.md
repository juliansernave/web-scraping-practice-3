# 7-Day Web Scraping Upskilling Plan → PoC Readiness

**Goal:** be ready to present a web-scraping proof of concept at work in ~2 weeks. The target is
still unknown, so the deliverable is a **framework, not a scraper** — skills that transfer to any
target the PoC ends up needing.

**Cadence:** 3–4 hours/day for 7 days. Every day ends with working, committed code.

**The capstone — `scrapekit`:** a target-agnostic, production-grade scraping pipeline with
pluggable fetchers (httpx for static / Playwright for JS) and dual extraction strategies
(CSS selectors / LLM) sharing one Pydantic schema, producing validated, deduplicated,
drift-monitored output — backed by a test suite that runs with zero network access.

**The pitch:** *"Most scraping PoCs demo a happy path. As a test automation engineer, I built
the unhappy paths first."*

---

## Why these gaps (from a review of your previous projects)

You already do well: right tool per job (requests/BS4 static, async Playwright for JS/scroll,
Apify for anti-bot platforms), per-item error isolation, and real operational maturity in
`apify-watch-stores` (config separation, `.env` secrets, cost budgeting with hard caps).

This week closes what's missing versus industry practice:

1. **No retry/backoff or real rate limiting** — only a fixed `sleep(1)`.
2. **No data validation layer** — hand-mapped `.get()` chains pass bad data silently.
3. **`print` logging** — no structured, machine-parsable logs.
4. **Zero tests** — ironic for a test automation engineer. This is your differentiator to exploit.
5. **No bounded async concurrency** at scale.
6. **robots.txt never actually parsed** (only mentioned in comments); no user-agent rotation.
7. **No AI-powered scraping exposure** — LLM extraction, crawl4ai/Firecrawl, self-healing selectors.

Explicitly out of scope this week (awareness level only): Scrapy mastery, CAPTCHA/anti-bot
deep-dives (you already have the Apify answer), dashboards.

---

## Library picks (one per job — decisions already made, don't re-litigate)

| Job | Pick | Why |
|---|---|---|
| Env/deps | **uv** | Fast, lockfile, `uv run pytest` in CI |
| HTTP | **httpx** | Sync + async in one API, first-class timeouts; familiar if you know requests |
| Retry/backoff | **tenacity** | Declarative `@retry` with exponential jitter; industry standard |
| Rate limiting | **aiolimiter** | `AsyncLimiter(10, 1)` token bucket; an explicit per-host policy, not a sleep |
| robots.txt | **protego** | The parser Scrapy uses; handles wildcards and `Crawl-delay` correctly |
| Parsing | **BeautifulSoup + lxml** | You already know it; spend the learning budget elsewhere |
| Validation | **pydantic v2** | Schema-as-code at the extraction boundary; the same schema drives LLM extraction |
| Logging | **structlog** | Bound context (`log.bind(url=...)`), JSON output for prod, pretty console for dev |
| Tests | **pytest + respx + syrupy + pytest-asyncio** | Transport-level fault injection + snapshot tests |
| Browser | **Playwright (async)** | Already strong — reused as a pluggable fetcher, not relearned |
| LLM extraction | **anthropic SDK** | `client.messages.parse(output_format=PydanticModel)` returns a validated instance |
| AI crawling | **crawl4ai** | Local and free; Firecrawl is the hosted/paid equivalent (awareness) |
| Lint | **ruff** | Lint + format in one tool, seconds in CI |

---

## Day 1 — Resilient HTTP: retries, backoff, rate limiting, robots.txt

**Learn (~1h):**
- Which failures are retryable (429, 5xx, timeouts) vs not (404, 403 — retrying won't help).
- Exponential backoff **with jitter** and why jitter matters (thundering herd).
- Honoring the `Retry-After` header when a server sends it.
- Token-bucket rate limiting vs fixed `sleep` — a per-host *policy*, not a pause.
- robots.txt as a parsed, enforced contract — not a comment in your code.

**Build (~2.5h):**
- `src/scrapekit/http/client.py`: an httpx `AsyncClient` wrapper with tenacity retry
  (exponential jitter, max 5 attempts, honors `Retry-After`), per-host `AsyncLimiter`,
  explicit timeouts.
- `src/scrapekit/http/robots.py`: fetch + parse robots.txt with protego; refuse disallowed
  URLs; feed `Crawl-delay` into the limiter.
- `src/scrapekit/http/headers.py`: small user-agent pool with rotation + honest default headers.
- Smoke-test manually against `https://httpbin.org/status/429`, `/status/500`, `/delay/3`
  and watch the backoff happen in real time.

**Done when:** hitting a 500 endpoint visibly retries with growing delays and gives up cleanly;
a robots-disallowed path raises a clear error; committed.

---

## Day 2 — Validation + structured logging + first end-to-end pipeline

**Learn (~1h):**
- Pydantic v2: field constraints, validators, `model_validate`. Key idea: validate at the
  extraction boundary so bad data fails **loudly at the edge**, not silently in the CSV.
- structlog: processors, bound loggers, JSON vs console rendering.

**Build (~2.5h):**
- `src/scrapekit/logging.py`: structlog setup — console renderer for dev, JSON flag for prod;
  every stage logs with bound context (`target`, `url`, `attempt`).
- `src/scrapekit/models/quote.py` + `models/book.py`: schemas with real constraints
  (price ≥ 0, rating 1–5, non-empty title, `HttpUrl` fields).
- `src/scrapekit/extractors/base.py` + `extractors/css.py`: an `Extractor` protocol; the CSS
  extractor returns validated model instances and **collects** per-item `ValidationError`s as
  structured log events + counters (upgrade of your per-item try/except pattern).
- `src/scrapekit/storage.py`: JSONL writer with SHA-256 content-hash dedup (port your
  existing pattern from web-scraping-practice-2), plus CSV export.
- `src/scrapekit/pipeline.py` v1: sequential fetch → extract → validate → store for one page
  of quotes.toscrape.com. Wire up `cli.py` (typer).

**Done when:** `uv run scrapekit run quotes` produces validated JSONL, structured logs per
stage, and valid/invalid/duplicate counts; a deliberately broken record is caught by
validation; committed.

---

## Day 3 — Testing scrapers like a test automation engineer ⭐ (the differentiator)

**Learn (~45m):** the scraper testing pyramid:
1. **Parser unit tests against frozen HTML fixtures** — fast, deterministic, zero network.
2. **HTTP-behavior tests with respx** — mock at the transport layer, inject 429/500/timeout
   sequences, assert retry and limiter behavior deterministically.
3. **Snapshot tests (syrupy)** — fixture HTML in, full extracted dataset snapshotted; parser
   changes get reviewed as diffs, like UI snapshot testing.
4. **Contract tests** — pydantic schemas reject malformed payloads.

Presentation line: *"a scraper without tests fails silently in production; mine fails in CI."*

**Build (~3h):**
- Curl 3–4 real pages into `tests/fixtures/html/` (fixtures = frozen contract with the site).
- `tests/test_parsers_snapshot.py`: CSS extractor over fixtures → syrupy snapshots.
- `tests/test_http_retry.py`: respx transport returning `[429, 429, 200]` → assert 3 attempts
  then success; `[500 × 6]` → assert it gives up after max attempts; assert limiter spacing.
- `tests/test_models_contract.py`: parametrized bad payloads (negative price, empty title,
  junk URL) all rejected.
- `tests/test_pipeline_e2e.py`: full pipeline against a mocked transport serving fixture
  HTML — no network, runs in <2s.
- `.github/workflows/ci.yml`: `uv sync` → `ruff check` → `pytest`. Push, watch it go green.

**Done when:** ~15+ tests green locally and in GitHub Actions with zero network access; committed.

---

## Day 4 — Async concurrency at scale + Playwright fetcher + drift monitoring

**Learn (~45m):**
- `asyncio.gather` with a `Semaphore` vs unbounded fan-out. The semaphore is *your* resource
  control; the rate limiter is *their* politeness policy — they compose.
- Selector drift: the #1 silent failure mode of production scrapers. Detect it by monitoring
  extraction rates, not by hoping.

**Build (~3h):**
- `pipeline.py` v2: async crawl of **all 50 pages / 1000 books** of books.toscrape.com —
  semaphore of 10, shared limiter, per-item error isolation. Under a minute, still polite.
- `src/scrapekit/fetchers/playwright_fetcher.py`: implement the `Fetcher` protocol with your
  existing Playwright skills; demo on `quotes.toscrape.com/js/` — same site JS-rendered.
  The A/B: httpx fetcher fails there, Playwright succeeds, **extractor and schema unchanged**.
- `src/scrapekit/monitoring.py`: a `RunReport` — pages fetched, records
  extracted/valid/invalid/duped, retry counts, duration, extraction-rate-per-page; **drift
  alert** when extraction rate drops below threshold; written to `reports/` as JSON.
- `tests/test_drift_detection.py`: mutate a fixture's class names → assert the alert fires.

**Done when:** 1000-book crawl completes with a clean run report; both fetchers share one
schema; drift test green; committed.

---

## Day 5 — LLM-powered extraction + self-healing fallback

**Learn (~1h):**
- Schema-guided extraction: pass the **same pydantic model** you validate with to
  `client.messages.parse(..., output_format=BookList)` — you get a validated instance back,
  no hand-rolled JSON prompting.
- Preprocess first: strip HTML to text/markdown — tokens are money.
- Cost model: `client.messages.count_tokens()` before the call; keep the model id in
  `config.py` so cost/quality is a config decision (a small model for volume, a big one for
  messy pages).

**Build (~2.5h):**
- `src/scrapekit/extractors/llm.py`: same `Extractor` protocol — html → trimmed text →
  `messages.parse` with the target's schema. Include: token pre-count + cost estimate logged
  per call, **hard budget cap** from `config.py` (abort when exceeded — your
  apify-watch-stores pattern), response caching keyed by content hash (never pay twice).
- The A/B demo: `scrapekit run books --extractor css` vs `--extractor llm` on the same 2–3
  pages; run report compares records, duration, and cost (~$0 vs ~cents).
- **Self-healing fallback** in `pipeline.py`: when CSS extraction yield drops below the drift
  threshold, automatically retry the page through the LLM extractor and flag it in the
  report. This ties Days 1–5 together and is the single best demo moment: *break the
  selectors live, the pipeline heals itself, the report shows what happened and what it cost.*
- Tests: inject the Anthropic client as a dependency → mock it in tests; one live smoke test
  marked `@pytest.mark.live` (excluded from CI).

**Done when:** both extractors produce schema-identical output; costs logged and capped;
self-healing demonstrated by deliberately breaking a selector; committed.

---

## Day 6 — crawl4ai spike + agentic awareness + decision framework

**Learn (~1.5h):**
- crawl4ai: browser-based crawling that emits LLM-ready markdown, built-in CSS/LLM extraction
  strategies, caching. (`uv add crawl4ai` today — it's heavy, so it's not in the base deps.)
- Where Firecrawl sits: same category, hosted/paid, API-first.
- "Agentic scraping" = an LLM deciding **navigation** (which links, when to paginate, how to
  recover), not just extraction — and why it's the expensive last resort.
- Awareness only: Scrapy (its middlewares/pipelines map to what you hand-built this week) and
  the anti-bot landscape (you already have the Apify answer).

**Build (~2h):**
- `notes/crawl4ai_spike.py` — a documented spike, not framework code: crawl a tolerant
  real-world target (Hacker News front page or an openlibrary.org subject page) → markdown →
  feed through your own `extractors/llm.py`. Write the effort-vs-hand-rolled verdict in `notes/`.
- `targets/hn.py`: news.ycombinator.com through the **main pipeline** — its robots.txt has a
  30s crawl-delay; proof your politeness machinery reads real-world directives.
- Draft the decision framework table into `README.md` (skeleton is already there).

**Done when:** spike runs and is written up with a when-to-use verdict; HN target runs
politely through the pipeline; framework table drafted; committed.

---

## Day 7 — Polish, demo script, presentation prep

**Build/write (~3h):**
- README: architecture diagram (ASCII is fine), quickstart, decision framework with **real
  cost numbers from your run reports**.
- The 10-minute demo script — rehearse it twice:
  1. `pytest` — green suite, zero network (30s). *Lead with tests; it's your differentiator.*
  2. `scrapekit run books --extractor css` — 1000 records + run report (2 min).
  3. Swap fetcher to Playwright on the JS quotes site — same schema (2 min).
  4. Break a selector live → drift alert → self-healing LLM fallback → cost line (3 min).
  5. CI run + repo tour (2 min).
- Tag `v1.0`; verify from a fresh clone: `uv sync && pytest && uv run scrapekit run quotes`.
- Buffer: anything that slipped earlier in the week lands here.

**Done when:** fresh-clone quickstart works; demo rehearsed end-to-end; tagged and pushed.

---

## Presentation talking points

### Decision framework — climb from the cheapest reliable rung

| Rung | When | Cost/1k pages | You have it from |
|---|---|---|---|
| Official API | Exists and covers the data | ~free | Always check first |
| httpx + CSS | Static HTML, stable structure | ~$0 | Days 1–4 |
| Playwright + CSS | JS rendering, infinite scroll | ~$0 + compute | Prior skill + Day 4 |
| LLM extraction | Messy / inconsistent / changing markup | ~$1–10 (measure Day 5) | Day 5 |
| crawl4ai / Firecrawl | Many unknown sites → LLM-ready content fast | $0 local / SaaS | Day 6 |
| Apify managed actors | Serious anti-bot (Instagram, Maps, marketplaces) | per-result pricing | Prior projects |
| Agentic scraping | Navigation itself needs judgment | highest, variable | Awareness |

### Ethics / legal / cost awareness
- robots.txt parsed programmatically (protego) and **enforced**, not just mentioned.
- Rate limiting is respect for the target's infrastructure, not an optimization.
- Review ToS before real targets; extra caution with personal data (GDPR-adjacent).
- Prefer official APIs when they exist; an identifiable user agent is an honesty option.
- LLM costs are metered, capped, and cached — show real numbers from run reports.

### Build vs buy
- **Build** the pipeline: validation, tests, and monitoring are target-agnostic — that's the moat.
- **Buy** the anti-bot layer (Apify): fighting CAPTCHAs in-house is a losing cost curve.

## Safe practice targets

| Target | Purpose |
|---|---|
| books.toscrape.com | Pagination, 1000 records, concurrency — built for practice |
| quotes.toscrape.com + `/js/` | Same data static vs JS-rendered — the fetcher A/B |
| httpbin.org (`/status/429`, `/status/500`, `/delay/N`) | Fault injection for retry/backoff |
| scrapethissite.com | Extra sandbox pages (forms, AJAX) if time allows |
| news.ycombinator.com | Real-world tolerant target; honor its 30s crawl-delay |
| openlibrary.org | Real-world, open-data, API-friendly — crawl4ai spike target |

## Leveraging your test-automation background (weave into every conversation)

1. **Contract tests** — pydantic schemas are executable contracts; malformed site data fails in CI, not in the downstream spreadsheet.
2. **Fixture/snapshot testing** — frozen HTML + syrupy means parser changes are reviewed as diffs, exactly like UI snapshot testing.
3. **Fault injection** — respx-mocked 429/500/timeout sequences test the retry machinery deterministically; most scraper authors never test failure paths at all.
4. **Drift monitoring = production assertions** — extraction-rate thresholds run on every crawl; the self-healing fallback is the recovery handler.
5. **CI-ready by construction** — dependency-injected fetchers/extractors/clients make everything testable without network.
