"""Extractor protocol: extract(html) -> ExtractionResult(valid records, per-item errors).

Day 2. Validation errors are collected (structured log events + counters), never raised —
one bad record must not abort a crawl.
"""

# TODO(Day 2): ExtractionResult + Extractor Protocol class.
