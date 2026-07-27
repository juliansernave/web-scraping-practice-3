# Day 2 — Learn: Validation + structured logging + first pipeline

Study notes for the Day 2 concepts, grounded in the code we built (`models/`, `extractors/`,
`storage.py`, `pipeline.py`, `logging.py`). Theory ↔ implementation.

**One-sentence synthesis (for the pitch):**
> *"The schema is the contract with the site: bad data fails **loudly at the extraction
> boundary**, as a counted event in the logs — not silently, three stages later, in the CSV."*

---

## 1. Pydantic v2 — validate at the boundary

Core insight: **there is exactly one place untrusted data enters the system** — the moment
site HTML becomes a typed record. Validate *there* and everything downstream is guaranteed
well-formed. Skip it and a `.get()` chain passes `None`/`""`/`-1` silently until it corrupts
the output.

- **Field constraints** are executable contract: `Field(min_length=1)`, `Field(ge=0)`,
  `Field(ge=1, le=5)`, `HttpUrl`. Verified rejections: empty title, negative price,
  rating=9, `"not-a-url"` (`models/book.py`).
- **`Decimal` for money**, never `float` — `price: Decimal = Field(ge=0)`. The target parser
  strips the `£`; the model enforces non-negative.
- **`model_validate(raw_dict)`** is the entry point the CSS extractor calls per item.

**Validator ordering gotcha (a real bug we hit and fixed):** a `mode="after"` validator runs
*after* `min_length`. Our `text` validator unwraps the site's curly quotes (`“…”`); with
`mode="after"`, a quote that was *only* `“”` passed `min_length=1` (raw length 2), then
unwrapped to `""` — an empty string stored as valid. Fix: unwrap in **`mode="before"`** so
the constraint validates the *cleaned* value.

```python
@field_validator("text", mode="before")   # BEFORE min_length, not after
@classmethod
def _unwrap_quotation_marks(cls, value):
    if isinstance(value, str):
        return value.strip().strip("“”‘’\"'").strip()
    return value
```

> **TAE angle:** a pydantic model is a **contract test that runs on every record in prod**,
> not just in CI. Malformed site data fails at the edge, exactly like an API schema assertion.

---

## 2. Collect errors, don't raise — one bad record ≠ dead crawl

The upgrade of the naive per-item `try/except` that swallows failures. The `CssExtractor`
loop catches **two** failure classes and records both instead of aborting:

```python
try:
    raw = self._parse_item(element, base_url)      # missing node -> AttributeError
    result.records.append(model.model_validate(raw))  # bad data  -> ValidationError
except ValidationError as exc:
    result.errors.append(ItemError(index, reason, raw)); log.warning("extract.invalid", ...)
except Exception as exc:
    result.errors.append(ItemError(index, repr(exc), raw)); log.warning("extract.parse_error", ...)
```

`ExtractionResult` carries `records` + `errors`; `valid_count`/`invalid_count`/`total` feed
the run report. Verified on a broken fixture: empty text → `extract.invalid` (validation),
missing `<small class="author">` → `extract.parse_error` (parse), one valid survivor. **Every
failure is counted and locatable by index — silent data loss becomes an inspectable event.**

---

## 3. structlog — logs as events with data, not strings

`print("got 48 quotes")` is a dead end. `log.info("extract.done", valid=48, invalid=2)` is:
- a **stable event key** (`extract.done`) you can grep/alert on, and
- **structured fields** rendered for two audiences by **one flag** (`configure_logging`):
  - dev → `ConsoleRenderer` (colorized `key=value`),
  - prod → `JSONRenderer` (one JSON object per line → ship to a log store).

Same call sites, zero changes between environments. `bind()` attaches context once
(`log.bind(target=..., url=...)`) and every subsequent event inherits it — that's how the
pipeline stamps `target`/`url` on `fetch.done`, `extract.done`, and `run.done` without
repeating them.

> **Why it matters for the PoC:** the `run.done` JSON line *is* the run report —
> `{"valid": 10, "invalid": 0, "duplicates": 0, ...}`. Day 4's drift monitor just reads these.

---

## 4. Content-hash dedup — identity by value, not position

`JsonlStore` hashes each record's **canonical JSON** (SHA-256, `sort_keys=True`) so field
ordering never affects identity. `dedup_fields=("text",)` narrows identity to a stable subset
(a quote is its text; its tag ordering is noise). The seen-set is **seeded from the existing
file on open**, so re-running a crawl skips what's already captured — **idempotent crawls**
(verified: second run → 10 duplicates, 0 stored). JSONL over a JSON array because every line
is independently valid: a crash mid-crawl leaves a readable file.

---

## 5. The seams — why everything is injectable

`pipeline.run(target, *, fetcher, extractor, store)` depends on **protocols**, not concretes:
- `Fetcher` — httpx today, Playwright Day 4, a fake serving fixture HTML in Day 3 tests.
- `Extractor` — CSS today, LLM Day 5. Same `ExtractionResult` contract.
- `Target` — pure config (`targets/`); a new site is a config file, never a pipeline change.

This is what makes Day 3 possible: inject a mock-transport fetcher and the **entire pipeline
runs with zero network**. Dependency injection isn't ceremony here — it's the precondition
for testability.

---

## Bug found this session (logged for honesty)

`Accept-Encoding: gzip, deflate, br` in Day 1's `headers.py` advertised **brotli**, which
httpx can't decode without the `brotli` package installed. quotes.toscrape.com sent brotli;
`.text` came back as binary garbage; `div.quote` matched 0 elements. Fix: **remove the
hardcoded `Accept-Encoding`** and let httpx set it from the decoders it actually has.
Lesson: *advertise only what you can decode.* Latent in Day 1 because we never parsed a body
until today.
