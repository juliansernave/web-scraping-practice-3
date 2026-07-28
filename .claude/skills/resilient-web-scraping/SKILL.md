---
name: resilient-web-scraping
description: Playbook for designing and building production-grade, resilient, testable web scrapers and crawlers — covers choosing the right extraction technique (CSS selectors vs headless browser vs LLM vs managed scraping service vs agentic), structuring a scraper as a fetch-extract-validate-dedupe-store pipeline with swappable pieces, handling rate limits/retries/robots.txt/pagination/selector drift, and writing scraper tests that never touch the network. Use this whenever the user wants to build, design, harden, or review a web scraper or data-extraction pipeline — even if they just say "scrape this site," "extract data from these pages," "this scraper keeps breaking," or "how do I handle rate limiting/pagination/blocked requests" — not only when they explicitly ask for "resilient" or "production" scraping. Prefer this over writing an ad hoc requests+BeautifulSoup script from scratch.
---

# Resilient Scraping

A playbook for building scrapers that keep working after you ship them — not just
happy-path scripts. It captures decisions and patterns distilled from a real 7-day
scraper-framework build, not a specific codebase to copy. Adapt everything here to
whatever language/stack the user is already in.

The core idea: **a scraper fails in production long before it fails in your test run.**
Sites rename CSS classes, rate-limit you, serve JS-rendered content, or return malformed
HTML on page 47 of 1000. A scraper that doesn't plan for this "succeeds" while silently
collecting garbage or nothing at all. Everything below exists to make failures loud
instead of silent.

## Step 1 — Climb the decision ladder before writing code

Don't default to "write a scraper." Pick the *cheapest reliable* technique for the job —
read `references/decision-ladder.md` for the full table with cost/effort tradeoffs and
concrete "use when" signals. In short, in order of preference:

1. **Official API** — if it exists and covers the data, nothing below this matters.
2. **HTTP client + CSS/XPath selectors** (httpx/requests + BeautifulSoup/lxml, etc.) —
   static HTML, stable markup. The default starting point.
3. **Headless browser + selectors** (Playwright/Puppeteer) — only when content requires
   JS execution (infinite scroll, client-rendered SPAs). Costs more compute and time;
   don't reach for it just because a site "feels modern."
4. **LLM-guided extraction** — messy, inconsistent, or frequently-changing markup where
   selectors would be brittle. Reads by meaning, not by CSS class. Costs real money per
   page — needs a budget cap (Pattern 6 below).
5. **Managed scraping service** (crawl4ai, Firecrawl, Apify, etc.) — many unknown sites,
   or a target with serious anti-bot defenses. Trades control/cost for not reinventing
   fetch infrastructure.
6. **Agentic scraping** (LLM drives navigation, not just extraction) — only when
   *navigating* the site itself requires judgment calls, not just reading the result.
   Highest cost and least predictable; reach for this last.

Ask which rung fits before writing anything. If the user hasn't said, a quick check of
the target site (view source, does content appear without JS?) usually answers it in
under a minute.

## Step 2 — Structure the scraper as a pipeline, not a script

Regardless of which rungs are chosen, structure the scraper as discrete stages:

```
fetch → extract → validate → dedupe → store → report
```

Each stage should be swappable independently. Concretely:

- **Fetch** and **extract** are the two seams that vary (static HTTP vs headless browser;
  CSS vs LLM). Define each as a small interface/protocol so the rest of the pipeline
  doesn't care which implementation is plugged in. See Pattern 2 below and
  `assets/protocols.py`.
- **Validate** happens at exactly one boundary — inside the extraction step, against a
  schema. Untrusted site data gets validated once; everything downstream trusts it.
- **Dedupe** and **store** are generic — they don't know or care what site produced the
  data.
- **Report** — every run should produce a small summary (pages fetched/failed, records
  valid/invalid, retries, duration). This is what makes silent failures visible; see
  Pattern 5.

This shape is *why* the scraper is testable: each stage can be exercised in isolation
with fake/injected dependencies, with zero network calls. See `references/testing-pyramid.md`.

## Step 3 — Apply these patterns

Read `references/patterns.md` for the full rationale on each; short version:

1. **Config-as-data per target.** A new site should be a small config value (base URL,
   schema, selector, a `parse_item`-style function) registered in a lookup — never a
   change to the shared pipeline code. If adding site #2 means editing the fetch/extract
   engine, the abstraction is wrong. See `assets/target_registry.py`.

2. **Two swappable seams via dependency injection.** Define a `Fetcher`-like interface
   (`fetch(url) -> raw result`) and an `Extractor`-like interface
   (`extract(raw) -> validated records + errors`). The pipeline depends only on these
   interfaces, never on a concrete HTTP library or LLM SDK. This is what lets you swap
   static-HTTP for headless-browser, or CSS for LLM, without touching orchestration code
   — and what lets tests inject fakes. See `assets/protocols.py`.

3. **Validate at one boundary; collect errors, don't raise.** One malformed record on
   page 500 must not abort a 1000-page crawl. Wrap per-item validation in a
   try/collect, not a crawl-wide try/except — accumulate failures as structured data
   (index, reason, raw payload) so they're visible in the run report, not swallowed by a
   bare `except: pass`.

4. **Distinguish "our" throttles from "their" throttles — and only retry transient
   failures.** A concurrency cap (semaphore/worker pool) is *your* resource limit; a
   rate limiter is *their* politeness policy (and should widen to honor a
   `Crawl-delay` from robots.txt if the site specifies one, since that overrides your
   default rate). Retries with exponential backoff + jitter are *your* recovery from
   transient failure — retry only 429/5xx/timeouts, and honor a `Retry-After` header
   when present. Never retry 404/403/401 — retrying a permanent client error just wastes
   time and looks more like abuse. See `assets/resilient_client.py`.

5. **Treat extraction rate as a production assertion.** Track `valid / total_matched`
   per run. A sudden drop to near-zero is the #1 silent failure mode — a renamed CSS
   class matches nothing, or matches items that no longer parse — and the crawl still
   exits 0 having collected garbage. Alert (log/flag/fail the run) when the rate drops
   below a threshold (e.g. 50%) instead of only checking record *count*, which doesn't
   catch a page that "succeeded" at zero.

6. **If using LLM extraction: pre-flight budget cap + content-hash cache.** Count tokens
   and estimate cost *before* the call, and hard-abort before spending past a configured
   budget — never after. Cache responses by a hash of the input content so an identical
   page (or a retried run) never pays twice. Validate the LLM's output against the exact
   same schema the deterministic extractor uses, so extractors are interchangeable.
   Optionally: when the extraction rate for a page drops below threshold (Pattern 5),
   treat that as "selectors likely drifted" and re-extract just that page via the LLM
   fallback — recovering automatically instead of failing the whole crawl.

## Step 4 — Test the failure paths, not just the happy path

This is the single most differentiating thing a scraper can have, and the thing almost
never done in practice-project scrapers. Read `references/testing-pyramid.md` for the
full methodology; the short version — all of this runs with **zero network access**:

- **Frozen HTML fixtures** — save real page HTML once, treat it as a contract test.
  Re-running extraction against the frozen fixture catches parser regressions instantly.
- **HTTP fault injection** — mock the transport layer (not the whole client) to script
  failure sequences (`[429, 429, 200]`, `[500]*6`, a connection error, a `Retry-After`
  header) and assert the retry/backoff/rate-limit logic deterministically.
- **Schema contract tests** — parametrize known-bad payloads (missing field, negative
  price, malformed URL, unexpected extra field) and assert they're rejected.
- **Drift tests** — mutate a fixture (rename a class the item selector matches, or a
  class a field parser reads) and assert the extraction-rate alert fires.
- **Pipeline end-to-end, without network** — inject a fake fetcher that returns fixture
  HTML and run the real pipeline through it, asserting on the final report.
- **Keep any real-network/live-API test isolated** behind a marker/tag excluded from CI
  by default — useful for manual sanity checks, but it shouldn't gate every run.

Templates for all of this are in `assets/test_harness_example.py` — adapt the framework
specifics (pytest/respx assumed) to whatever the project already uses.

## Ethics and politeness — non-negotiable, not optional

- **Parse and enforce robots.txt.** Missing/4xx robots.txt conventionally means "allowed"
  (RFC 9309); a 5xx or fetch failure should be treated conservatively as "disallowed"
  until it recovers — don't assume permission when you couldn't check.
- **Rate limits are a policy per host, not an afterthought** — bake them into the fetch
  layer, not into ad hoc `sleep()` calls scattered through target-specific code.
- **Honest headers, polite variety — not evasion.** Rotating a small pool of real
  browser user-agent strings is reasonable (some sites vary markup/limits by UA);
  spoofing headers to defeat detection or bypass an anti-bot wall is out of scope for
  this playbook. If a site actively blocks scraping, that's a signal to move up the
  decision ladder to an official API or a managed service with its own permissioning —
  not to escalate evasion.

## Reference files

- `references/decision-ladder.md` — full table: each rung, when to use it, rough cost,
  and the reasoning behind the ordering.
- `references/patterns.md` — deeper rationale for the six patterns above, with the
  "why" behind each one spelled out.
- `references/testing-pyramid.md` — the full network-free testing methodology with
  concrete test-case shapes for each layer.
- `assets/resilient_client.py` — a genericized resilient HTTP client: retry with
  jittered backoff (transient statuses only), per-host rate limiting, robots.txt
  enforcement, honest header rotation. Adapt the HTTP library to the project's stack.
- `assets/protocols.py` — the `Fetcher`/`Extractor` interface shapes plus the small
  result dataclasses (`FetchResult`, `ExtractionResult`, `ItemError`) that make the
  pipeline swappable and testable.
- `assets/target_registry.py` — the config-as-data pattern for registering a new
  scrape target without touching pipeline code.
- `assets/test_harness_example.py` — worked examples of fault-injection, contract, and
  drift tests (pytest + respx), to adapt to the project's actual test framework.
