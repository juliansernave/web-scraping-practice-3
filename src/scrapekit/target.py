"""Target config type: what the pipeline needs to know to scrape one site.

A ``Target`` is pure data — URL, schema, and how to parse an item — kept separate from the
framework so adding a new site is a config file in ``targets/``, never a change to the
pipeline. The pipeline depends on this shape; the concrete configs (``targets/quotes.py``,
``targets/books.py``) supply the values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from scrapekit.extractors.css import ParseItem


@dataclass(frozen=True)
class Target:
    """Everything the pipeline needs for one site.

    ``dedup_fields`` names the identity subset for storage dedup (e.g. ``("url",)`` for
    books); ``None`` dedupes on the whole record.
    """

    name: str
    url: str
    model: type[BaseModel]
    item_selector: str
    parse_item: ParseItem
    dedup_fields: tuple[str, ...] | None = None
