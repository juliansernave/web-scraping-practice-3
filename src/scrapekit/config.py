"""Central settings via pydantic-settings: rate limits, timeouts, LLM model id + budget cap.

Day 1: rate-limit/retry/timeout settings. Day 5: LLM model id, USD budget cap.
Reads .env automatically (see .env.example). All fields override from the environment with
the ``SCRAPEKIT_`` prefix, e.g. ``SCRAPEKIT_REQUESTS_PER_SECOND=5``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so .env is read once; call ``get_settings.cache_clear()`` in tests."""
    return Settings()
