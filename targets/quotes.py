"""quotes.toscrape.com — first pipeline target (Day 2); /js/ variant for the Playwright A/B (Day 4).

Page markup (one per quote)::

    <div class="quote">
      <span class="text">“…the quote…”</span>
      <small class="author">Albert Einstein</small>
      <div class="tags"> <a class="tag">change</a> … </div>
    </div>
"""

from __future__ import annotations

from typing import Any

from bs4.element import Tag

from scrapekit.models.quote import Quote
from scrapekit.target import Target


def parse_quote(element: Tag, base_url: str | None) -> dict[str, Any]:
    """Read one ``div.quote`` into the raw dict the :class:`Quote` schema validates.

    ``.get_text`` on a missing node would raise ``AttributeError`` — that's intentional: the
    CSS extractor catches it as a per-item parse error rather than letting bad markup masquerade
    as a valid record.
    """
    return {
        "text": element.select_one("span.text").get_text(strip=True),  # type: ignore[union-attr]
        "author": element.select_one("small.author").get_text(strip=True),  # type: ignore[union-attr]
        "tags": [a.get_text(strip=True) for a in element.select("div.tags a.tag")],
    }


QUOTES = Target(
    name="quotes",
    url="https://quotes.toscrape.com/",
    model=Quote,
    item_selector="div.quote",
    parse_item=parse_quote,
    dedup_fields=("text",),  # a quote is identified by its text, regardless of tag ordering
)

# The JS-rendered twin: identical data, model, selectors, and parser — the quotes are just
# built by JavaScript. Feeding this target through the httpx fetcher yields 0 records; through
# the Playwright fetcher it yields the same 10 as QUOTES. That contrast is the Day-4 A/B, and
# the only thing that changes between the two runs is the fetcher.
QUOTES_JS = Target(
    name="quotes-js",
    url="https://quotes.toscrape.com/js/",
    model=Quote,
    item_selector="div.quote",
    parse_item=parse_quote,
    dedup_fields=("text",),
)
