# The network-free testing pyramid for scrapers

Most scraper "tests," if they exist at all, are a manual run against the live site
followed by eyeballing the output. That catches nothing about *failure* behavior — retry
logic, rate limiting, malformed pages, schema violations — because none of those are
easy to trigger on demand against a real site. The fix is to test each layer against
controlled, fake inputs instead of the live network. All five layers below run in
milliseconds with zero network access, which also means they're safe to run in CI on
every commit without hitting anyone's rate limits.

This only works because of Pattern 2 (dependency injection via `Fetcher`/`Extractor`
interfaces) — every layer below relies on being able to substitute a fake for the real
network-touching component.

## 1. Frozen HTML fixtures as a contract test

Save the real HTML of a representative page to a file, once. Point the extractor at that
frozen fixture instead of a live fetch. This does two things:
- **Catches parser regressions immediately** — if you change extraction logic, run it
  against the fixture and diff the output (a snapshot-testing library is convenient
  here, but a plain equality assertion works too).
- **Documents the actual contract with the site** — the fixture *is* what the site
  looked like when you wrote the parser. When extraction starts failing against the
  live site, diffing new live HTML against the frozen fixture usually shows exactly
  what changed.

Keep a "broken" fixture too — a deliberately malformed variant (missing field, empty
container) — and assert the extractor collects errors for it rather than crashing
(exercises Pattern 3).

## 2. HTTP fault injection at the transport layer

Don't mock your own HTTP client wrapper — mock the underlying transport (whatever your
HTTP library exposes for this; e.g. `respx` for `httpx`, `nock` for Node, `responses`
for `requests`) so the *real* retry/backoff/rate-limit code runs, just against scripted
responses instead of a real socket. Script sequences like:

- `[429, 429, 200]` — asserts the client retries transient failures and eventually
  succeeds, with the expected number of attempts.
- `[500, 500, 500, 500, 500]` (exceeding max retries) — asserts the client gives up
  after the configured attempt count and raises/surfaces the failure, rather than
  retrying forever.
- A single `404` — asserts the client does *not* retry (Pattern 4) and fails on the
  first attempt.
- A connection-level error (timeout, connection reset) — asserts it's treated as
  transient and retried, same as a 5xx.
- A `429` with a `Retry-After` header — asserts the client honors the header's delay
  rather than falling back to its own backoff calculation.
- Multiple rapid requests against a configured rate limit — asserts real wall-clock
  spacing between requests (use a lower-bound-only timing assertion; CI machines can be
  slower than expected but should never be *faster* than a real rate limit allows).

This is the layer real scraper projects skip almost universally, and it's the cheapest
one to write relative to the bugs it catches — retry-storm bugs and rate-limiter-off-by-one
bugs are otherwise nearly impossible to catch before production.

## 3. Schema contract tests

Your validation schema (pydantic, zod, JSON Schema, whatever the stack uses) is an
executable contract with the site. Test it directly, independent of any HTML parsing:
parametrize a list of known-bad payloads — missing required field, empty string where
content is required, out-of-range value, wrong type, an unexpected extra field if the
schema is strict about that — and assert each one is rejected. Also test that a known-good
payload is *accepted* and that any data-cleaning the schema does (stripping currency
symbols, normalizing whitespace, converting to a precise decimal type for money) actually
happens.

This test layer has nothing to do with fetching or parsing HTML — it's purely "does the
schema enforce what I think it enforces," and it's the fastest layer to write and run.

## 4. Drift tests

Take a healthy fixture and deliberately corrupt it in the two ways real sites actually
drift, then assert Pattern 5's extraction-rate signal fires:

- **Rename the item container's class/selector target** — the item selector now matches
  zero elements. Assert extraction rate drops to 0 and the drift alert fires.
- **Rename a field's class/selector inside an otherwise-intact item container** — items
  still match (the selector finds them), but the specific field parse fails for all of
  them. Assert items are found (`total_matched > 0`) but all fail validation
  (`valid == 0`), and the drift alert still fires.

These two cases fail *differently* under the hood (zero matches vs. all-matches-invalid)
but must trip the same alert — testing only one gives false confidence that drift
detection works when it's only covering half the actual failure modes.

## 5. Pipeline end-to-end, without network

Wire together a fake fetcher (returns fixed HTML regardless of URL — no browser, no
sockets) with the *real* extraction, validation, dedup, and storage code, and run the
actual top-level pipeline function through it. Assert on the final report: pages
fetched, records valid/invalid, duplicates skipped, stored count. This is the test that
proves all the pieces compose correctly together, not just in isolation — and it's cheap
to run because the fake fetcher makes it instant.

Also worth covering here: run the pipeline twice against the same fixture data and
assert the second run doesn't re-store already-seen records (dedup idempotency) — a
common real bug is a dedup key that isn't actually stable across runs.

## Keep exactly one test behind a "live" marker/tag

It's worth having *one* test that hits the real network or a real paid API (e.g. an LLM
extractor call), to sanity-check that your fakes still match reality. Mark it clearly
(a pytest marker, a tag, a separate test file/folder) and exclude it from the default
CI run — it should be something a developer runs manually before a release, not
something that gates every commit or burns API budget on every push.
