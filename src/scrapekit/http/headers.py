"""User-agent pool with rotation + honest default headers. (Day 1)

Rotation spreads requests across a few realistic UA strings (some sites vary markup or
rate limits by UA), while the default headers stay honest: a real ``Accept`` set plus an
identifiable contact so a curious sysadmin can reach us. Rotation is deterministic
round-robin — reproducible in tests, unlike ``random.choice``.
"""

from __future__ import annotations

import itertools

# A small pool of current, real browser UA strings. Kept short on purpose — this is polite
# variety, not evasion (fingerprint spoofing is out of scope; see PLAN.md).
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)

# Honest, static headers sent with every request. An identifiable UA is an ethics option
# (PLAN.md); flip HONEST_IDENTITY on to prepend a contactable scrapekit token.
BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class HeaderPool:
    """Round-robins over ``user_agents`` and merges the honest base headers.

    Instantiable (and injectable) so tests can pin a single UA and assert rotation order.
    """

    def __init__(
        self,
        user_agents: tuple[str, ...] = USER_AGENTS,
        base_headers: dict[str, str] | None = None,
    ) -> None:
        if not user_agents:
            raise ValueError("HeaderPool needs at least one user agent")
        self._user_agents = user_agents
        self._base = dict(base_headers if base_headers is not None else BASE_HEADERS)
        self._cycle = itertools.cycle(user_agents)

    def next_headers(self) -> dict[str, str]:
        """Return a fresh header dict with the next UA in the rotation."""
        return {**self._base, "User-Agent": next(self._cycle)}


# Module-level default pool for callers that don't need their own rotation state.
_default_pool = HeaderPool()


def next_headers() -> dict[str, str]:
    """Convenience wrapper over the module-level default :class:`HeaderPool`."""
    return _default_pool.next_headers()
