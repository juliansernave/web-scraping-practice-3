"""CLI entry point: `scrapekit run <target> [--extractor css|llm] [--fetcher httpx|playwright]`."""

import typer

app = typer.Typer(help="scrapekit — production-grade scraping pipeline")


@app.callback()
def main() -> None:
    """Keep `run` an explicit subcommand (typer collapses single-command apps otherwise)."""


@app.command()
def run(
    target: str,
    extractor: str = typer.Option("css", help="Extraction strategy: css or llm"),
    fetcher: str = typer.Option("httpx", help="Fetcher: httpx or playwright"),
) -> None:
    """Run the pipeline for a configured target (see targets/)."""
    # TODO(Day 2): wire up targets/ config -> pipeline.run()
    typer.echo(f"Not implemented yet — Day 2 task. (target={target}, extractor={extractor}, fetcher={fetcher})")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
