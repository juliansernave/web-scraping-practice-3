"""LLM extractor + self-healing tests.

The Anthropic client is injected, so every test here runs with **zero network** against a fake
client — proving the contract (schema-identical output, budget cap, caching, self-healing)
deterministically. One ``@pytest.mark.live`` smoke test exercises the real API and is excluded
from CI (`-m "not live"`).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from scrapekit.config import Settings, get_settings
from scrapekit.extractors.css import CssExtractor
from scrapekit.extractors.llm import BudgetExceededError, LlmExtractor
from scrapekit.fetchers.base import FetchResult
from scrapekit.models.book import Book
from scrapekit.models.quote import Quote
from scrapekit.pipeline import run
from scrapekit.storage import JsonlStore
from targets.books import BOOKS, parse_book


# --- fakes -------------------------------------------------------------------------------


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _CountResult:
    def __init__(self, input_tokens: int) -> None:
        self.input_tokens = input_tokens


class _FakeMessages:
    """Stands in for client.messages — records call counts, validates via the real schema."""

    def __init__(self, items: list[dict], input_tokens: int, output_tokens: int) -> None:
        self._items = items
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.count_calls = 0
        self.parse_calls = 0

    def count_tokens(self, *, model, messages):  # noqa: ANN001 - test double
        self.count_calls += 1
        return _CountResult(self._input_tokens)

    def parse(self, *, model, max_tokens, messages, output_format):  # noqa: ANN001 - test double
        self.parse_calls += 1
        # output_format is the generated list wrapper; validating here mirrors the real SDK,
        # so the extractor receives genuine validated item-model instances.
        parsed = output_format(items=self._items)

        class _Response:
            pass

        response = _Response()
        response.parsed_output = parsed
        response.usage = _Usage(self._input_tokens, self._output_tokens)
        return response


class _FakeAnthropic:
    def __init__(self, items: list[dict], *, input_tokens: int = 1000, output_tokens: int = 200):
        self.messages = _FakeMessages(items, input_tokens, output_tokens)


class _FakeFetcher:
    def __init__(self, html: str) -> None:
        self._html = html

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(url=url, final_url=url, status=200, html=self._html, elapsed=0.0)

    async def aclose(self) -> None:
        return None


def _settings(**overrides) -> Settings:
    base = dict(llm_budget_usd=1.0, llm_model="claude-opus-4-8")
    base.update(overrides)
    return Settings(**base)


# --- contract ----------------------------------------------------------------------------


def test_returns_validated_schema_instances(tmp_path):
    """Same schema as the CSS extractor: Quote instances, curly quotes unwrapped at the edge."""
    items = [{"text": "“It is our choices”", "author": "J.K. Rowling", "tags": ["choices"]}]
    ext = LlmExtractor(
        Quote, settings=_settings(), client=_FakeAnthropic(items), cache_dir=tmp_path
    )
    result = ext.extract("<html><body><p>It is our choices</p></body></html>")

    assert result.valid_count == 1
    assert isinstance(result.records[0], Quote)
    assert result.records[0].text == "It is our choices"  # unwrapped — identical to CSS output
    assert ext.spent_usd > 0  # cost was accrued from the (fake) usage


def test_aborts_before_paying_when_over_budget(tmp_path):
    """A pre-call estimate over the cap raises — and parse() is never reached (no spend)."""
    fake = _FakeAnthropic([], input_tokens=1_000_000)  # ~$5 estimate
    ext = LlmExtractor(
        Quote, settings=_settings(llm_budget_usd=0.0001), client=fake, cache_dir=tmp_path
    )
    with pytest.raises(BudgetExceededError):
        ext.extract("<html>x</html>")
    assert fake.messages.parse_calls == 0  # aborted BEFORE the paid call
    assert ext.spent_usd == 0.0


def test_caches_by_content_hash(tmp_path):
    """Identical page content is served from cache — the second extract makes no API call."""
    items = [{"text": "“Cached quote”", "author": "A", "tags": []}]
    fake = _FakeAnthropic(items)
    ext = LlmExtractor(Quote, settings=_settings(), client=fake, cache_dir=tmp_path)
    html = "<html><body><p>Cached quote</p></body></html>"

    first = ext.extract(html)
    second = ext.extract(html)

    assert fake.messages.parse_calls == 1  # never paid twice
    assert first.valid_count == second.valid_count == 1


# --- self-healing ------------------------------------------------------------------------


async def test_self_healing_recovers_a_drifted_page(load_html, tmp_path):
    """Break the item selector → CSS yields 0 → the LLM fallback re-extracts the page."""
    # The LLM fallback "knows" the 20 books (sourced from a healthy CSS parse of the fixture).
    healthy = CssExtractor(Book, "article.product_pod", parse_book).extract(
        load_html("books_page1.html"), base_url=BOOKS.url
    )
    book_dicts = [b.model_dump(mode="json") for b in healthy.records]
    assert len(book_dicts) == 20

    # Serve a page whose item class was renamed — CSS matches nothing.
    broken_html = load_html("books_page1.html").replace("product_pod", "product_moved")
    llm = LlmExtractor(
        Book, settings=_settings(), client=_FakeAnthropic(book_dicts), cache_dir=tmp_path
    )
    single_page = replace(BOOKS, page_urls=(BOOKS.url,))

    with JsonlStore(tmp_path / "books.jsonl", dedup_fields=("url",)) as store:
        report = await run(
            single_page,
            fetcher=_FakeFetcher(broken_html),
            store=store,
            llm_fallback=llm,
        )

    assert report.healed_pages == 1
    assert report.valid == 20  # recovered to full yield despite the broken selector
    assert report.pages[0].healed is True
    assert report.llm_cost_usd > 0  # the recovery cost is recorded


async def test_no_healing_when_css_is_healthy(load_html, tmp_path):
    """A healthy page never touches the LLM — the fallback's parse() is not called."""
    fake = _FakeAnthropic([])
    llm = LlmExtractor(Book, settings=_settings(), client=fake, cache_dir=tmp_path)
    single_page = replace(BOOKS, page_urls=(BOOKS.url,))

    with JsonlStore(tmp_path / "books.jsonl", dedup_fields=("url",)) as store:
        report = await run(
            single_page,
            fetcher=_FakeFetcher(load_html("books_page1.html")),
            store=store,
            llm_fallback=llm,
        )

    assert report.healed_pages == 0
    assert report.valid == 20
    assert fake.messages.parse_calls == 0  # LLM never invoked on a healthy page


# --- live smoke (excluded from CI: -m "not live") ---------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not get_settings().anthropic_api_key,
    reason="ANTHROPIC_API_KEY not set — skip rather than fail a fresh clone with no .env",
)
def test_llm_extractor_live_quotes(load_html):
    """Hits the real Anthropic API. Run with `uv run pytest -m live` and a key in .env."""
    ext = LlmExtractor(Quote)  # real client, key from .env
    result = ext.extract(load_html("quotes_page1.html"))
    assert result.valid_count >= 5
    assert all(isinstance(q, Quote) for q in result.records)
    assert ext.spent_usd > 0
