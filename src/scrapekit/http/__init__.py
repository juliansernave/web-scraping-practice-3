"""Resilient HTTP layer: retrying client, robots.txt enforcement, polite headers. (Day 1)"""

from scrapekit.http.client import ResilientClient, RetryableStatusError
from scrapekit.http.headers import HeaderPool, next_headers
from scrapekit.http.robots import RobotsDisallowedError, RobotsPolicy

__all__ = [
    "ResilientClient",
    "RetryableStatusError",
    "RobotsDisallowedError",
    "RobotsPolicy",
    "HeaderPool",
    "next_headers",
]
