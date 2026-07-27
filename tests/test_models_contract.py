"""Contract tests: the pydantic schemas are executable contracts with each site.

Malformed payloads must be rejected *at the boundary*, in CI — not silently written to the
CSV. This is the test-automation framing: a schema is an assertion that runs on every record.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrapekit.models.book import Book
from scrapekit.models.quote import Quote

# --- Quote -------------------------------------------------------------------------------

GOOD_QUOTE = {
    "text": "“It is our choices that show what we truly are.”",
    "author": "J.K. Rowling",
    "tags": ["choices"],
}

BAD_QUOTES = {
    "empty-text": {"text": "", "author": "A"},
    "only-wrapping-marks": {"text": "“”", "author": "A"},  # unwraps to "" -> rejected
    "whitespace-text": {"text": "   ", "author": "A"},
    "empty-author": {"text": "hi", "author": ""},
    "missing-author": {"text": "hi"},
    "extra-field": {"text": "hi", "author": "A", "surprise": 1},  # extra="forbid"
}


def test_quote_accepts_valid_payload():
    q = Quote.model_validate(GOOD_QUOTE)
    # The wrapping curly marks are stripped at the boundary — we store the quote, not its markup.
    assert q.text == "It is our choices that show what we truly are."
    assert q.author == "J.K. Rowling"
    assert q.tags == ["choices"]


@pytest.mark.parametrize("payload", BAD_QUOTES.values(), ids=list(BAD_QUOTES))
def test_quote_rejects_bad_payload(payload):
    with pytest.raises(ValidationError):
        Quote.model_validate(payload)


# --- Book --------------------------------------------------------------------------------

GOOD_BOOK = {
    "title": "A Light in the Attic",
    "price": "51.77",  # currency symbol already stripped by the parser
    "rating": 3,
    "availability": "In stock",
    "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
}

BAD_BOOKS = {
    "empty-title": {**GOOD_BOOK, "title": ""},
    "negative-price": {**GOOD_BOOK, "price": "-1.00"},
    "rating-too-low": {**GOOD_BOOK, "rating": 0},
    "rating-too-high": {**GOOD_BOOK, "rating": 6},
    "junk-url": {**GOOD_BOOK, "url": "not-a-url"},
    "extra-field": {**GOOD_BOOK, "surprise": 1},
}


def test_book_accepts_valid_payload():
    from decimal import Decimal

    b = Book.model_validate(GOOD_BOOK)
    assert b.price == Decimal("51.77")  # Decimal, not float — money is never binary float
    assert b.rating == 3


@pytest.mark.parametrize("payload", BAD_BOOKS.values(), ids=list(BAD_BOOKS))
def test_book_rejects_bad_payload(payload):
    with pytest.raises(ValidationError):
        Book.model_validate(payload)
