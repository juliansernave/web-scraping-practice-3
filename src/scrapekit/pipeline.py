"""Orchestrator: fetch -> extract -> validate -> dedupe -> store -> RunReport.

Day 2: sequential v1 (one page of quotes).
Day 4: async v2 — semaphore-bounded concurrency (yours) composed with rate limiter (theirs).
Day 5: self-healing fallback — CSS yield below drift threshold => retry page via LLM, flag in report.

The two throttles are different knobs that compose:
  * the **semaphore** is *our* resource control — at most N fetches in flight, so we don't open
    1000 sockets at once and exhaust our own memory/FDs;
  * the per-host **rate limiter** (inside the shared client) is *their* politeness policy — a
    ceiling on requests/second regardless of how many coroutines are ready to go.
Ten coroutines all holding a semaphore slot still emit at the limiter's rate; the semaphore
caps concurrency, the limiter caps throughput. Different questions, both answered.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from scrapekit.extractors.base import Extractor
from scrapekit.extractors.css import CssExtractor
from scrapekit.fetchers.base import Fetcher
from scrapekit.logging import get_logger
from scrapekit.monitoring import DEFAULT_DRIFT_THRESHOLD, PageStat, RunReport
from scrapekit.storage import JsonlStore
from scrapekit.target import Target

log = get_logger(__name__)


async def run(
    target: Target,
    *,
    fetcher: Fetcher,
    store: JsonlStore,
    extractor: Extractor | None = None,
    concurrency: int = 10,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> RunReport:
    """Crawl every page of ``target`` concurrently, extract/validate/store, return a RunReport.

    Dependencies are injected (fetcher, store, extractor) so tests drive the whole flow against
    fixture HTML with no network. A single page target (``page_urls is None``) reduces to one
    fetch — the Day-2 behavior, unchanged.
    """
    extractor = extractor or CssExtractor(target.model, target.item_selector, target.parse_item)
    urls = list(target.page_urls) if target.page_urls else [target.url]
    bound = log.bind(target=target.name, pages=len(urls))
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()

    semaphore = asyncio.Semaphore(concurrency)  # OUR cap on concurrent fetches

    async def fetch_and_extract(url: str):
        # Hold a slot only for the network fetch; the client's per-host limiter paces it.
        async with semaphore:
            fetched = await fetcher.fetch(url)
        # Extraction is CPU-bound and needs no slot — do it after releasing.
        return extractor.extract(fetched.html, base_url=fetched.final_url)

    # return_exceptions=True => one page failing (after all retries) can't abort the crawl.
    outcomes = await asyncio.gather(*(fetch_and_extract(u) for u in urls), return_exceptions=True)

    # Aggregate + store sequentially in page order: single-threaded so JsonlStore isn't shared
    # across concurrent writers, and the output file stays deterministic/reviewable.
    pages: list[PageStat] = []
    pages_fetched = pages_failed = 0
    records = valid = invalid = stored = duplicates = 0

    for url, outcome in zip(urls, outcomes):
        if isinstance(outcome, Exception):
            pages_failed += 1
            pages.append(PageStat(url=url, ok=False, matched=0, valid=0, invalid=0))
            bound.warning("page.failed", url=url, error=repr(outcome))
            continue
        pages_fetched += 1
        records += outcome.total
        valid += outcome.valid_count
        invalid += outcome.invalid_count
        for record in outcome.records:
            if store.append(record):
                stored += 1
            else:
                duplicates += 1
        pages.append(
            PageStat(
                url=url,
                ok=True,
                matched=outcome.total,
                valid=outcome.valid_count,
                invalid=outcome.invalid_count,
            )
        )

    report = RunReport(
        target=target.name,
        started_at=started_at,
        pages_requested=len(urls),
        pages_fetched=pages_fetched,
        pages_failed=pages_failed,
        records_extracted=records,
        valid=valid,
        invalid=invalid,
        duplicates=duplicates,
        stored=stored,
        retries=getattr(fetcher, "retries", 0),  # best-effort; not every fetcher retries
        duration_seconds=round(time.perf_counter() - start, 3),
        drift_threshold=drift_threshold,
        pages=pages,
    )
    report.log_summary()
    return report
