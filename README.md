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
fetch (httpx | playwright) → extract (css | llm) → validate (pydantic)
     → dedupe (sha-256) → store (jsonl/csv) → RunReport (drift alerts, costs)
```

<!-- TODO Day 7: replace with the full ASCII architecture diagram -->

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
