"""One-off script: run the ingestion pipeline for all channels, including already-existing videos.

Usage:
    python -m scripts.force_full_run

Replicates the ingest_youtube_channel loop but removes the "skip if already in DB" check,
so every video in each channel's recent list goes through the full pipeline
(transcript → LLM → summary → classification) with overwrite=True.
No production code files are modified.
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    import src.persons.models  # noqa: F401
    import src.channels.models  # noqa: F401
    import src.videos.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401

    from src.database import create_db_and_tables
    create_db_and_tables()


def _process_channel_force(session, watched) -> tuple[int, int, int]:
    """Run the full pipeline for a channel without skipping existing videos.

    Returns (ingested, skipped_no_transcript, errors).
    """
    from src.ingestion import service as ingestion_service
    from src.ingestion.schemas import IngestionYoutubeRequest
    from src.videos import service as videos_service

    channel_id = ingestion_service._resolve_youtube_channel_id(watched.identifier)
    playlist_info = ingestion_service._list_recent_channel_videos(channel_id, watched.video_count)
    candidates = playlist_info.candidates

    logger.info(
        "Channel %s — found %d candidate(s)", watched.name, len(candidates)
    )

    person_hint = None
    if playlist_info.channel_name or playlist_info.channel_handle:
        person_hint = {
            "name": playlist_info.channel_name,
            "platform_handle": playlist_info.channel_handle,
        }

    ingested = 0
    skipped_no_transcript = 0
    errors = 0

    for candidate in candidates:
        logger.info("  Processing video_id=%s", candidate.video_id)

        try:
            fetched_transcript = videos_service.fetch_transcript_from_youtube(
                candidate.video_id,
                watched.transcript_languages,
            )
        except videos_service.YouTubeTranscriptFetchError as exc:
            logger.warning(
                "  Transcript unavailable for video_id=%s (%s): %s",
                candidate.video_id, exc.code, exc.detail,
            )
            skipped_no_transcript += 1
            errors += 1
            continue

        payload = IngestionYoutubeRequest(
            person=person_hint,
            video={
                "video_url": candidate.video_url,
                "title": candidate.title,
                "published_at": candidate.published_at,
            },
            transcript={
                "raw_text": fetched_transcript["full_text"],
                "language": str(fetched_transcript.get("language", "tr")),
                "segments": fetched_transcript.get("segments"),
            },
            overwrite={"transcript": True, "summary": True, "classification": True},
        )

        try:
            result = ingestion_service.ingest_youtube(session, payload)
            logger.info(
                "  video_id=%s — actions=%s mentions=%d",
                candidate.video_id,
                result.actions,
                result.classification_mentions,
            )
            ingested += 1
        except Exception as exc:
            logger.warning("  video_id=%s failed: %s", candidate.video_id, exc)
            errors += 1

    return ingested, skipped_no_transcript, errors


def main() -> int:
    from src.jobs.youtube_watch.config import load_watch_config

    try:
        config = load_watch_config()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Config error: %s", exc)
        return 2

    if not config.channels:
        logger.warning("No channels configured.")
        return 0

    logger.info("Loaded %d channel(s)", len(config.channels))

    try:
        _bootstrap()
    except Exception as exc:
        logger.error("Bootstrap failed: %s", exc, exc_info=True)
        return 2

    from src.database import engine
    from sqlmodel import Session

    total_ingested = 0
    total_errors = 0

    with Session(engine) as session:
        for watched in config.channels:
            logger.info("=== Channel: %s (%s) ===", watched.name, watched.identifier)
            try:
                ingested, skipped_no_transcript, errors = _process_channel_force(session, watched)
                total_ingested += ingested
                total_errors += errors
                logger.info(
                    "  Done — ingested=%d skipped_no_transcript=%d errors=%d",
                    ingested, skipped_no_transcript, errors,
                )
            except Exception as exc:
                logger.error("Channel %s failed entirely: %s", watched.name, exc, exc_info=True)
                total_errors += 1

    logger.info(
        "All done — total_ingested=%d total_errors=%d",
        total_ingested, total_errors,
    )
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
