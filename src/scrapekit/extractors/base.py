"""Extractor protocol: extract(html) -> ExtractionResult(valid records, per-item errors).

Day 2. Validation errors are collected (structured log events + counters), never raised —
one bad record must not abort a crawl.

This is the upgrade of the per-item ``try/except`` pattern the plan calls out: instead of
swallowing a bad record with a bare ``except``, we capture *what* failed, *why*, and the raw
payload — turning silent data loss into a counted, inspectable event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class ItemError:
    """A single record that failed to parse or validate, kept for the run report and logs."""

    index: int  # position of the item within the page (for locating it in the fixture)
    error: str  # human-readable reason (a ValidationError summary, or a parse exception)
    raw: dict[str, Any]  # the raw field dict we tried to validate (empty if parsing itself failed)


@dataclass
class ExtractionResult(Generic[M]):
    """The outcome of extracting one page: the records that validated + the ones that didn't."""

    records: list[M] = field(default_factory=list)
    errors: list[ItemError] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Every item the page's item-selector matched — valid or not."""
        return len(self.records) + len(self.errors)

    @property
    def valid_count(self) -> int:
        return len(self.records)

    @property
    def invalid_count(self) -> int:
        return len(self.errors)


@runtime_checkable
class Extractor(Protocol):
    """Turns page HTML into validated records. CSS (Day 2) and LLM (Day 5) both implement it.

    ``base_url`` lets an extractor resolve relative links (e.g. a book's detail URL) against
    the page they came from.
    """

    def extract(self, html: str, *, base_url: str | None = None) -> ExtractionResult: ...
