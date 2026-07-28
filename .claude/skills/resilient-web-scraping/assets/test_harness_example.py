"""Worked examples of the network-free testing pyramid (references/testing-pyramid.md).

Uses pytest + respx (an httpx transport mock) + pytest-asyncio as concrete tools — swap
for whatever your project's stack uses (nock for a Node/axios client, responses for
requests, etc). The point is the *shape* of each test — what it injects, what it
asserts — not these specific libraries.

These assume `ResilientClient` from resilient_client.py and the `Fetcher`/`Extractor`/
`Target` shapes from protocols.py and target_registry.py. Adapt imports to your project's
actual module layout.
"""

from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest
from httpx import Response
from pydantic import BaseModel, ValidationError

# from your_project.resilient_client import ClientSettings, ResilientClient, RetryableStatusError
# from your_project.protocols import ExtractionResult, FetchResult, ItemError
# from your_project.pipeline import run  # your top-level orchestrator

URL = "https://example.test/data"


def _fast_settings(**overrides):
    """Settings tuned so retries/backoff are near-instant — test the logic, not the wall clock."""
    base = dict(
        max_attempts=5,
        backoff_initial_seconds=0.001,
        backoff_max_seconds=0.005,
        requests_per_second=1000.0,
        timeout_seconds=5.0,
    )
    base.update(overrides)
    return base  # construct your actual ClientSettings(**base) here


# --- Layer 2: HTTP fault injection --------------------------------------------------------


async def test_retries_then_succeeds(respx_mock):
    """[429, 429, 200] -> three attempts, final success."""
    route = respx_mock.get(URL).mock(
        side_effect=[Response(429), Response(429), Response(200, text="ok")]
    )
    # async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
    #     resp = await client.fetch(URL)
    # assert resp.status_code == 200
    assert route  # placeholder — wire up your real client import above


async def test_gives_up_after_max_attempts(respx_mock):
    """A permanently-500 endpoint gives up after exactly max_attempts and re-raises."""
    route = respx_mock.get(URL).mock(return_value=Response(500))
    # async with ResilientClient(settings=_fast_settings(max_attempts=5), enforce_robots=False) as c:
    #     with pytest.raises(RetryableStatusError):
    #         await c.fetch(URL)
    assert route.call_count == 0  # replace with real assertion once wired up


async def test_does_not_retry_404(respx_mock):
    """404 is a client error — retrying can't help, so it fails on the first attempt."""
    route = respx_mock.get(URL).mock(return_value=Response(404))
    # async with ResilientClient(settings=_fast_settings(), enforce_robots=False) as client:
    #     with pytest.raises(httpx.HTTPStatusError):
    #         await client.fetch(URL)
    # assert route.call_count == 1
    assert route


async def test_retries_transport_error(respx_mock):
    """A connection error is transient (network blip) — retry it, then succeed."""
    respx_mock.get(URL).mock(side_effect=[httpx.ConnectError("boom"), Response(200, text="ok")])


async def test_honors_retry_after_header(respx_mock):
    """When the server sends Retry-After, take that path instead of computed backoff."""
    respx_mock.get(URL).mock(
        side_effect=[Response(429, headers={"Retry-After": "0"}), Response(200, text="ok")]
    )


async def test_rate_limiter_spaces_requests(respx_mock):
    """With requests_per_second=2, the 3rd of 3 rapid requests waits for a token refill.

    Lower-bound assertion only (CI can be slower, never faster) — proves the limiter
    actually throttled instead of letting every request through immediately.
    """
    respx_mock.get(URL).mock(return_value=Response(200, text="x"))
    start = time.perf_counter()
    # ... fire 3 requests through a client configured with requests_per_second=2.0 ...
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.0  # replace with `>= 0.4` once wired to a real rate-limited client


# --- Layer 3: schema contract tests --------------------------------------------------------


class ExampleRecord(BaseModel):
    title: str
    price: str
    model_config = {"extra": "forbid"}


GOOD_PAYLOAD = {"title": "Example Item", "price": "19.99"}

BAD_PAYLOADS = {
    "empty-title": {**GOOD_PAYLOAD, "title": ""},
    "missing-price": {"title": "Example Item"},
    "extra-field": {**GOOD_PAYLOAD, "surprise": 1},  # rejected because extra="forbid"
}


def test_accepts_valid_payload():
    record = ExampleRecord.model_validate(GOOD_PAYLOAD)
    assert record.title == "Example Item"


@pytest.mark.parametrize("payload", BAD_PAYLOADS.values(), ids=list(BAD_PAYLOADS))
def test_rejects_bad_payload(payload):
    with pytest.raises(ValidationError):
        ExampleRecord.model_validate(payload)


# --- Layer 4: drift tests -------------------------------------------------------------------
#
# Real versions of these mutate a frozen HTML fixture (e.g. load_html("page1.html")) rather
# than constructing HTML inline — shown minimally here to keep the example self-contained.

HEALTHY_HTML = """
<div class="item"><span class="title">A</span><span class="price">1.00</span></div>
<div class="item"><span class="title">B</span><span class="price">2.00</span></div>
"""


def _extract(html: str, item_selector: str, price_class: str):
    """Stand-in extractor: real code uses BeautifulSoup/lxml + your Extractor implementation."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(item_selector)
    valid = 0
    for el in elements:
        price_el = el.select_one(f".{price_class}")
        if price_el is not None:
            valid += 1
    total = len(elements)
    return valid, total


def test_no_drift_on_healthy_page():
    valid, total = _extract(HEALTHY_HTML, ".item", "price")
    assert total == 2
    assert valid == 2  # yield_rate == 1.0, no drift


def test_drift_alert_when_item_class_renamed():
    """Rename the item container class: the selector now matches nothing -> rate 0 -> alert."""
    drifted = HEALTHY_HTML.replace("item", "product")
    valid, total = _extract(drifted, ".item", "price")  # selector still looks for old class
    assert total == 0  # nothing matched -> extraction rate 0.0 -> drift alert should fire


def test_drift_alert_when_field_class_renamed():
    """Items still match, but the price node is gone -> every item fails to parse -> alert."""
    drifted = HEALTHY_HTML.replace("price", "cost")
    valid, total = _extract(drifted, ".item", "price")
    assert total == 2  # the item selector still matches...
    assert valid == 0  # ...but the field selector inside each item does not


# --- Layer 5: pipeline end-to-end, without network -----------------------------------------


class FakeFetcher:
    """Serves one fixed HTML string regardless of URL — no browser, no network.

    This is the fake that Pattern 2's dependency injection exists to enable: inject it
    wherever a real Fetcher would go, and the entire pipeline runs deterministically.
    """

    def __init__(self, html: str) -> None:
        self._html = html

    async def fetch(self, url: str):
        # return FetchResult(url=url, final_url=url, status=200, html=self._html, elapsed=0.0)
        return {"url": url, "final_url": url, "status": 200, "html": self._html, "elapsed": 0.0}

    async def aclose(self) -> None:
        return None


async def test_pipeline_end_to_end_no_network(tmp_path):
    """Wire a fake fetcher + real extraction/validation/storage through the real pipeline.

    Assert on the final report (pages fetched, valid/invalid, stored, duplicates) rather
    than on any intermediate stage — this is what proves the pieces compose correctly.
    """
    fetcher = FakeFetcher(HEALTHY_HTML)
    # with JsonlStore(tmp_path / "out.jsonl") as store:
    #     report = await run(YOUR_TARGET, fetcher=fetcher, store=store)
    # assert report.pages_fetched == 1
    # assert report.valid == 2
    assert fetcher is not None  # placeholder until wired to your real pipeline


async def test_dedup_idempotent_across_runs(tmp_path):
    """Running the same fixture through the pipeline twice should not double-store records."""
    # first = await _run_once(tmp_path)
    # second = await _run_once(tmp_path)
    # assert second.duplicates == second.records_extracted  # nothing new the second time
    pass


# --- Keep exactly one test behind a "live" marker ------------------------------------------
#
# Register a `live` marker in your pytest config (pyproject.toml: markers = ["live: hits
# real network/API — excluded from CI by default"]) and run everything else with
# `pytest -m "not live"` in CI. This is the one test allowed to hit the real world.


@pytest.mark.live
async def test_real_extractor_against_live_api():
    """A manual sanity check that your fakes still match reality. Not part of the default run."""
    pytest.skip("wire this up to a real call before relying on it — shown as a placeholder")
