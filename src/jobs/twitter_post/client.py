"""Twitter client wrappers for posting single tweets and threads.

- TweepyClient  — real X API v2, OAuth 1.0a user context.
- DryRunClient  — logs tweets locally without posting; for dev / smoke tests.

tweepy is imported lazily so this module is importable without the dependency.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from src.config import settings

logger = logging.getLogger(__name__)

_INTER_TWEET_DELAY_SECONDS = 30  # Twitter needs time to register a tweet before it can be replied to


@dataclass
class PostedTweet:
    tweet_id: str
    url: str


@dataclass
class PostedThread:
    tweets: list[PostedTweet] = field(default_factory=list)

    @property
    def first(self) -> PostedTweet:
        return self.tweets[0]

    @property
    def tweet_ids(self) -> list[str]:
        return [t.tweet_id for t in self.tweets]


class TwitterClient(Protocol):
    def post_tweet(self, text: str) -> PostedTweet: ...
    def post_thread(self, tweets: list[str]) -> PostedThread: ...


class TweepyClient:
    def __init__(self) -> None:
        import tweepy

        missing = [
            name
            for name, val in (
                ("TWITTER_API_KEY", settings.TWITTER_API_KEY),
                ("TWITTER_API_SECRET", settings.TWITTER_API_SECRET),
                ("TWITTER_ACCESS_TOKEN", settings.TWITTER_ACCESS_TOKEN),
                ("TWITTER_ACCESS_TOKEN_SECRET", settings.TWITTER_ACCESS_TOKEN_SECRET),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing Twitter credentials: {', '.join(missing)}")

        self._client = tweepy.Client(
            consumer_key=settings.TWITTER_API_KEY,
            consumer_secret=settings.TWITTER_API_SECRET,
            access_token=settings.TWITTER_ACCESS_TOKEN,
            access_token_secret=settings.TWITTER_ACCESS_TOKEN_SECRET,
        )
        self._handle = settings.TWITTER_HANDLE or "i"

    def _create_tweet(self, text: str, reply_to: str | None = None) -> PostedTweet:
        kwargs: dict = {"text": text}
        if reply_to:
            kwargs["in_reply_to_tweet_id"] = int(reply_to)
        response = self._client.create_tweet(**kwargs)
        data = getattr(response, "data", None) or {}
        tweet_id = str(data.get("id") or "")
        if not tweet_id:
            raise RuntimeError(f"Twitter did not return a tweet id: {response}")
        return PostedTweet(
            tweet_id=tweet_id,
            url=f"https://twitter.com/{self._handle}/status/{tweet_id}",
        )

    def post_tweet(self, text: str) -> PostedTweet:
        return self._create_tweet(text)

    def post_thread(self, tweets: list[str]) -> PostedThread:
        if not tweets:
            raise ValueError("Thread must have at least one tweet")
        posted: list[PostedTweet] = []
        prev_id: str | None = None
        for i, text in enumerate(tweets):
            pt = self._create_tweet(text, reply_to=prev_id)
            posted.append(pt)
            prev_id = pt.tweet_id
            logger.info(
                "Posted thread tweet %d/%d (id=%s)", i + 1, len(tweets), pt.tweet_id
            )
            if i < len(tweets) - 1:
                time.sleep(_INTER_TWEET_DELAY_SECONDS)
        return PostedThread(tweets=posted)


class DryRunClient:
    """Logs tweet text instead of posting — for local testing."""

    def post_tweet(self, text: str) -> PostedTweet:
        logger.info("[DRY-RUN] Would tweet (%d weighted chars):\n%s", len(text), text)
        return PostedTweet(tweet_id="dryrun", url="https://twitter.com/dry-run")

    def post_thread(self, tweets: list[str]) -> PostedThread:
        logger.info("[DRY-RUN] Would post thread (%d tweets):", len(tweets))
        posted = []
        for i, text in enumerate(tweets, 1):
            logger.info(
                "  ── Tweet %d/%d (%d chars) ──\n%s\n",
                i, len(tweets), len(text), text,
            )
            posted.append(
                PostedTweet(tweet_id=f"dryrun-{i}", url=f"https://twitter.com/dry-run/{i}")
            )
        return PostedThread(tweets=posted)


def build_client(dry_run: bool | None = None) -> TwitterClient:
    use_dry_run = settings.TWITTER_DRY_RUN if dry_run is None else dry_run
    if use_dry_run:
        logger.info("Twitter client: dry-run mode")
        return DryRunClient()
    return TweepyClient()
