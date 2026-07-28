"""The two swappable seams (Pattern 2 in references/patterns.md).

Adapt this to your language/framework — the point isn't this exact Python shape, it's
the *idea*: the pipeline depends on these narrow interfaces, never on a concrete HTTP
library, browser automation tool, or LLM SDK. That's what lets you swap implementations
(static HTTP <-> headless browser, CSS selectors <-> LLM extraction) without touching
orchestration code, and what lets tests inject fakes with zero network access.

Genericized from a real scraper-framework build; strip/rename freely to fit your stack
(e.g. use a TypeScript interface, a Go interface, an ABC — whatever your language uses
for "depend on the shape, not the implementation").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

# Swap this for whatever validation library your project uses (pydantic here).
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


# --- Fetch seam ------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    """The raw result of fetching one URL, independent of *how* it was fetched.

    Deliberately a plain, unvalidated data shape — it's your own transport metadata,
    not untrusted input. Validation belongs entirely in the extraction step below,
    which is the actual boundary where untrusted site data enters the system.
    """

    url: str  # the URL requested
    final_url: str  # where we ended up after redirects (base for resolving relative links)
    status: int
    html: str
    elapsed: float  # wall-clock seconds, useful for a run report


@runtime_checkable
class Fetcher(Protocol):
    """Fetches a URL's raw content. Implement one per fetch strategy.

    e.g. a static-HTTP implementation (requests/httpx) and a headless-browser
    implementation (Playwright/Puppeteer) both satisfy this same shape — the pipeline
    that calls `fetch()` doesn't know or care which one it got.
    """

    async def fetch(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


# --- Extract seam ------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemError:
    """A single record that failed to parse or validate — kept, not discarded.

    Turning a silently-swallowed bad record into a counted, inspectable event is the
    whole point of Pattern 3 (validate at one boundary; collect, don't raise).
    """

    index: int  # position within the page, for locating it in a fixture/log
    error: str  # human-readable reason (a validation error summary, or a parse exception)
    raw: dict[str, Any]  # the raw field dict we tried to validate (empty if parsing itself failed)


@dataclass
class ExtractionResult(Generic[M]):
    """The outcome of extracting one page: what validated + what didn't."""

    records: list[M] = field(default_factory=list)
    errors: list[ItemError] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Every item matched by the extractor — valid or not. Used for the drift ratio."""
        return len(self.records) + len(self.errors)

    @property
    def valid_count(self) -> int:
        return len(self.records)

    @property
    def invalid_count(self) -> int:
        return len(self.errors)

    @property
    def yield_rate(self) -> float:
        """Fraction of matched items that validated. 0.0 when nothing matched — that's drift.

        This is the number Pattern 5 (extraction rate as a production assertion) watches.
        """
        return self.valid_count / self.total if self.total else 0.0


@runtime_checkable
class Extractor(Protocol):
    """Turns raw fetched content into validated records + collected errors.

    A CSS-selector implementation and an LLM implementation both satisfy this same
    shape — as long as both validate against the *same* schema, they're interchangeable,
    which is what makes the self-healing fallback in Pattern 6 possible (swap one
    extractor for another mid-crawl on a page that looks drifted).
    """

    def extract(self, raw: str, *, base_url: str | None = None) -> ExtractionResult: ...
