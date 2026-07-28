"""CLI entry point: `scrapekit run <target> [--extractor css|llm] [--fetcher httpx|playwright]`."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

from scrapekit.config import get_settings
from scrapekit.extractors.base import Extractor
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


def _build_extractor(kind: str, target: Target) -> Extractor | None:
    """None => the pipeline builds its default CssExtractor; 'llm' => an injected LlmExtractor."""
    if kind == "css":
        return None
    if kind == "llm":
        from scrapekit.extractors.llm import (
            LlmExtractor,
        )  # lazy: only pay the anthropic import here

        return LlmExtractor(target.model, settings=get_settings())
    raise typer.BadParameter(f"Unknown extractor {kind!r} (choose css or llm).")


async def _run_async(
    target: Target,
    *,
    fetcher_kind: str,
    extractor_kind: str,
    out_path: Path,
    concurrency: int,
    max_pages: int | None,
    heal: bool,
    write_report: bool,
) -> RunReport:
    fetcher = _build_fetcher(fetcher_kind, target)
    extractor = _build_extractor(extractor_kind, target)
    llm_fallback: Extractor | None = None
    if heal:
        from scrapekit.extractors.llm import LlmExtractor

        llm_fallback = LlmExtractor(target.model, settings=get_settings())

    async with fetcher:
        with JsonlStore(out_path, dedup_fields=target.dedup_fields) as store:
            report = await run_pipeline(
                target,
                fetcher=fetcher,
                store=store,
                extractor=extractor,
                llm_fallback=llm_fallback,
                concurrency=concurrency,
                max_pages=max_pages,
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
    max_pages: int | None = typer.Option(None, help="Crawl only the first N pages (LLM A/B)."),
    heal: bool = typer.Option(
        False, "--heal", help="Self-heal drifted pages via the LLM extractor."
    ),
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
    if extractor not in ("css", "llm"):
        typer.echo(f"Unknown extractor {extractor!r} (choose css or llm).", err=True)
        raise typer.Exit(code=2)
    if fetcher not in ("httpx", "playwright"):
        typer.echo(f"Unknown fetcher {fetcher!r} (choose httpx or playwright).", err=True)
        raise typer.Exit(code=2)

    out_path = Path(out) if out else Path("data") / f"{target}.jsonl"
    rep = asyncio.run(
        _run_async(
            tgt,
            fetcher_kind=fetcher,
            extractor_kind=extractor,
            out_path=out_path,
            concurrency=concurrency,
            max_pages=max_pages,
            heal=heal,
            write_report=report,
        )
    )

    drift = " DRIFT!" if rep.drift_alert else ""
    healed = f" healed={rep.healed_pages}" if rep.healed_pages else ""
    cost = f" cost=${rep.llm_cost_usd:.4f}" if rep.llm_cost_usd else ""
    typer.echo(
        f"\n{target}: pages={rep.pages_fetched}/{rep.pages_requested} "
        f"extracted={rep.records_extracted} valid={rep.valid} invalid={rep.invalid} "
        f"stored={rep.stored} duplicates={rep.duplicates} retries={rep.retries} "
        f"rate={rep.extraction_rate:.2f}{drift}{healed}{cost} "
        f"({rep.duration_seconds}s) -> {out_path}"
    )


if __name__ == "__main__":
    app()
