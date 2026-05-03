"""Orchestration: scan DB for new video summaries and post them as threads."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session

from src.config import settings
from src.jobs.twitter_post import repository
from src.jobs.twitter_post.client import TwitterClient, build_client
from src.jobs.twitter_post.formatter import build_thread
from src.jobs.twitter_post.models import TwitterPostRun
from src.jobs.twitter_post.schemas import TweetRunSummary
from src.models import utc_now

logger = logging.getLogger(__name__)

_MAX_TOPIC_TWEETS = 5  # cap subtopic tweets per thread


@dataclass
class _TweetCandidate:
    """All data needed to build and record a tweet, pre-loaded before any API call."""
    video_id: int
    video_youtube_id: str
    title: str | None
    published_at: datetime | None
    short_summary: str
    highlights_json: str | None
    channel_name: str | None
    topic_dicts: list[dict] = field(default_factory=list)


def _load_candidates(
    session: Session,
    *,
    candidate_window: int,
) -> list[_TweetCandidate]:
    """Load unposted summaries and all enrichment data into plain objects."""
    raw = repository.find_unposted_summaries(session, last_n=candidate_window)
    candidates: list[_TweetCandidate] = []
    for video, video_summary in raw:
        channel = repository.get_channel(session, video.channel_id)
        topic_rows = repository.get_topic_mentions(session, video.id)[:_MAX_TOPIC_TWEETS]
        topic_dicts = [
            {
                "name": topic.name,
                "slug": topic.slug,
                "summary": tm.summary,
                "sentiment": tm.sentiment,
            }
            for tm, topic in topic_rows
        ]
        candidates.append(
            _TweetCandidate(
                video_id=video.id,
                video_youtube_id=video.video_id,
                title=video.title,
                published_at=video.published_at,
                short_summary=(video_summary.short_summary or "").strip(),
                highlights_json=video_summary.highlights,
                channel_name=channel.name if channel else None,
                topic_dicts=topic_dicts,
            )
        )
    return candidates


def run_once(
    engine: Engine,
    *,
    client: TwitterClient | None = None,
) -> TweetRunSummary:
    """Execute one polling cycle.

    DB connections are released between phases so the Twitter API calls
    (which can stall or retry) never hold a connection from the pool.

    Phase 1 — short session: create run record, load all candidate data.
    Phase 2 — no session: build tweet text, post to Twitter API.
    Phase 3 — short session per post: record each result immediately.
    Phase 4 — short session: finalize run record.
    """
    max_posts_per_run = max(1, int(settings.TWITTER_MAX_POSTS_PER_RUN))
    candidate_window = max(10, max_posts_per_run * 2)

    # ── Phase 1: load everything in one short session ─────────────────────
    with Session(engine) as session:
        run = repository.create_run(session)
        run_id = run.id
        candidates = _load_candidates(session, candidate_window=candidate_window)
    # DB connection released here

    summary = TweetRunSummary()
    summary.candidates_found = len(candidates)
    logger.info(
        "Twitter post run %d: %d candidate(s) (window=%d, cap=%d)",
        run_id, summary.candidates_found, candidate_window, max_posts_per_run,
    )

    if summary.candidates_found == 0:
        with Session(engine) as session:
            run_obj = session.get(TwitterPostRun, run_id)
            repository.finalize_run(
                session, run_obj,
                status="success", candidates_found=0,
                posted=0, skipped=0, failed=0, error_details=None,
            )
        logger.info("Run %d: nothing to post", run_id)
        return summary

    try:
        twitter = client or build_client()
    except Exception as exc:
        message = f"client_init: {type(exc).__name__}: {str(exc)[:300]}"
        logger.error("Twitter client init failed: %s", message)
        summary.failed = summary.candidates_found
        with Session(engine) as session:
            run_obj = session.get(TwitterPostRun, run_id)
            repository.finalize_run(
                session, run_obj,
                status="failed", candidates_found=summary.candidates_found,
                posted=0, skipped=0, failed=summary.failed,
                error_details=json.dumps([message]),
            )
        return summary

    errors: list[str] = []

    # ── Phase 2+3: post tweets; record each result in its own session ─────
    for candidate in candidates:
        if (summary.posted + summary.failed) >= max_posts_per_run:
            logger.info(
                "Run %d: reached TWITTER_MAX_POSTS_PER_RUN=%d; stopping early "
                "with %d candidate(s) deferred to next run",
                run_id, max_posts_per_run,
                len(candidates) - (summary.posted + summary.failed + summary.skipped),
            )
            break

        if not candidate.short_summary:
            logger.warning(
                "Video %s has empty short_summary — skipping", candidate.video_youtube_id
            )
            with Session(engine) as session:
                repository.record_post(
                    session, run_id=run_id, video_id=candidate.video_id,
                    status="skipped", tweet_id=None, tweet_url=None, tweet_text=None,
                    thread_tweet_ids=None, thread_length=0,
                    error_message="empty short_summary", attempt_count=0, posted_at=None,
                )
            summary.skipped += 1
            continue

        tweets = build_thread(
            title=candidate.title or "—",
            channel_name=candidate.channel_name,
            highlights_json=candidate.highlights_json,
            short_summary=candidate.short_summary,
            published_at=candidate.published_at,
            topic_mentions=candidate.topic_dicts,
        )

        # No DB connection held during the Twitter API call.
        try:
            posted = twitter.post_thread(tweets)
            first = posted.first
            with Session(engine) as session:
                repository.record_post(
                    session, run_id=run_id, video_id=candidate.video_id,
                    status="posted",
                    tweet_id=first.tweet_id,
                    tweet_url=first.url,
                    tweet_text=tweets[0],
                    thread_tweet_ids=json.dumps(posted.tweet_ids),
                    thread_length=len(posted.tweets),
                    error_message=None,
                    attempt_count=1,
                    posted_at=utc_now(),
                )
            summary.posted += 1
            logger.info(
                "Posted thread (%d tweets) for video %s (first tweet id=%s)",
                len(tweets), candidate.video_youtube_id, first.tweet_id,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.error(
                "Failed to post video %s: %s", candidate.video_youtube_id, message, exc_info=True,
            )
            with Session(engine) as session:
                repository.record_post(
                    session, run_id=run_id, video_id=candidate.video_id,
                    status="failed",
                    tweet_id=None, tweet_url=None,
                    tweet_text=tweets[0] if tweets else None,
                    thread_tweet_ids=None, thread_length=0,
                    error_message=message, attempt_count=1, posted_at=None,
                )
            summary.failed += 1
            errors.append(f"video {candidate.video_youtube_id}: {message}")

    # ── Phase 4: finalize run ─────────────────────────────────────────────
    if summary.failed == 0:
        run_status = "success"
    elif summary.posted == 0:
        run_status = "failed"
    else:
        run_status = "partial_fail"

    with Session(engine) as session:
        run_obj = session.get(TwitterPostRun, run_id)
        repository.finalize_run(
            session, run_obj,
            status=run_status, candidates_found=summary.candidates_found,
            posted=summary.posted, skipped=summary.skipped, failed=summary.failed,
            error_details=json.dumps(errors) if errors else None,
        )
    logger.info(
        "Run %d done — status=%s found=%d posted=%d skipped=%d failed=%d",
        run_id, run_status, summary.candidates_found,
        summary.posted, summary.skipped, summary.failed,
    )
    return summary
