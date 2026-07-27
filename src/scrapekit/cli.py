"""CLI entry point: `scrapekit run <target> [--extractor css|llm] [--fetcher httpx|playwright]`."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

from scrapekit.config import get_settings
from scrapekit.fetchers.base import Fetcher
from scrapekit.fetchers.httpx_fetcher import HttpxFetcher
from scrapekit.logging import configure_logging, get_logger
from scrapekit.monitoring import RunReport
from scrapekit.pipeline import run as run_pipeline
from scrapekit.storage import JsonlStore
from scrapekit.target import Target

app = typer.Typer(help="scrapekit — production-grade scraping pipeline")
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """Keep `run` an explicit subcommand (typer collapses single-command apps otherwise)."""


def _load_registry() -> dict[str, Target]:
    """Import the target registry from the repo-root ``targets/`` package.

    Console scripts don't put the invocation cwd on ``sys.path``, but ``targets/`` lives at
    the repo root (it's dev config, not packaged). Add cwd on demand so ``uv run scrapekit``
    from the repo root finds it.
    """
    try:
        from targets import REGISTRY
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path.cwd()))
        from targets import REGISTRY
    return REGISTRY


def _build_fetcher(kind: str, target: Target) -> Fetcher:
    """Construct the chosen fetcher, applying the target's per-host rate to the httpx client."""
    if kind == "httpx":
        settings = get_settings()
        if target.requests_per_second is not None:
            settings = settings.model_copy(
                update={"requests_per_second": target.requests_per_second}
            )
        return HttpxFetcher(settings=settings)
    if kind == "playwright":
        # Imported lazily so an httpx-only run never pays the Playwright import cost.
        from scrapekit.fetchers.playwright_fetcher import PlaywrightFetcher

        return PlaywrightFetcher()
    raise typer.BadParameter(f"Unknown fetcher {kind!r} (choose httpx or playwright).")


async def _run_async(
    target: Target, fetcher_kind: str, out_path: Path, concurrency: int, write_report: bool
) -> RunReport:
    fetcher = _build_fetcher(fetcher_kind, target)
    async with fetcher:
        with JsonlStore(out_path, dedup_fields=target.dedup_fields) as store:
            report = await run_pipeline(
                target, fetcher=fetcher, store=store, concurrency=concurrency
            )
    if write_report:
        path = report.write("reports")
        typer.echo(f"report -> {path}")
    return report


@app.command()
def run(
    target: str,
    extractor: str = typer.Option("css", help="Extraction strategy: css or llm"),
    fetcher: str = typer.Option("httpx", help="Fetcher: httpx or playwright"),
    out: str | None = typer.Option(None, help="Output JSONL path (default: data/<target>.jsonl)"),
    concurrency: int = typer.Option(10, help="Max concurrent fetches (our resource cap)."),
    report: bool = typer.Option(False, "--report", help="Write the RunReport JSON to reports/."),
    json_logs: bool = typer.Option(False, "--json-logs", help="Emit JSON logs (prod) vs console."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="DEBUG-level logging."),
) -> None:
    """Run the pipeline for a configured target (see targets/)."""
    configure_logging(json_logs=json_logs, level=logging.DEBUG if verbose else logging.INFO)

    registry = _load_registry()
    tgt = registry.get(target)
    if tgt is None:
        typer.echo(f"Unknown target {target!r}. Available: {', '.join(sorted(registry))}", err=True)
        raise typer.Exit(code=2)

    # The LLM extractor lands on Day 5; fail clearly rather than run silently wrong.
    if extractor != "css":
        typer.echo(f"Extractor {extractor!r} not available yet (LLM is Day 5).", err=True)
        raise typer.Exit(code=2)
    if fetcher not in ("httpx", "playwright"):
        typer.echo(f"Unknown fetcher {fetcher!r} (choose httpx or playwright).", err=True)
        raise typer.Exit(code=2)

    out_path = Path(out) if out else Path("data") / f"{target}.jsonl"
    rep = asyncio.run(_run_async(tgt, fetcher, out_path, concurrency, report))

    drift = " DRIFT!" if rep.drift_alert else ""
    typer.echo(
        f"\n{target}: pages={rep.pages_fetched}/{rep.pages_requested} "
        f"extracted={rep.records_extracted} valid={rep.valid} invalid={rep.invalid} "
        f"stored={rep.stored} duplicates={rep.duplicates} retries={rep.retries} "
        f"rate={rep.extraction_rate:.2f}{drift} ({rep.duration_seconds}s) -> {out_path}"
    )


if __name__ == "__main__":
    app()
