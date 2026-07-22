"""Day-0 smoke test: the package imports and the version is set.

Replaced by the real suite on Day 3 (fixtures, respx fault injection, syrupy snapshots,
contract tests, mocked-transport e2e).
"""

import scrapekit


def test_package_imports():
    assert scrapekit.__version__ == "0.1.0"
