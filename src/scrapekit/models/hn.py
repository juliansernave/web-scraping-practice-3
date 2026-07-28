"""Hacker News story schema for the front page. (Day 6)

Job posts and some "Ask HN" threads have no score/author (no votelinks yet, no comments),
so ``points``/``author`` default rather than fail — a story missing a title or link is the
real drift signal, not a fresh post with zero votes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class HnStory(BaseModel):
    """One front-page story: id, title, target url, score, author, comment count."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: int = Field(gt=0, description="HN item id, from the <tr class='athing' id=...>.")
    title: str = Field(min_length=1)
    url: HttpUrl = Field(description="The story's link, or its own HN item page for text posts.")
    points: int = Field(default=0, ge=0)
    author: str | None = Field(default=None)
    comments: int = Field(default=0, ge=0)
