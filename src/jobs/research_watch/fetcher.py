"""HTTP access for the research watch job: direct requests (no proxy, owner rule 2026-11-09)
with the configured User-Agent, robots.txt per host and a courtesy delay between requests.

robots.txt handling is the broker watch rule (RFC 9309): missing (4xx) allows, unreachable or
5xx disallows the host for this run. Tests pass an ``httpx.MockTransport``.
"""

import logging
import time
from urllib.parse import urlsplit

import httpx

from src.jobs.broker_watch.robots import RobotsRules

logger = logging.getLogger(__name__)

MAX_PAGE_BYTES = 5 * 1024 * 1024  # the listing page is ~0.4 MB, an article ~50 kB


class RobotsDisallowed(Exception):
    """The host's robots.txt forbids this URL for our User-Agent."""


class HttpFetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        request_delay: float = 1.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._request_delay = request_delay
        self.requests_sent = 0
        self._robots: dict[str, RobotsRules | None] = {}
        # trust_env=False: never pick up a proxy from the environment.
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            trust_env=False,
            event_hooks={"request": [self._request_hook]},
        )

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._client.close()

    def get_text(self, url: str) -> str:
        response = self._client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_PAGE_BYTES:
            raise ValueError(f"page too large ({len(response.content)} bytes): {url}")
        return response.text

    def _request_hook(self, request: httpx.Request) -> None:
        if not request.url.path.endswith("/robots.txt"):
            self._check_robots(str(request.url))
        if self._request_delay and self.requests_sent:
            time.sleep(self._request_delay)
        self.requests_sent += 1

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.netloc not in self._robots:
            self._robots[parts.netloc] = None  # requests made while loading are not re-checked
            self._robots[parts.netloc] = self._load_robots(
                f"{parts.scheme}://{parts.netloc}/robots.txt"
            )
        rules = self._robots[parts.netloc]
        if rules is not None and not rules.can_fetch(url):
            raise RobotsDisallowed(url)

    def _load_robots(self, robots_url: str) -> RobotsRules | None:
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt unreachable, host disallowed: %s (%s)", robots_url, exc)
            return RobotsRules.disallow_all()
        if response.status_code >= 500:
            logger.warning("robots.txt %d, host disallowed: %s", response.status_code, robots_url)
            return RobotsRules.disallow_all()
        if response.status_code >= 400:
            return None
        return RobotsRules(response.text, self._user_agent)
