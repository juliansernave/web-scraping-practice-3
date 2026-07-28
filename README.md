# scrapekit

A target-agnostic, production-grade web scraping framework. Point it at a site: pick a
fetcher (httpx for static HTML, Playwright for JS-rendered pages), pick an extraction
strategy (CSS selectors for stable markup, LLM for messy or changing markup), define a
Pydantic schema — and get validated, deduplicated, drift-monitored output, backed by a
test suite that runs with zero network access.

> *"Most scraping PoCs demo a happy path. As a test automation engineer, I built the
> unhappy paths first."*

Built over a 7-day upskilling sprint — see [PLAN.md](PLAN.md) for the roadmap.

## Quickstart

```bash
uv sync
uv run pytest                       # green, no network needed
uv run scrapekit run quotes         # first pipeline (Day 2)
```

Copy `.env.example` to `.env` and add your `ANTHROPIC_API_KEY` for LLM extraction (Day 5).

## Architecture

```
targets/*.py                  the only per-site code: url + pydantic model + item_selector
     |                        + parse_item + dedup_fields (+ page_urls, requests_per_second)
     v
cli.py                        scrapekit run <target> [--fetcher] [--extractor] [--heal]
     v
Fetcher (protocol)            httpx_fetcher.py: tenacity retry+backoff, per-host aiolimiter
 httpx | playwright                widened by robots.txt Crawl-delay, protego enforcement
     |                        playwright_fetcher.py: same protocol, JS-rendered pages
     v
Extractor (protocol)          css.py: BeautifulSoup selectors -> raw dict
 css | llm                    llm.py: text -> Claude, schema-guided parse, token pre-count,
     |                              USD budget cap, content-hash cache (never pay twice)
     v
pydantic model (models/*.py)  the contract: bad records collected as ItemError, not raised
     |
     +-- yield below drift threshold? --> llm_fallback re-extracts the page (self-heal)
     v
storage.py                    sha-256 content-hash dedupe -> JSONL (+ CSV export)
     v
monitoring.py: RunReport      pages fetched/failed, valid/invalid/duped, retries, duration,
                               extraction_rate, drift_alert, healed_pages, llm_cost_usd
```

## Decision framework: which tool for which job

Climb from the cheapest reliable rung. "Measured" rows are real numbers from `reports/`
(this repo's own runs); the rest is awareness from prior projects, not yet built here.

| Rung | When | Cost/1k pages | Measured |
|---|---|---|---|
| Official API | Exists and covers the data | ~free | — |
| httpx + CSS | Static HTML, stable structure | $0 | ✅ 1000 books in 6.9s (`scrapekit run books`) |
| Playwright + CSS | JS rendering, infinite scroll | $0 + compute | ✅ 10 quotes/js in 2.3s incl. ~2s browser launch |
| LLM extraction | Messy / inconsistent / changing markup | ~$49–110 (`claude-opus-4-8`) | ✅ books: $0.049/pg; HN: $0.11/pg — a cheaper model would cut this proportionally |
| crawl4ai / Firecrawl | Many unknown sites → LLM-ready content fast | $0 local fetch + whatever extractor you pair it with | ✅ HN fetch: 1.5s, $0 (extraction cost = the LLM row above) |
| Apify managed actors | Serious anti-bot targets (e.g. openlibrary's bot wall, hit during the Day-6 spike) | per-result pricing | — |
| Agentic scraping | Navigation itself needs judgment, not just extraction | highest, variable | — |

## Ethics

robots.txt is parsed (protego) and enforced. Rate limits are a per-host policy, not an
afterthought. Practice targets are sites that permit scraping (books.toscrape.com,
quotes.toscrape.com, httpbin.org, Hacker News with its 30s crawl-delay honored).
