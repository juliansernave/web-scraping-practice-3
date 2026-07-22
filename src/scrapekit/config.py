"""Central settings via pydantic-settings: rate limits, timeouts, LLM model id + budget cap.

Day 1: rate-limit/retry/timeout settings. Day 5: LLM model id, USD budget cap.
Reads .env automatically (see .env.example).
"""

# TODO(Day 1): class Settings(BaseSettings) with requests_per_second, max_retries,
# timeout_seconds; TODO(Day 5): anthropic model id + llm_budget_usd hard cap.
