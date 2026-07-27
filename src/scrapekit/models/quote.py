"""Quote schema for quotes.toscrape.com: text (non-empty), author, tags. (Day 2)

The schema *is* the contract with the site (PLAN.md gap #2). Validating here means malformed
markup fails loudly at the extraction boundary — not silently three stages later in the CSV.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Quote(BaseModel):
    """One quote: the text, its author, and its tags.

    ``str_strip_whitespace`` trims every string field; the ``text`` validator additionally
    strips the curly quotation marks the site wraps around each quote (``“…”``) so the stored
    value is the quote itself, not the site's presentation of it.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=1, description="The quote body, unwrapped from its “ ” marks.")
    author: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)

    @field_validator("text", mode="before")
    @classmethod
    def _unwrap_quotation_marks(cls, value: object) -> object:
        # Run BEFORE min_length so a quote that's *only* wrapping marks (“”) unwraps to "" and
        # is then rejected by min_length=1 — not silently stored as an empty string.
        if isinstance(value, str):
            return value.strip().strip("“”‘’\"'").strip()
        return value
