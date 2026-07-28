"""news.ycombinator.com — real-world tolerant target (Day 6). Its robots.txt sets a 30s
Crawl-delay; running this target through the main pipeline is the proof that the politeness
machinery (``http/robots.py`` + the per-host limiter in ``http/client.py``) honors a real
site's directive, not just the ones we invented for httpbin.

Front-page markup: each story is *two* sibling ``<tr>``s, not one self-contained element —
the title row (``tr.athing``) and the following subtext row (score/author/comments). The
``item_selector`` matches the title row; ``parse_story`` reaches into its next sibling for
the rest. Job posts have no score/author (no votes yet) — the model defaults those rather
than rejecting the record; a story missing a title or link is the real drift signal.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4.element import Tag

from scrapekit.models.hn import HnStory
from scrapekit.target import Target

FRONT_PAGE = "https://news.ycombinator.com/"


def _count(text: str) -> int:
    """"140 comments" / "140\xa0comments" / "discuss" -> 140 / 140 / 0 (no digits yet)."""
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def parse_story(element: Tag, base_url: str | None) -> dict[str, Any]:
    """Read one ``tr.athing`` (+ its subtext sibling row) into the raw dict :class:`HnStory`
    validates."""
    anchor = element.select_one("span.titleline > a")
    subtext = element.find_next_sibling("tr")
    score_span = subtext.select_one("span.score") if subtext else None
    author_a = subtext.select_one("a.hnuser") if subtext else None
    # Direct children only: excludes the age link, which is nested one level deeper inside
    # <span class="age">, so the last direct <a> is always the comments/discuss link.
    subline_links = subtext.select("span.subline > a") if subtext else []

    return {
        "id": element.get("id"),
        "title": anchor.get_text(strip=True),  # type: ignore[union-attr]
        "url": urljoin(base_url or FRONT_PAGE, anchor.get("href")),  # type: ignore[union-attr]
        "points": _count(score_span.get_text()) if score_span else 0,
        "author": author_a.get_text(strip=True) if author_a else None,
        "comments": _count(subline_links[-1].get_text()) if subline_links else 0,
    }


HN = Target(
    name="hn",
    url=FRONT_PAGE,
    model=HnStory,
    item_selector="tr.athing",
    parse_item=parse_story,
    dedup_fields=("id",),
    # No override here: the default settings.requests_per_second (1 req/s) is *less* polite
    # than HN's own 30s Crawl-delay, so http/client.py's robots-aware limiter widens itself
    # automatically — proof the machinery reads the real directive instead of our default.
)
