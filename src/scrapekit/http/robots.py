"""robots.txt as an enforced contract: fetch + parse with protego, refuse disallowed URLs,
feed Crawl-delay into the rate limiter.

Day 1. protego is what Scrapy uses — handles wildcards and Crawl-delay correctly.
"""

# TODO(Day 1): async is_allowed(url, user_agent) and crawl_delay(url) helpers, cached per host.
