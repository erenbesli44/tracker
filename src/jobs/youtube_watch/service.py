"""Orchestration service for one YouTube watch run."""

import json
import logging

from fastapi import HTTPException
from sqlmodel import Session

from src.config import settings
from src.ingestion import service as ingestion_service
from src.ingestion.schemas import IngestionYoutubeChannelRunRequest
from src.jobs.youtube_watch import repository
from src.jobs.youtube_watch.schemas import (
    ChannelJobResult,
    JobRunSummary,
    WatchConfig,
    WatchedChannel,
)
from src.videos import service as videos_service

logger = logging.getLogger(__name__)


def run_once(session: Session, config: WatchConfig) -> JobRunSummary:
    """Execute one full polling cycle across all configured channels."""
    run = repository.create_run(session)
    proxy_source = "webshare-api" if settings.WEBSHARE_API_KEY else "env-static"
    proxy_count = len(videos_service._direct_proxy_targets()) if settings.YOUTUBE_PROXY_ENABLED else 0
    logger.info(
        "YouTube proxy enabled=%s mode=%s source=%s proxies=%d",
        settings.YOUTUBE_PROXY_ENABLED,
        settings.YOUTUBE_PROXY_MODE,
        proxy_source,
        proxy_count,
    )
    logger.info(
        "YouTube watch run started (id=%d, channels=%d)",
        run.id,
        len(config.channels),
    )

    summary = JobRunSummary()
    channel_errors: list[str] = []

    for watched in config.channels:
        logger.info("Processing channel: %s (%s)", watched.name, watched.identifier)
        result = _process_channel(session, watched)

        summary.channels_scanned += 1
        summary.videos_detected += result.videos_detected
        summary.videos_ingested += result.videos_ingested
        summary.videos_skipped += result.videos_skipped
        summary.errors_count += result.errors_count

        if result.errors_count > 0 and result.videos_ingested == 0:
            ch_status = "failed"
        elif result.errors_count > 0:
            ch_status = "partial"
        else:
            ch_status = "success"

        if result.error_detail:
            channel_errors.append(f"{watched.name}: {result.error_detail}")

        repository.add_channel_result(
            session,
            run.id,
            channel_identifier=result.channel_identifier,
            channel_name=result.channel_name,
            resolved_channel_id=result.resolved_channel_id,
            videos_detected=result.videos_detected,
            videos_ingested=result.videos_ingested,
            videos_skipped=result.videos_skipped,
            errors_count=result.errors_count,
            error_detail=result.error_detail,
            status=ch_status,
        )

        logger.info(
            "Channel %s — detected=%d ingested=%d skipped=%d errors=%d",
            watched.name,
            result.videos_detected,
            result.videos_ingested,
            result.videos_skipped,
            result.errors_count,
        )

    if summary.errors_count == 0:
        run_status = "success"
    elif summary.videos_ingested == 0 and summary.channels_scanned > 0:
        run_status = "failed"
    else:
        run_status = "partial_fail"

    repository.finalize_run(
        session,
        run,
        status=run_status,
        channels_scanned=summary.channels_scanned,
        videos_detected=summary.videos_detected,
        videos_ingested=summary.videos_ingested,
        videos_skipped=summary.videos_skipped,
        errors_count=summary.errors_count,
        error_details=json.dumps(channel_errors) if channel_errors else None,
    )

    logger.info(
        "Run %d finished — status=%s channels=%d detected=%d ingested=%d skipped=%d errors=%d",
        run.id,
        run_status,
        summary.channels_scanned,
        summary.videos_detected,
        summary.videos_ingested,
        summary.videos_skipped,
        summary.errors_count,
    )

    return summary


def _process_channel(session: Session, watched: WatchedChannel) -> ChannelJobResult:
    """Run the ingestion pipeline for one channel; never raises."""
    try:
        request = IngestionYoutubeChannelRunRequest(
            youtube_channel=watched.identifier,
            video_count=watched.video_count,
            transcript_languages=watched.transcript_languages,
        )
        result = ingestion_service.ingest_youtube_channel(session, request)

        videos_skipped = result.videos_skipped_existing + result.videos_skipped_no_transcript
        error_detail: str | None = None
        if result.errors_count > 0:
            error_detail = f"{result.errors_count} video(s) failed ingestion"

        return ChannelJobResult(
            channel_identifier=watched.identifier,
            channel_name=watched.name,
            resolved_channel_id=result.youtube_channel_id,
            videos_detected=result.videos_detected,
            videos_ingested=result.videos_ingested,
            videos_skipped=videos_skipped,
            errors_count=result.errors_count,
            error_detail=error_detail,
        )

    except HTTPException as exc:
        logger.error(
            "Channel %s failed (HTTP %d): %s",
            watched.identifier,
            exc.status_code,
            exc.detail,
        )
        return ChannelJobResult(
            channel_identifier=watched.identifier,
            channel_name=watched.name,
            errors_count=1,
            error_detail=str(exc.detail)[:500],
        )

    except Exception as exc:
        logger.error("Channel %s failed: %s", watched.identifier, exc, exc_info=True)
        return ChannelJobResult(
            channel_identifier=watched.identifier,
            channel_name=watched.name,
            errors_count=1,
            error_detail=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
