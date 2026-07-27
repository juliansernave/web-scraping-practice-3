"""Per-target configs: base URL, pydantic schema, CSS selectors, rate limits, robots policy.

``REGISTRY`` maps the CLI's ``<target>`` argument to its :class:`~scrapekit.target.Target`.
Adding a site is: write a module here, then register it below — no pipeline change.
"""

from __future__ import annotations

from scrapekit.target import Target

from targets.quotes import QUOTES

REGISTRY: dict[str, Target] = {
    QUOTES.name: QUOTES,
    # books (Day 4), hn (Day 6) register here as they come online.
}
