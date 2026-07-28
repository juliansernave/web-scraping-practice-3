# Decision ladder: which extraction technique for which job

The organizing idea: **climb from the cheapest, most reliable rung, and only move up when
the current rung genuinely can't do the job.** Each rung up trades away predictability,
speed, or cost-per-page for the ability to handle messier or more defended targets. Skipping
straight to "LLM extraction" or "headless browser" because it feels more powerful usually
just adds latency, cost, and new failure modes for no benefit.

| Rung | When to use it | Rough cost | What you give up moving here |
|---|---|---|---|
| **Official API** | It exists and covers the data you need | Near-free (rate limits aside) | Nothing — always prefer this if available |
| **HTTP client + CSS/XPath selectors** | Static HTML, structure is stable across pages/runs | $0, fast (seconds for hundreds of pages) | Can't execute JS; breaks silently if the site redesigns markup |
| **Headless browser + selectors** | Content requires JS execution — infinite scroll, client-rendered SPA, interaction-gated content | $0 + real compute/time (browser launch + render, ~10-50x slower per page than plain HTTP) | Much slower, heavier infra, still brittle to markup changes like the row above |
| **LLM-guided extraction** | Markup is messy, inconsistent across pages, or changes often enough that selector maintenance is the bottleneck | Real money per page (tokens) — can be $0.03–0.15/page depending on model and page size | Slower per page than selectors, non-deterministic in edge cases, needs a budget cap and cache (see patterns.md) |
| **Managed scraping service** (crawl4ai, Firecrawl, Apify, etc.) | Many unknown/changing sites where hand-rolling fetch infra isn't worth it, or a target with anti-bot defenses beyond what honest headers + rate limiting can pass | Per-request/managed pricing; $0 local compute if self-hosted (e.g. crawl4ai) | Less control over exactly how fetching happens; may still need your own extraction step on top |
| **Agentic scraping** (LLM drives navigation decisions, not just extraction) | The *navigation itself* needs judgment — which link to follow, when a search needs refining, multi-step interaction with no fixed structure | Highest and most variable — multiple LLM calls per page, unpredictable path length | Least predictable cost and runtime; hardest to test deterministically; reach for this only when a fixed crawl plan genuinely can't be written |

## How to actually decide, fast

1. **Check for an API first.** Five minutes of searching for `<site> API` or checking
   their docs/footer saves the entire rest of this ladder.
2. **View source on a target page.** If the data you need is present in the raw HTML
   (view-source, not the rendered DOM), you're on rung 2 — no browser needed.
   If it's empty/placeholder until JS runs, you need rung 3.
3. **Look at 3-5 real pages before picking an extractor.** If the markup structure is
   consistent across them, CSS/XPath selectors (rung 2/3) will be fast and free. If
   the structure genuinely varies page-to-page (different templates, inconsistent
   nesting, frequently-redesigned layouts), that's the actual signal for LLM extraction
   — not "selectors seem hard to write."
4. **If the site is actively blocking you** (CAPTCHAs, aggressive rate limiting beyond
   normal politeness, IP bans), that's a signal to move to a managed service with its
   own permissioning infrastructure — not to escalate to browser fingerprint spoofing or
   header evasion. See the ethics section in SKILL.md.
5. **Only reach for agentic scraping when the crawl plan itself can't be fixed in
   advance** — e.g. "search for X, read the top 3 results, follow whichever link best
   matches Y." If you can write down the list of URLs to visit ahead of time, you don't
   need an agent to visit them.

## A note on "measured" vs. "awareness"

When advising a user, be honest about which of these you have concrete experience/data
for vs. which are general industry awareness. A decision ladder is most useful when it's
calibrated to reality — if you don't actually know the cost-per-page for a given
approach on their stack, say so rather than presenting a guessed number as measured fact.
