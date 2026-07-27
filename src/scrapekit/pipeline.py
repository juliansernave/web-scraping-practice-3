"""Orchestrator: fetch -> extract -> validate -> dedupe -> store -> RunReport.

Day 2: sequential v1 (one page of quotes).
Day 4: async v2 — semaphore-bounded concurrency (yours) composed with rate limiter (theirs).
Day 5: self-healing fallback — CSS yield below drift threshold => retry page via LLM, flag in report.

Every stage logs one structured event bound to the target; the ``RunReport`` is the same
counts made returnable so the CLI (and Day 4's ``monitoring.py``) can act on them.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from scrapekit.extractors.base import Extractor
from scrapekit.extractors.css import CssExtractor
from scrapekit.fetchers.base import Fetcher
from scrapekit.logging import get_logger
from scrapekit.storage import JsonlStore
from scrapekit.target import Target

log = get_logger(__name__)


@dataclass
class RunReport:
    """The outcome of one pipeline run — the numbers the ``Done when`` criteria care about."""

    target: str
    pages_fetched: int
    records_extracted: int  # everything the selector matched
    valid: int  # passed schema validation
    invalid: int  # failed validation/parsing (see extract.invalid logs)
    duplicates: int  # valid but already stored (content-hash match)
    stored: int  # valid and newly written
    duration_seconds: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def run(
    target: Target,
    *,
    fetcher: Fetcher,
    store: JsonlStore,
    extractor: Extractor | None = None,
) -> RunReport:
    """Run the sequential pipeline for one page of ``target``.

    Dependencies are injected (fetcher, store, extractor) so Day 3 can drive the whole flow
    against fixture HTML with no network. Defaults to a :class:`CssExtractor` built from the
    target's selector + parser.
    """
    extractor = extractor or CssExtractor(target.model, target.item_selector, target.parse_item)
    bound = log.bind(target=target.name, url=target.url)
    start = time.perf_counter()

    # 1. fetch
    fetched = await fetcher.fetch(target.url)
    bound.info("fetch.done", status=fetched.status, elapsed=round(fetched.elapsed, 3))

    # 2. extract + validate (errors are collected inside the extractor, not raised)
    result = extractor.extract(fetched.html, base_url=fetched.final_url)

    # 3. dedupe + store
    stored = duplicates = 0
    for record in result.records:
        if store.append(record):
            stored += 1
        else:
            duplicates += 1

    report = RunReport(
        target=target.name,
        pages_fetched=1,
        records_extracted=result.total,
        valid=result.valid_count,
        invalid=result.invalid_count,
        duplicates=duplicates,
        stored=stored,
        duration_seconds=round(time.perf_counter() - start, 3),
    )
    bound.info("run.done", **report.as_dict())
    return report
