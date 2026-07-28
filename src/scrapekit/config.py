"""Central settings via pydantic-settings: rate limits, timeouts, LLM model id + budget cap.

Day 1: rate-limit/retry/timeout settings. Day 5: LLM model id, USD budget cap.
Reads .env automatically (see .env.example). All fields override from the environment with
the ``SCRAPEKIT_`` prefix, e.g. ``SCRAPEKIT_REQUESTS_PER_SECOND=5``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime knobs for the resilient HTTP layer (Day 1)."""

    model_config = SettingsConfigDict(
        env_prefix="SCRAPEKIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate Day-5 keys (ANTHROPIC_API_KEY, LLM budget) not modelled yet
    )

    # --- Rate limiting (their politeness policy) -------------------------------------------
    requests_per_second: float = Field(
        default=1.0,
        gt=0,
        description="Default per-host token-bucket rate when robots.txt is silent.",
    )

    # --- Retry / backoff (recover from transient failures) ---------------------------------
    max_attempts: int = Field(
        default=5, ge=1, description="Total attempts (1 initial + retries) before giving up."
    )
    backoff_initial_seconds: float = Field(
        default=0.5, gt=0, description="Base delay for the first backoff before exponential growth."
    )
    backoff_max_seconds: float = Field(
        default=30.0, gt=0, description="Ceiling for a single backoff wait."
    )

    # --- Timeouts (never hang forever) -----------------------------------------------------
    timeout_seconds: float = Field(
        default=10.0, gt=0, description="Per-request timeout applied to connect/read/write/pool."
    )

    # --- Identity / network ----------------------------------------------------------------
    proxy_url: str | None = Field(
        default=None, description="Optional outbound proxy, e.g. http://user:pass@host:port."
    )

    # --- LLM extraction (Day 5) ------------------------------------------------------------
    # Keeping the model id and prices in config makes cost/quality a *configuration* decision,
    # not a code change: swap to a cheaper model for volume or a bigger one for messy pages.
    llm_model: str = Field(
        default="claude-opus-4-8", description="Anthropic model id for LLM extraction."
    )
    llm_budget_usd: float = Field(
        default=1.0,
        ge=0,
        description="Hard USD cap per extractor; a call that would exceed it aborts.",
    )
    llm_max_output_tokens: int = Field(
        default=4096, gt=0, description="max_tokens for an extraction call (a page of records)."
    )
    # Opus 4.8 list price ($/million tokens). Used for the pre-call estimate and the actual
    # post-call cost; update alongside llm_model so the run report's cost line stays honest.
    llm_input_usd_per_mtok: float = Field(default=5.0, ge=0)
    llm_output_usd_per_mtok: float = Field(default=25.0, ge=0)

    # The Anthropic SDK reads ANTHROPIC_API_KEY from the process env; we also surface it here
    # (unprefixed alias, so it's read as ANTHROPIC_API_KEY, not SCRAPEKIT_ANTHROPIC_API_KEY)
    # and pass it explicitly to the client — so the .env pydantic-settings already loads is the
    # single source of truth, with no dependence on the SDK's own env lookup.
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )

    @field_validator("proxy_url", "anthropic_api_key", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        # A commented-out .env line left as ``KEY=`` reads as "" — treat that as unset, so an
        # empty proxy doesn't break the httpx client and an empty key falls back to SDK lookup.
        return value or None


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so .env is read once; call ``get_settings.cache_clear()`` in tests."""
    return Settings()
