"""Day 6 spike — crawl4ai vs. the hand-rolled pipeline, same target and schema.

Not framework code: a one-off script to answer "how much of Days 1-5 does crawl4ai replace?"
Run with ``uv run --group spike python notes/crawl4ai_spike.py`` (needs ANTHROPIC_API_KEY).

Original plan was openlibrary.org/subjects/science_fiction (the PLAN.md pick). It failed: a
manual probe got HTTP 303 to /verify_human — a bot-detection wall, not a JS-hydration problem.
Solving that is explicitly out of scope this week (PLAN.md: anti-bot deep-dives => Apify), so
the spike target switched to Hacker News instead, reusing the exact schema targets/hn.py
already validates against — the fair A/B is "same data, two fetch+extract paths," not
"different sites."

Path A (Days 1-5, already run): httpx fetcher -> CSS extractor -> HnStory. 30/30 valid, $0,
~1.6s, and it enforced robots.txt's 30s Crawl-delay along the way (see reports/hn_*.json).

Path B (this script): crawl4ai's browser crawl -> markdown -> the *same* LlmExtractor and
HnStory model from scrapekit.extractors.llm, unchanged. crawl4ai's markdown already IS the
"strip to text" step LlmExtractor's own html_to_text would otherwise do -- feeding markdown
into an extractor built for HTML is redundant work, not a wrong result.
"""

from __future__ import annotations

import asyncio
import time

from crawl4ai import AsyncWebCrawler

from scrapekit.config import get_settings
from scrapekit.extractors.llm import LlmExtractor
from scrapekit.models.hn import HnStory

FRONT_PAGE = "https://news.ycombinator.com/"


async def main() -> None:
    start = time.perf_counter()
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=FRONT_PAGE)
    fetch_seconds = time.perf_counter() - start
    assert result.success, f"crawl4ai fetch failed: status={result.status_code}"

    markdown = result.markdown.raw_markdown
    print(f"crawl4ai fetch: {fetch_seconds:.2f}s, {len(markdown)} markdown chars")

    extractor = LlmExtractor(HnStory, settings=get_settings())
    extract_start = time.perf_counter()
    extraction = extractor.extract(markdown, base_url=FRONT_PAGE)
    extract_seconds = time.perf_counter() - extract_start

    print(
        f"llm extract: {extract_seconds:.2f}s, "
        f"valid={extraction.valid_count} invalid={extraction.invalid_count} "
        f"cost=${extractor.spent_usd:.4f}"
    )
    for story in extraction.records[:5]:
        print(f"  {story.points:>4} pts  {story.title}")
    for err in extraction.errors[:5]:
        print(f"  REJECTED: {err.error}")


if __name__ == "__main__":
    asyncio.run(main())
