"""Persistence helpers for YouTube watch run logs."""

from sqlmodel import Session

from src.jobs.youtube_watch.models import YouTubeWatchChannelResult, YouTubeWatchRun
from src.models import utc_now


def create_run(session: Session) -> YouTubeWatchRun:
    run = YouTubeWatchRun()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def add_channel_result(
    session: Session,
    run_id: int,
    *,
    channel_identifier: str,
    channel_name: str,
    resolved_channel_id: str | None,
    videos_detected: int,
    videos_ingested: int,
    videos_skipped: int,
    errors_count: int,
    error_detail: str | None,
    status: str,
) -> YouTubeWatchChannelResult:
    result = YouTubeWatchChannelResult(
        run_id=run_id,
        channel_identifier=channel_identifier,
        channel_name=channel_name,
        resolved_channel_id=resolved_channel_id,
        videos_detected=videos_detected,
        videos_ingested=videos_ingested,
        videos_skipped=videos_skipped,
        errors_count=errors_count,
        error_detail=error_detail,
        status=status,
    )
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


def finalize_run(
    session: Session,
    run: YouTubeWatchRun,
    *,
    status: str,
    channels_scanned: int,
    videos_detected: int,
    videos_ingested: int,
    videos_skipped: int,
    errors_count: int,
    error_details: str | None,
) -> YouTubeWatchRun:
    run.finished_at = utc_now()
    run.status = status
    run.channels_scanned = channels_scanned
    run.videos_detected = videos_detected
    run.videos_ingested = videos_ingested
    run.videos_skipped = videos_skipped
    run.errors_count = errors_count
    run.error_details = error_details
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
