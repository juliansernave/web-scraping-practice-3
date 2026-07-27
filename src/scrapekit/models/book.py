"""Book schema for books.toscrape.com: title (non-empty), price >= 0, rating 1-5, HttpUrl. (Day 2)

Parsing/pagination is wired on Day 4; the schema lands now so both the CSS extractor (Day 4)
and the LLM extractor (Day 5) validate against the *same* contract.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Book(BaseModel):
    """One book listing.

    ``price`` is a ``Decimal`` — money should never be a binary float. The target parser is
    responsible for stripping the currency symbol (``£51.77`` → ``51.77``) so the model
    receives a clean numeric string; the model enforces it's non-negative.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1)
    price: Decimal = Field(ge=0, description="Price with the currency symbol already stripped.")
    rating: int = Field(ge=1, le=5, description="Star rating 1–5 (books.toscrape encodes it in a class).")
    availability: str = Field(default="", description="Raw availability text, e.g. 'In stock'.")
    url: HttpUrl = Field(description="Absolute URL to the book's detail page.")
