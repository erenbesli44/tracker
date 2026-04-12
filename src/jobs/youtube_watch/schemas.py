"""Typed schemas for YouTube watch job orchestration."""

from dataclasses import dataclass, field


# ── Config map DTOs ────────────────────────────────────────────────────────────

@dataclass
class WatchDefaults:
    video_count: int = 5
    transcript_languages: list[str] = field(default_factory=lambda: ["tr", "en"])


@dataclass
class WatchedChannel:
    """One entry from watched_channels.yaml."""

    name: str
    youtube_channel_id: str | None = None
    handle: str | None = None
    video_count: int = 5
    transcript_languages: list[str] = field(default_factory=lambda: ["tr", "en"])

    @property
    def identifier(self) -> str:
        """Best identifier to pass to the ingestion service."""
        return self.youtube_channel_id or self.handle or self.name


@dataclass
class WatchConfig:
    channels: list[WatchedChannel]
    defaults: WatchDefaults = field(default_factory=WatchDefaults)


# ── Run result DTOs ────────────────────────────────────────────────────────────

@dataclass
class ChannelJobResult:
    channel_identifier: str
    channel_name: str
    resolved_channel_id: str | None = None
    videos_detected: int = 0
    videos_ingested: int = 0
    videos_skipped: int = 0
    errors_count: int = 0
    error_detail: str | None = None


@dataclass
class JobRunSummary:
    channels_scanned: int = 0
    videos_detected: int = 0
    videos_ingested: int = 0
    videos_skipped: int = 0
    errors_count: int = 0
