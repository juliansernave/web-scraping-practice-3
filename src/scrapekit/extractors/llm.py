"""LLM schema-guided extraction: html -> trimmed text -> client.messages.parse(output_format=Model).

Day 5. Must include: token pre-count + cost estimate per call, hard USD budget cap from
config (abort when exceeded), response cache keyed by content hash (never pay twice).
The Anthropic client is injected as a dependency so tests can mock it.

The point of this extractor is that it validates against the *same* pydantic model the CSS
extractor produces — Claude reads the page by meaning, not by CSS class, so it survives markup
that would break selectors. That resilience is what the Day-5 self-healing fallback trades cost
for: cents per page vs. re-writing selectors by hand.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import anthropic
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError, create_model

from scrapekit.config import Settings, get_settings
from scrapekit.extractors.base import ExtractionResult, ItemError
from scrapekit.logging import get_logger

log = get_logger(__name__)


class BudgetExceededError(Exception):
    """Raised before a call whose estimated cost would push spend over the configured cap.

    Aborting *before* the call (not after) is the point: the hard cap is a pre-flight check, so
    the crawl can never spend past the budget even by one call.
    """

    def __init__(self, spent: float, estimate: float, budget: float) -> None:
        self.spent = spent
        self.estimate = estimate
        self.budget = budget
        super().__init__(
            f"LLM budget ${budget:.2f} would be exceeded: "
            f"spent ${spent:.4f} + estimate ${estimate:.4f}"
        )


_EXTRACTION_PROMPT = (
    "Extract every record from the page text below into the given schema. "
    "Return all items you can find; do not invent fields that aren't present. "
    "Page text:\n\n{text}"
)


class LlmExtractor:
    """Extract records via a schema-guided Anthropic call, validated against ``item_model``.

    Same :class:`Extractor` protocol as :class:`CssExtractor`, so it drops into the pipeline
    unchanged. Constructed with the item model; a list wrapper is built so one call returns all
    records on the page.
    """

    def __init__(
        self,
        item_model: type[BaseModel],
        *,
        settings: Settings | None = None,
        client: anthropic.Anthropic | None = None,
        cache_dir: str | Path = "data/llm_cache",  # under data/ (gitignored) — it's derived cache
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        self._item_model = item_model
        # Wrap the item model in a list so a single parse() call yields the whole page.
        self._list_model = create_model(f"{item_model.__name__}List", items=(list[item_model], ...))
        self._cache_dir = Path(cache_dir)
        self.spent_usd = 0.0  # cumulative actual cost, surfaced in the RunReport

    # --- helpers ---------------------------------------------------------------------------
    @staticmethod
    def _html_to_text(html: str) -> str:
        """Strip scripts/styles and collapse to text — tokens are money, markup is noise."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)

    def _cache_path(self, digest: str) -> Path:
        return self._cache_dir / f"{self._item_model.__name__}_{digest}.json"

    def _estimate_usd(self, input_tokens: int) -> float:
        """Worst-case pre-call estimate: measured input + a full max_tokens output."""
        s = self._settings
        return (
            input_tokens / 1_000_000 * s.llm_input_usd_per_mtok
            + s.llm_max_output_tokens / 1_000_000 * s.llm_output_usd_per_mtok
        )

    def _load_cache(self, digest: str) -> list[dict[str, Any]] | None:
        path = self._cache_path(digest)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _save_cache(self, digest: str, payloads: list[dict[str, Any]]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(digest).write_text(
            json.dumps(payloads, ensure_ascii=False), encoding="utf-8"
        )

    def _to_result(self, payloads: list[dict[str, Any]]) -> ExtractionResult:
        """Validate raw dicts into item models, collecting per-item failures like the CSS path."""
        result: ExtractionResult = ExtractionResult()
        for index, payload in enumerate(payloads):
            try:
                result.records.append(self._item_model.model_validate(payload))
            except ValidationError as exc:
                reason = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                )
                result.errors.append(ItemError(index=index, error=reason, raw=payload))
        return result

    # --- the extraction --------------------------------------------------------------------
    def extract(self, html: str, *, base_url: str | None = None) -> ExtractionResult:
        text = self._html_to_text(html)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        cached = self._load_cache(digest)
        if cached is not None:
            # Never pay twice: identical page content served from cache at $0.
            log.info("llm.cache_hit", digest=digest[:12], records=len(cached))
            return self._to_result(cached)

        messages = [{"role": "user", "content": _EXTRACTION_PROMPT.format(text=text)}]

        # Pre-count tokens and gate on the budget *before* spending anything.
        pre = self._client.messages.count_tokens(model=self._settings.llm_model, messages=messages)
        estimate = self._estimate_usd(pre.input_tokens)
        if self.spent_usd + estimate > self._settings.llm_budget_usd:
            raise BudgetExceededError(self.spent_usd, estimate, self._settings.llm_budget_usd)
        log.info(
            "llm.cost_estimate",
            input_tokens=pre.input_tokens,
            estimate_usd=round(estimate, 4),
            spent_usd=round(self.spent_usd, 4),
            budget_usd=self._settings.llm_budget_usd,
        )

        # No `thinking`: structured extraction is simple, and thinking tokens are pure cost here.
        response = self._client.messages.parse(
            model=self._settings.llm_model,
            max_tokens=self._settings.llm_max_output_tokens,
            messages=messages,
            output_format=self._list_model,
        )

        actual = (
            response.usage.input_tokens / 1_000_000 * self._settings.llm_input_usd_per_mtok
            + response.usage.output_tokens / 1_000_000 * self._settings.llm_output_usd_per_mtok
        )
        self.spent_usd += actual

        items = list(response.parsed_output.items)
        payloads = [item.model_dump(mode="json") for item in items]
        self._save_cache(digest, payloads)

        log.info(
            "llm.extracted",
            records=len(items),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=round(actual, 4),
            spent_usd=round(self.spent_usd, 4),
        )
        return ExtractionResult(records=items, errors=[])
