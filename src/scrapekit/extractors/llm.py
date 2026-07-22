"""LLM schema-guided extraction: html -> trimmed text -> client.messages.parse(output_format=Model).

Day 5. Must include: token pre-count + cost estimate per call, hard USD budget cap from
config (abort when exceeded), response cache keyed by content hash (never pay twice).
The Anthropic client is injected as a dependency so tests can mock it.
"""

# TODO(Day 5): LlmExtractor implementing the Extractor protocol.
