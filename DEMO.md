# scrapekit — 10-minute demo script

Rehearsed end-to-end on 2026-07-28 (numbers below are real, not estimates — re-run any
command to reproduce). Run everything from the repo root; `.env` needs `ANTHROPIC_API_KEY`
for steps 2 and 4.

> *"Most scraping PoCs demo a happy path. As a test automation engineer, I built the
> unhappy paths first."*

---

## 1. Tests — the differentiator (30s budget, actually ~1.5s)

```bash
uv run pytest -m "not live" -q
```

**Say:** "Before the scraper touches a single page, here's the test suite — the same one
that gates CI. 36 tests, zero network calls, in about a second and a half."

**Rehearsed output:** `36 passed, 1 deselected` (the one `@pytest.mark.live` LLM smoke test
is intentionally excluded — same as CI). Snapshot tests, respx-mocked retry/backoff
sequences, drift-detection tests that mutate fixture HTML, pydantic contract tests.

**Punchline:** "A scraper without tests fails silently in production. Mine fails in CI."

---

## 2. The scale run — 1000 records, one command (2 min)

```bash
uv run scrapekit run books --extractor css --report
```

**Say:** "50 pages, 1000 books, bounded concurrency, a shared per-host rate limiter, robots.txt
enforced — and a run report at the end, not just a CSV."

**Rehearsed output:** `books: pages=50/50 extracted=1000 valid=1000 invalid=0 stored=1000
duplicates=0 retries=0 rate=1.00 (5.5s)`. Open the written `reports/books_*.json` to show the
per-page detail if there's time.

---

## 3. Same schema, different fetcher — the JS A/B (2 min)

```bash
uv run scrapekit run quotes-js --fetcher httpx --report      # fails
uv run scrapekit run quotes-js --fetcher playwright --report # succeeds
```

**Say:** "This page renders its quotes with JavaScript. httpx never runs a script — watch."

**Rehearsed output:** httpx: `extracted=0 valid=0 rate=0.00 DRIFT!` (0.4s — it doesn't even
see the quotes, and the drift alert fires because it matched nothing). Playwright: `extracted=10
valid=10 rate=1.00 (2.5s)`.

**Punchline:** "Same target config, same schema, same extractor — the only thing that
changed is the fetcher. That's the whole point of the `Fetcher` protocol from Day 2."

---

## 4. Break it live, watch it heal (3 min)

```bash
uv run scrapekit run books --drift-demo --heal --max-pages 1 --report
```

**Say:** "`--drift-demo` mutates the item selector in memory right now — nothing on disk
changes, this is standing in for the site renaming a CSS class overnight."

**Rehearsed output:**
```
--drift-demo: item_selector 'article.product_pod' -> 'article.product_pod__DEMO_BROKEN'
...
books: pages=1/1 extracted=20 valid=20 invalid=0 stored=20 ... healed=1 cost=$0.0489 (16.4s)
```

**Say, while it's running:** "The CSS selector now matches nothing — extraction rate drops
to zero, which is exactly the drift alert from Day 4. Because `--heal` is on, the pipeline
re-extracts that page through the LLM extractor — same pydantic model, so the output is
identical either way — and the run report shows what it cost: 5 cents, once."

**Punchline:** "Selector drift is the #1 silent failure mode in scraping. Mine doesn't fail
silently — it fails loudly, then fixes itself, and tells you what that cost."

---

## 5. CI + repo tour (2 min)

```bash
cat .github/workflows/ci.yml
```

**Say:** "Every push runs exactly what we just ran by hand: `uv sync --frozen`, `ruff check`,
`ruff format --check`, `pytest -m "not live"`. No network access in CI — everything's mocked
at the transport layer or served from HTML fixtures."

**Repo tour, in order:** `PLAN.md` (the 7-day arc) → `src/scrapekit/http/` (Day 1: retry,
robots) → `models/` + `extractors/` (Day 2/5: the shared pydantic contract, CSS and LLM
both validate against it) → `tests/` (Day 3) → `pipeline.py` + `monitoring.py` (Day 4/5:
concurrency, drift, self-heal) → `targets/hn.py` + `notes/day6_learn.md` (Day 6: a real
site, and the crawl4ai spike) → `README.md`'s decision framework table (real cost numbers,
not guesses).

---

## Fresh-clone check (run once before presenting)

```bash
uv sync && uv run pytest && uv run scrapekit run quotes
```

If this doesn't work from a clean clone, nothing else in this script matters — verify it
first.
