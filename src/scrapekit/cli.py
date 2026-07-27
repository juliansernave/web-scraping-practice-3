"""CLI entry point: `scrapekit run <target> [--extractor css|llm] [--fetcher httpx|playwright]`."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

from scrapekit.fetchers.httpx_fetcher import HttpxFetcher
from scrapekit.logging import configure_logging, get_logger
from scrapekit.pipeline import RunReport, run as run_pipeline
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


async def _run_async(target: Target, out_path: Path) -> RunReport:
    async with HttpxFetcher() as fetcher:
        with JsonlStore(out_path, dedup_fields=target.dedup_fields) as store:
            return await run_pipeline(target, fetcher=fetcher, store=store)


@app.command()
def run(
    target: str,
    extractor: str = typer.Option("css", help="Extraction strategy: css or llm"),
    fetcher: str = typer.Option("httpx", help="Fetcher: httpx or playwright"),
    out: str | None = typer.Option(None, help="Output JSONL path (default: data/<target>.jsonl)"),
    json_logs: bool = typer.Option(False, "--json-logs", help="Emit JSON logs (prod) vs console."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="DEBUG-level logging."),
) -> None:
    """Run the pipeline for a configured target (see targets/)."""
    configure_logging(json_logs=json_logs, level=logging.DEBUG if verbose else logging.INFO)

    registry = _load_registry()
    tgt = registry.get(target)
    if tgt is None:
        typer.echo(
            f"Unknown target {target!r}. Available: {', '.join(sorted(registry))}", err=True
        )
        raise typer.Exit(code=2)

    # These strategies land later in the plan; fail clearly rather than run silently wrong.
    if extractor != "css":
        typer.echo(f"Extractor {extractor!r} not available yet (LLM is Day 5).", err=True)
        raise typer.Exit(code=2)
    if fetcher != "httpx":
        typer.echo(f"Fetcher {fetcher!r} not available yet (Playwright is Day 4).", err=True)
        raise typer.Exit(code=2)

    out_path = Path(out) if out else Path("data") / f"{target}.jsonl"
    report = asyncio.run(_run_async(tgt, out_path))

    typer.echo(
        f"\n{target}: extracted={report.records_extracted} "
        f"valid={report.valid} invalid={report.invalid} "
        f"stored={report.stored} duplicates={report.duplicates} "
        f"({report.duration_seconds}s) -> {out_path}"
    )


if __name__ == "__main__":
    app()
