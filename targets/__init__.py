"""Per-target configs: base URL, pydantic schema, CSS selectors, rate limits, robots policy.

``REGISTRY`` maps the CLI's ``<target>`` argument to its :class:`~scrapekit.target.Target`.
Adding a site is: write a module here, then register it below — no pipeline change.
"""

from __future__ import annotations

from scrapekit.target import Target

from targets.books import BOOKS
from targets.quotes import QUOTES, QUOTES_JS

REGISTRY: dict[str, Target] = {t.name: t for t in (QUOTES, QUOTES_JS, BOOKS)}
