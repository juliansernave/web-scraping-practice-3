# The six patterns, in depth

These are the load-bearing design decisions that separate a scraper that works once from
one that keeps working. Each includes the failure mode it prevents — that's the part
worth internalizing, not the specific code shape (which should adapt to whatever
language/stack the project uses).

## 1. Config-as-data per target

**The pattern:** A new site to scrape is a small, plain data value — base URL, the
validation schema, an item selector, a small function that turns one matched element
into a raw field dict, maybe a per-site rate override. These configs live in one place
(a `targets/` folder, a registry dict, whatever fits the project) and get looked up by
name. The shared fetch/extract/pipeline code never branches on which site it's running.

**Why it matters:** the alternative — copy-pasting a scraper script per site, or adding
`if site == "foo"` branches into shared code — means every new site is a new
opportunity to introduce a bug in code that other sites also depend on. Config-as-data
means adding site #10 can't break sites #1-9, because it never touches their code path.

**The one non-obvious wrinkle:** don't make the per-site config a flat
`{field_name: selector}` map if pages have any real-world messiness (a rating encoded
in a CSS class name, a price with a currency symbol, a relative URL needing resolution
against the page's base). A declarative map can't express "read this attribute, strip
this prefix, convert to Decimal." A small callable can, while the generic
select-and-validate loop around it stays exactly the same for every target. See
`assets/target_registry.py`.

## 2. Two swappable seams via dependency injection

**The pattern:** Define two narrow interfaces — something like a `Fetcher`
(`fetch(url) -> raw_result`) and an `Extractor` (`extract(raw_result) -> validated
records + errors`). Everything that orchestrates the crawl (the pipeline) takes these as
parameters/dependencies; it never imports or knows about a specific HTTP library,
browser automation tool, or LLM SDK directly.

**Why it matters:** three things fall out of this for free, which is why it's worth the
small amount of upfront structure:
- **You can swap fetch strategy without touching anything else.** Static HTTP today,
  headless browser tomorrow (e.g. because a target switched to client-side rendering) —
  same extractor, same schema, same pipeline, one line changed at the call site.
- **You can swap extraction strategy without touching fetch.** CSS selectors today, LLM
  extraction on a page that's drifted — same interface, same downstream validation.
- **Tests run with zero network.** Inject a fake fetcher that returns fixed HTML from a
  file, and the entire pipeline — extraction, validation, dedup, storage, reporting —
  runs deterministically in milliseconds. This is what makes the testing pyramid in
  `references/testing-pyramid.md` possible at all.

The result types matter too: keep the *fetch* output (raw HTML/bytes + status + final
URL) as a plain, un-validated data shape — it's your own transport metadata, not
untrusted input. Validation belongs entirely inside the *extraction* step (Pattern 3),
which is the actual boundary where untrusted site data enters the system.

## 3. Validate at one boundary; collect errors, don't raise

**The pattern:** Inside the extraction step, wrap each individual record's parsing +
validation in a per-item try/except that *collects* failures as structured data (which
item, why it failed, what the raw payload looked like) rather than letting an exception
propagate up and abort the whole page or the whole crawl.

**Why it matters:** a scraper visiting hundreds or thousands of pages *will* hit
malformed markup somewhere — a missing field, a page mid-redesign, an ad slot that
matched the item selector by accident. If one bad record raises all the way up, either
the whole run crashes (bad) or someone wraps the *entire extraction loop* in a bare
`try/except: continue` (worse — now you're silently losing records with no visibility
into how many or why). Collecting structured per-item errors gives you both survival
*and* visibility — the run report can say "1000 pages, 3 items failed to parse, here's
why" instead of either crashing or silently shipping incomplete data.

**Validate exactly once, at this boundary.** Everything downstream (dedup, storage,
reporting) should be able to trust that a record reaching it is well-formed — don't
re-validate at every stage, and don't skip validation because "the selector already
found the right element." A found element and a *valid* record are different claims.

## 4. "Ours vs. theirs" throttling, and retry only what's retryable

**The pattern:** Keep two distinct throttles conceptually separate, because they answer
different questions:
- **A concurrency cap** (semaphore, worker pool, connection pool limit) is *your*
  resource control — it stops you from opening more sockets/threads than your own
  process can handle, regardless of what the target site allows.
- **A rate limiter** (token bucket, sliding window) is *their* politeness policy — a
  ceiling on requests/second to a given host, independent of how many workers you have
  ready to fire. If the site's robots.txt specifies a `Crawl-delay`, that should widen
  your default rate limiter for that host — the site's stated preference overrides your
  default.

Both can be active at once and they compose correctly: ten workers all holding a
concurrency slot still only emit requests at the rate limiter's pace. Conflating the two
(e.g. "just use one semaphore for everything") loses the distinction between "how much
can *I* handle" and "how much will *they* tolerate."

**On retries specifically:** only retry failures that are actually transient — HTTP 429
(rate limited), 5xx (server error), and connection-level failures like timeouts. Use
exponential backoff with jitter (not a fixed delay — fixed delays across many concurrent
workers cause a thundering-herd retry spike). If the server sends a `Retry-After`
header, honor it over your own backoff calculation — it's more precise. **Never retry
404, 403, or 401** — these are permanent client errors; retrying them wastes time, adds
load to a site that's already telling you no, and can look more like abusive traffic
than a bug.

## 5. Extraction rate as a production assertion

**The pattern:** For every page (and for the run as a whole), track
`valid_records / total_matched_items`. If this ratio drops below a threshold (a
reasonable default is 50%, tune per target), treat it as a signal that something
structural broke — not just "some bad data today."

**Why it matters — this is the #1 silent failure mode of scrapers in production.** A
site renames a CSS class. Your item selector now matches nothing. The crawl runs to
completion, exits with status 0, logs "0 records" or "500 pages fetched successfully,"
and unless someone is watching record *counts* closely, the failure goes unnoticed for
days. Worse: sometimes the item selector still matches (a container div didn't change)
but a *field* selector inside it broke — now every item is "found" but fails validation,
same silent-zero outcome. Tracking extraction rate catches both cases with one signal,
because both collapse the ratio toward zero.

Make this an active assertion, not passive logging: alert, flag the run, or (per Pattern
6) trigger an automatic fallback when the rate drops below threshold. A number sitting
unread in a log file that nobody greps is not a production assertion.

## 6. LLM extraction: pre-flight budget cap + content-hash cache

**The pattern (when LLM extraction is the right rung — see decision-ladder.md):**
- **Estimate cost before the call, not after.** Count/estimate input tokens, compute the
  worst-case cost (including max possible output), and check that against a configured
  budget *before* making the API call. If the estimate would exceed budget, abort before
  spending — a cap enforced only after the fact isn't really a cap.
- **Cache by content hash.** Hash the actual page content (not the URL — the same URL
  can serve changing content, and different URLs can coincidentally serve identical
  content) and cache the extraction result under that hash. A re-run over the same pages,
  or a retry after a partial failure, should never pay for the same extraction twice.
- **Validate against the same schema as the deterministic extractor.** If CSS extraction
  and LLM extraction both produce records validated against the identical schema, they
  become truly interchangeable — either one can serve as the primary or the fallback
  extractor without downstream code caring which one ran.
- **Optional but powerful: wire it to Pattern 5 as a self-healing fallback.** When a
  page's extraction rate drops below threshold (selectors likely drifted), automatically
  re-extract *that specific page* through the LLM extractor instead of failing it. This
  turns "a human has to notice the drift, investigate, and rewrite selectors" into "the
  crawl recovers automatically, and the human gets a report saying which pages needed
  healing and what it cost." Keep this behind the same budget cap so a bad drift event
  can't blow the budget trying to heal every page.
