# Day 6 — Learn: crawl4ai spike, agentic awareness, decision framework

**One-sentence synthesis (for the pitch):**
> *"crawl4ai gets you clean markdown from an unknown site in one line; it doesn't get you
> robots.txt enforcement, per-host rate limits, retry/backoff, validation, dedup, drift
> monitoring, or a cost cap — that's the 5 days of scrapekit it doesn't replace."*

---

## 1. crawl4ai: what it actually is

A browser-based crawler (Playwright under the hood) whose output is LLM-ready markdown, plus
built-in CSS/LLM extraction strategies and its own content cache. `uv add --group spike
crawl4ai` — kept out of base deps (heavy: playwright, numpy, tokenizers, ~1GB of transitive
deps) because it's an awareness spike, not a framework dependency.

```python
async with AsyncWebCrawler() as crawler:
    result = await crawler.arun(url="https://news.ycombinator.com/")
    markdown = result.markdown.raw_markdown
```
One call replaces fetch + "strip HTML to text" (the first half of `extractors/llm.py`).

## 2. The spike, and a detour

Plan was `openlibrary.org/subjects/science_fiction` (a PLAN.md pick, and JS-hydrated — the
book listing isn't in the raw HTML `curl` returns, only after client-side rendering). crawl4ai
should handle that fine... except it didn't get the chance:

```
result.status_code == 303   # redirected to /verify_human
```

openlibrary's bot-detection wall, not a rendering problem. Solving that is explicitly out of
scope this week (PLAN.md: anti-bot deep-dives → the Apify answer, not a hand-rolled bypass),
so the spike target switched to **Hacker News** — reusing the exact schema `targets/hn.py`
already validates against, which turns this into a fair A/B: same data, two fetch+extract
paths, not two different sites.

**A real finding worth keeping**: "browser-based" doesn't mean "gets past anti-bot." crawl4ai
renders JS; it doesn't solve challenge walls. That's still Apify's job.

## 3. The A/B — same schema (`HnStory`), two paths

| Path | Fetch | Extract | Records | Duration | Cost |
|---|---|---|---|---|---|
| **A** (Days 1–5, `scrapekit run hn`) | httpx (robots-aware, rate-limited) | CSS selectors | 30/30 valid | 1.6s | $0 |
| **B** (this spike, `notes/crawl4ai_spike.py`) | crawl4ai (headless browser) | LLM (`extractors/llm.py`, `HnStory`) | 30/30 valid | 1.5s fetch + 30.6s LLM call | $0.1098 |

Both paths validate against the identical pydantic model — that's the point of the
`Extractor` protocol from Day 2, still paying off on Day 6. Feeding crawl4ai's markdown into
`LlmExtractor.extract()` unchanged works, but is slightly redundant: `LlmExtractor`'s own
`_html_to_text` (BeautifulSoup strip-to-text) is doing near-nothing to markdown that already
has no tags left — crawl4ai's markdown step and our own html-to-text step do the same job.

Real LLM cost, `claude-opus-4-8` list price: **~$0.05–0.11/page** depending on page density
(books.toscrape at ~20 records/page: $0.049/page, $49/1k pages; HN at 30 records/page:
$0.11/page, $110/1k pages — see `reports/`). That's meaningfully higher than the PLAN.md
draft guess of "$1–10/1k pages"; a cheaper model (Haiku-tier) would close most of that gap for
volume work, which is exactly why `config.py` keeps the model id swappable. Numbers below are
measured this way, not the plan's original estimate.

## 4. Agentic scraping — the distinction that matters

"Agentic" means **the LLM decides navigation** — which links to follow, when to paginate, how
to recover from a dead end — not just extraction. crawl4ai's own deep-crawl strategies (BFS/
DFS link-following) are *rule-based* navigation, not agentic; they decide "follow every link
matching X," not "an LLM read the page and chose." True agentic scraping puts a model in the
loop for every navigation decision — the most flexible approach and the most expensive, both
in tokens and in latency, which is why it's the last resort on the decision ladder, not the
default.

## 5. Awareness-only notes

- **Scrapy**: its middlewares (retry, robots, throttling) and pipelines (item processing,
  dedup, storage) map directly to what we hand-built this week — `http/client.py` +
  `http/robots.py` ≈ Scrapy's downloader middlewares; `storage.py` ≈ an item pipeline. Not
  relearning it; the concepts already transferred.
- **Anti-bot**: openlibrary's `/verify_human` wall (§2) is the live example — Apify's managed
  actors are still the answer for serious anti-bot targets, not a skill to build in-house.
