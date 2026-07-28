"""RunReport: pages fetched, records extracted/valid/invalid/duped, retry counts, duration,
extraction-rate-per-page — plus a drift alert when extraction rate drops below threshold.

Day 4. Selector drift is the #1 silent failure mode of production scrapers: the site renames a
class, your selectors quietly match nothing, and the crawl "succeeds" while collecting zero
data. You catch it by *monitoring the extraction rate*, not by hoping. The rate is a production
assertion that runs on every crawl; the drift alert is its failure.

Reports are written to ``reports/`` as JSON and logged as a summary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scrapekit.logging import get_logger

log = get_logger(__name__)

# Below this fraction of matched-items-that-validated, we treat the run as drifted. A broken
# item selector matches nothing (rate 0); renamed field classes match items that all fail to
# parse (rate 0). Both trip the same wire.
DEFAULT_DRIFT_THRESHOLD = 0.5


@dataclass
class PageStat:
    """Per-page extraction outcome — the granularity drift detection needs."""

    url: str
    ok: bool  # did the fetch succeed (vs. failing after all retries)
    matched: int  # items the item-selector matched on this page
    valid: int
    invalid: int
    healed: bool = False  # CSS yield was low, so the LLM fallback re-extracted this page

    @property
    def yield_rate(self) -> float:
        """Fraction of matched items that validated. 0.0 when nothing matched — that's drift."""
        return self.valid / self.matched if self.matched else 0.0


@dataclass
class RunReport:
    """The outcome of one crawl. The counts are returnable (not just logged) so the CLI can
    print them and the drift alert can gate a run."""

    target: str
    started_at: str  # ISO timestamp, stamped by the pipeline
    pages_requested: int
    pages_fetched: int
    pages_failed: int
    records_extracted: int  # everything the selector matched, across all pages
    valid: int
    invalid: int
    duplicates: int
    stored: int
    retries: int  # transient-failure retries the fetcher performed during this run
    duration_seconds: float
    drift_threshold: float
    healed_pages: int = 0  # pages the LLM fallback recovered after CSS drift
    llm_cost_usd: float = 0.0  # what the self-healing (or LLM extractor) spent this run
    pages: list[PageStat] = field(default_factory=list)

    @property
    def extraction_rate(self) -> float:
        """Overall fraction of matched items that validated — the health signal."""
        return self.valid / self.records_extracted if self.records_extracted else 0.0

    @property
    def drift_alert(self) -> bool:
        """True when the extraction rate falls below the threshold — selectors likely broke."""
        return self.extraction_rate < self.drift_threshold

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["extraction_rate"] = round(self.extraction_rate, 4)
        data["drift_alert"] = self.drift_alert
        for stat, dumped in zip(self.pages, data["pages"]):
            dumped["yield_rate"] = round(stat.yield_rate, 4)
        return data

    def summary(self) -> dict[str, object]:
        """The report without the per-page detail — for a one-line log event."""
        return {k: v for k, v in self.as_dict().items() if k != "pages"}

    def log_summary(self) -> None:
        log.info("run.report", **self.summary())
        if self.drift_alert:
            log.warning(
                "drift.alert",
                target=self.target,
                extraction_rate=round(self.extraction_rate, 4),
                threshold=self.drift_threshold,
                hint="extraction rate below threshold — selectors may have drifted",
            )

    def write(self, directory: str | Path) -> Path:
        """Persist the full report (with per-page detail) as JSON under ``directory``."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        safe_ts = self.started_at.replace(":", "").replace("-", "").replace(".", "")
        path = directory / f"{self.target}_{safe_ts}.json"
        path.write_text(json.dumps(self.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
