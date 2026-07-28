"""Config-as-data pattern (Pattern 1 in references/patterns.md).

A new site to scrape is a plain data value, not new code in the shared pipeline. This
file shows the shape; a real project would put the `Target` type in one module and each
concrete site's config in its own small module, then register them here.

Genericized from a real scraper-framework build. The specific field names below are a
reasonable starting point — add/drop fields to fit what your pipeline actually needs
(e.g. auth headers, a pagination cursor pattern, per-target LLM vs CSS preference).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    pass  # import your Tag/Element type here for ParseItem's real signature


# A target's parser: (matched element, page base URL) -> raw field dict for the model.
# A callable, not a declarative {field: selector} map — real pages don't map cleanly
# (a rating encoded in a class name, a price with a currency symbol, a relative URL).
# The callable handles that per-site messiness; the generic select-and-validate loop
# elsewhere in the pipeline stays identical for every target.
ParseItem = Callable[[Any, str | None], dict[str, Any]]


@dataclass(frozen=True)
class Target:
    """Everything the pipeline needs to know to scrape one site. Pure data — no logic.

    Keeping this frozen/immutable means a target's config can't be accidentally mutated
    mid-crawl, and makes it trivial to build test variants with e.g. `dataclasses.replace`
    (scope a multi-page target down to one page for a focused test, etc).
    """

    name: str  # lookup key, e.g. used as a CLI argument
    url: str  # entry point / single-page URL
    model: type[BaseModel]  # the validation schema — the contract for this target's records
    item_selector: str  # CSS/XPath selector matching each record's root element
    parse_item: ParseItem  # element -> raw dict, before validation

    # Optional: narrows dedup identity to specific fields (e.g. ("url",)) instead of the
    # whole record — set this when a record's "identity" is smaller than all its fields.
    dedup_fields: tuple[str, ...] | None = None

    # Optional: the full list of pages to crawl, known up front. Deliberately explicit
    # rather than "follow the next link" — knowing every URL ahead of time is what lets
    # the pipeline fan pages out concurrently. A chain you have to walk one link at a
    # time can't be parallelized. If your target genuinely requires link-following
    # pagination (next-page URL only discoverable after fetching the current page),
    # that's a signal the pipeline needs a distinct "discover more pages" step — don't
    # force it into this shape.
    page_urls: tuple[str, ...] | None = None

    # Optional: this site's own rate-limit override — per-host politeness lives with the
    # target's config, not as one global knob shared across every site you scrape.
    requests_per_second: float | None = None


# --- Example target + registry -----------------------------------------------------------
#
# In a real project, each target like this would live in its own small module
# (targets/example_site.py), and this registry would import and collect them.
# Shown inline here for a self-contained example.

# from your_project.models import ExampleRecord
#
# def _parse_example_item(element, base_url):
#     return {
#         "title": element.select_one(".title").get_text(strip=True),
#         "price": element.select_one(".price").get_text(strip=True).lstrip("$"),
#     }
#
# EXAMPLE_TARGET = Target(
#     name="example",
#     url="https://example.test/catalog",
#     model=ExampleRecord,
#     item_selector=".product",
#     parse_item=_parse_example_item,
#     dedup_fields=("title",),
# )
#
# REGISTRY: dict[str, Target] = {t.name: t for t in (EXAMPLE_TARGET,)}
#
# Adding site #2 is: write one more module like the above, add it to the tuple below.
# The pipeline that consumes `Target` instances never changes.
