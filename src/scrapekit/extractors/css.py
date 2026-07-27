"""CSS-selector extraction (BeautifulSoup/lxml) returning validated pydantic instances. (Day 2)

Design (PLAN.md): the extractor owns the *contract* — select each item, build a raw dict,
validate it against the target's schema, and collect (never raise) per-item failures. The
site-specific *parsing* — which selectors, how to read a rating out of a class name, how to
strip a currency symbol — lives in each ``targets/`` config as a ``parse_item`` callable.

Why a callable instead of a flat ``{field: selector}`` map: real pages don't map cleanly.
A book's rating is encoded in a CSS class (``<p class="star-rating Three">``), its price
carries a currency symbol, its URL is relative. A declarative selector map can't express any
of that; a small parse function per target can, while the generic loop below stays shared.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel, ValidationError

from scrapekit.extractors.base import ExtractionResult, ItemError
from scrapekit.logging import get_logger

log = get_logger(__name__)

# A target's parser: (item element, page base URL) -> raw field dict for the model.
ParseItem = Callable[[Tag, str | None], dict[str, Any]]


class CssExtractor:
    """Extract records via CSS selectors, validating each against ``model``.

    ``item_selector`` matches each record's root element; ``parse_item`` turns one such
    element into a raw dict; ``model`` validates it. Items that fail to parse or validate are
    collected as :class:`ItemError`\\ s and logged — the crawl continues.
    """

    def __init__(
        self,
        model: type[BaseModel],
        item_selector: str,
        parse_item: ParseItem,
        *,
        parser: str = "lxml",
    ) -> None:
        self._model = model
        self._item_selector = item_selector
        self._parse_item = parse_item
        self._parser = parser

    def extract(self, html: str, *, base_url: str | None = None) -> ExtractionResult:
        soup = BeautifulSoup(html, self._parser)
        elements = soup.select(self._item_selector)

        result: ExtractionResult = ExtractionResult()
        for index, element in enumerate(elements):
            raw: dict[str, Any] = {}
            try:
                raw = self._parse_item(element, base_url)
                result.records.append(self._model.model_validate(raw))
            except ValidationError as exc:
                # Schema rejected the record — the contract catching bad site data at the edge.
                reason = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                )
                result.errors.append(ItemError(index=index, error=reason, raw=raw))
                log.warning("extract.invalid", index=index, reason=reason, model=self._model.__name__)
            except Exception as exc:  # noqa: BLE001 — a missing/renamed node must not abort the crawl
                result.errors.append(ItemError(index=index, error=repr(exc), raw=raw))
                log.warning("extract.parse_error", index=index, error=repr(exc))

        log.info(
            "extract.done",
            selector=self._item_selector,
            model=self._model.__name__,
            total=result.total,
            valid=result.valid_count,
            invalid=result.invalid_count,
        )
        return result
