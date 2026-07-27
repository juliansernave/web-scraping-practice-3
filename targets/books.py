"""books.toscrape.com — the scale target: 50 pages / 1000 books, async crawl (Day 4), LLM A/B (Day 5).

Listing markup (one per book)::

    <article class="product_pod">
      <h3><a href="the-book_1/index.html" title="A Light in the Attic">...</a></h3>
      <p class="price_color">£51.77</p>
      <p class="star-rating Three"></p>
      <p class="instock availability">In stock</p>
    </article>

Two quirks the parser handles: the rating is encoded as a CSS *class* (``star-rating Three``),
and the price carries a currency symbol (plus occasional mojibake) — both stripped so the model
receives clean values.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4.element import Tag

from scrapekit.models.book import Book
from scrapekit.target import Target

CATALOGUE = "https://books.toscrape.com/catalogue/"
PAGE_URLS = tuple(f"{CATALOGUE}page-{n}.html" for n in range(1, 51))  # 50 pages × 20 = 1000

_RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def parse_book(element: Tag, base_url: str | None) -> dict[str, Any]:
    """Read one ``article.product_pod`` into the raw dict the :class:`Book` schema validates."""
    anchor = element.select_one("h3 a")
    price_text = element.select_one("p.price_color").get_text(strip=True)  # type: ignore[union-attr]
    rating_p = element.select_one("p.star-rating")
    # The rating word is the class that isn't "star-rating"; StopIteration if the markup drifts.
    rating_word = next(c for c in rating_p.get("class", []) if c != "star-rating")  # type: ignore[union-attr]

    return {
        "title": anchor.get("title"),  # type: ignore[union-attr]
        # Strip everything but digits and the decimal point: "£51.77" / "Â£51.77" -> "51.77".
        "price": re.sub(r"[^0-9.]", "", price_text),
        "rating": _RATING_WORDS[rating_word],
        "availability": element.select_one("p.instock.availability").get_text(strip=True),  # type: ignore[union-attr]
        # Detail-page links are relative to the catalogue page they appear on.
        "url": urljoin(base_url or CATALOGUE, anchor.get("href")),  # type: ignore[union-attr]
    }


BOOKS = Target(
    name="books",
    url=PAGE_URLS[0],
    model=Book,
    item_selector="article.product_pod",
    parse_item=parse_book,
    dedup_fields=("url",),  # a book is identified by its detail URL
    page_urls=PAGE_URLS,
    requests_per_second=8.0,  # a practice site built for scraping — polite, but lets us breathe
)
