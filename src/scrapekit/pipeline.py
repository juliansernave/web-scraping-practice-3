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

Self-healing (Day 5): when a page's extraction yield falls below the drift threshold — the
signal that selectors broke — the pipeline re-extracts *that page* through an injected LLM
fallback (reads by meaning, not by CSS class) and flags it in the report. The drift alert stops
being just a warning and becomes a recovery trigger.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from scrapekit.extractors.base import Extractor, ExtractionResult
from scrapekit.extractors.css import CssExtractor
from scrapekit.fetchers.base import Fetcher
from scrapekit.logging import get_logger
from scrapekit.monitoring import DEFAULT_DRIFT_THRESHOLD, PageStat, RunReport
from scrapekit.storage import JsonlStore
from scrapekit.target import Target

log = get_logger(__name__)


@dataclass
class _PageOutcome:
    """One page's fetch+extract result, plus the html retained only if it needs healing."""

    url: str
    result: ExtractionResult
    final_url: str
    heal_html: str | None  # the page HTML, kept only when yield is below the drift threshold


def _page_yield(result: ExtractionResult) -> float:
    return result.valid_count / result.total if result.total else 0.0


async def run(
    target: Target,
    *,
    fetcher: Fetcher,
    store: JsonlStore,
    extractor: Extractor | None = None,
    llm_fallback: Extractor | None = None,
    concurrency: int = 10,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    max_pages: int | None = None,
) -> RunReport:
    """Crawl every page of ``target`` concurrently, extract/validate/store, return a RunReport.

    Dependencies are injected (fetcher, store, extractor, llm_fallback) so tests drive the whole
    flow against fixture HTML with no network. ``max_pages`` bounds the crawl (used for the LLM
    A/B so it runs on 2–3 pages, not 50). ``llm_fallback``, when present, re-extracts any page
    whose yield drops below ``drift_threshold``.
    """
    extractor = extractor or CssExtractor(target.model, target.item_selector, target.parse_item)
    urls = list(target.page_urls) if target.page_urls else [target.url]
    if max_pages is not None:
        urls = urls[:max_pages]
    bound = log.bind(target=target.name, pages=len(urls))
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()

    semaphore = asyncio.Semaphore(concurrency)  # OUR cap on concurrent fetches

    async def fetch_and_extract(url: str) -> _PageOutcome:
        async with semaphore:  # hold a slot only for the network fetch
            fetched = await fetcher.fetch(url)
        result = extractor.extract(fetched.html, base_url=fetched.final_url)
        # Retain html for the LLM fallback only when this page looks drifted — bounds memory.
        needs_heal = llm_fallback is not None and _page_yield(result) < drift_threshold
        return _PageOutcome(url, result, fetched.final_url, fetched.html if needs_heal else None)

    # return_exceptions=True => one page failing (after all retries) can't abort the crawl.
    outcomes = await asyncio.gather(*(fetch_and_extract(u) for u in urls), return_exceptions=True)

    # Aggregate + store sequentially in page order: single-threaded so JsonlStore isn't shared
    # across concurrent writers, and any LLM heal spends the budget deterministically.
    pages: list[PageStat] = []
    pages_fetched = pages_failed = healed_pages = 0
    records = valid = invalid = stored = duplicates = 0
    healing_budget_hit = False

    for url, outcome in zip(urls, outcomes):
        if isinstance(outcome, Exception):
            pages_failed += 1
            pages.append(PageStat(url=url, ok=False, matched=0, valid=0, invalid=0))
            bound.warning("page.failed", url=url, error=repr(outcome))
            continue
        pages_fetched += 1
        result = outcome.result
        healed = False

        # Self-heal: a flagged page (yield below threshold) is re-extracted via the LLM.
        if outcome.heal_html is not None and not healing_budget_hit and llm_fallback is not None:
            from scrapekit.extractors.llm import BudgetExceededError

            try:
                result = llm_fallback.extract(outcome.heal_html, base_url=outcome.final_url)
                healed = True
                healed_pages += 1
                bound.info("heal.recovered", url=url, records=result.valid_count)
            except BudgetExceededError as exc:
                healing_budget_hit = True  # cap reached — stop healing, keep CSS results
                bound.warning("heal.budget_exceeded", url=url, error=str(exc))

        records += result.total
        valid += result.valid_count
        invalid += result.invalid_count
        for record in result.records:
            if store.append(record):
                stored += 1
            else:
                duplicates += 1
        pages.append(
            PageStat(
                url=url,
                ok=True,
                matched=result.total,
                valid=result.valid_count,
                invalid=result.invalid_count,
                healed=healed,
            )
        )

    # Sum LLM spend across whichever extractors ran (primary and/or fallback).
    extractors_used = [extractor]
    if llm_fallback is not None and llm_fallback is not extractor:
        extractors_used.append(llm_fallback)
    llm_cost = sum(getattr(e, "spent_usd", 0.0) for e in extractors_used)

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
        retries=getattr(fetcher, "retries", 0),
        duration_seconds=round(time.perf_counter() - start, 3),
        drift_threshold=drift_threshold,
        healed_pages=healed_pages,
        llm_cost_usd=round(llm_cost, 4),
        pages=pages,
    )
    report.log_summary()
    return report
