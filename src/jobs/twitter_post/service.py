"""Orchestration: scan DB for new video summaries and post them as threads."""

import json
import logging

from sqlmodel import Session

from src.config import settings
from src.jobs.twitter_post import repository
from src.jobs.twitter_post.client import TwitterClient, build_client
from src.jobs.twitter_post.formatter import build_thread
from src.jobs.twitter_post.schemas import TweetRunSummary
from src.models import utc_now

logger = logging.getLogger(__name__)

_MAX_TOPIC_TWEETS = 5  # cap subtopic tweets per thread


def run_once(
    session: Session,
    *,
    client: TwitterClient | None = None,
) -> TweetRunSummary:
    """Execute one polling cycle. Never raises — errors are logged on the run row."""
    run = repository.create_run(session)
    summary = TweetRunSummary()
    errors: list[str] = []

    candidates = repository.find_unposted_summaries(session, last_n=10)
    summary.candidates_found = len(candidates)
    logger.info(
        "Twitter post run %d: %d candidate(s) from last 10 videos",
        run.id, summary.candidates_found,
    )

    if summary.candidates_found == 0:
        repository.finalize_run(
            session, run,
            status="success", candidates_found=0,
            posted=0, skipped=0, failed=0, error_details=None,
        )
        logger.info("Run %d: nothing to post", run.id)
        return summary

    try:
        twitter = client or build_client()
    except Exception as exc:
        message = f"client_init: {type(exc).__name__}: {str(exc)[:300]}"
        logger.error("Twitter client init failed: %s", message)
        summary.failed = summary.candidates_found
        repository.finalize_run(
            session, run,
            status="failed", candidates_found=summary.candidates_found,
            posted=0, skipped=0, failed=summary.failed,
            error_details=json.dumps([message]),
        )
        return summary

    for video, video_summary in candidates:
        short = (video_summary.short_summary or "").strip()
        if not short:
            logger.warning("Video %s has empty short_summary — skipping", video.video_id)
            repository.record_post(
                session, run_id=run.id, video_id=video.id,
                status="skipped", tweet_id=None, tweet_url=None, tweet_text=None,
                thread_tweet_ids=None, thread_length=0,
                error_message="empty short_summary", attempt_count=0, posted_at=None,
            )
            summary.skipped += 1
            continue

        # Load enrichment data
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

        tweets = build_thread(
            title=video.title or "—",
            channel_name=channel.name if channel else None,
            highlights_json=video_summary.highlights,
            short_summary=video_summary.short_summary,
            published_at=video.published_at,
            topic_mentions=topic_dicts,
        )

        try:
            posted = twitter.post_thread(tweets)
            first = posted.first
            repository.record_post(
                session, run_id=run.id, video_id=video.id,
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
                len(tweets), video.video_id, first.tweet_id,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.error(
                "Failed to post video %s: %s", video.video_id, message, exc_info=True,
            )
            repository.record_post(
                session, run_id=run.id, video_id=video.id,
                status="failed",
                tweet_id=None, tweet_url=None,
                tweet_text=tweets[0] if tweets else None,
                thread_tweet_ids=None, thread_length=0,
                error_message=message, attempt_count=1, posted_at=None,
            )
            summary.failed += 1
            errors.append(f"video {video.video_id}: {message}")

    if summary.failed == 0:
        run_status = "success"
    elif summary.posted == 0:
        run_status = "failed"
    else:
        run_status = "partial_fail"

    repository.finalize_run(
        session, run,
        status=run_status, candidates_found=summary.candidates_found,
        posted=summary.posted, skipped=summary.skipped, failed=summary.failed,
        error_details=json.dumps(errors) if errors else None,
    )
    logger.info(
        "Run %d done — status=%s found=%d posted=%d skipped=%d failed=%d",
        run.id, run_status, summary.candidates_found,
        summary.posted, summary.skipped, summary.failed,
    )
    return summary
