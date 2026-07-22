"""Orchestrator: fetch -> extract -> validate -> dedupe -> store -> RunReport.

Day 2: sequential v1 (one page of quotes).
Day 4: async v2 — semaphore-bounded concurrency (yours) composed with rate limiter (theirs).
Day 5: self-healing fallback — CSS yield below drift threshold => retry page via LLM, flag in report.
"""

# TODO(Day 2): run(target, fetcher, extractor) v1.
