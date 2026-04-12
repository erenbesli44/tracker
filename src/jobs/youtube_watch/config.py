"""YAML config loader for the YouTube watch job."""

from pathlib import Path

import yaml

from src.config import settings
from src.jobs.youtube_watch.schemas import WatchConfig, WatchDefaults, WatchedChannel


def load_watch_config(path: str | None = None) -> WatchConfig:
    """Load and validate watched_channels.yaml.

    Args:
        path: override the config file path; defaults to settings.WATCH_CONFIG_PATH.

    Raises:
        FileNotFoundError: config file does not exist.
        ValueError: config file is structurally invalid.
    """
    config_path = Path(path or settings.WATCH_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Watch config not found: {config_path}")

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Invalid config: root must be a YAML mapping")

    raw_defaults = raw.get("defaults") or {}
    defaults = WatchDefaults(
        video_count=int(raw_defaults.get("video_count", 5)),
        transcript_languages=list(raw_defaults.get("transcript_languages") or ["tr", "en"]),
    )

    raw_channels = raw.get("channels") or []
    if not isinstance(raw_channels, list):
        raise ValueError("Invalid config: 'channels' must be a list")

    channels: list[WatchedChannel] = []
    for i, entry in enumerate(raw_channels):
        if not isinstance(entry, dict):
            raise ValueError(f"Channel entry {i} must be a YAML mapping")

        channel_id: str | None = entry.get("youtube_channel_id") or None
        handle: str | None = entry.get("handle") or None

        if not channel_id and not handle:
            raise ValueError(
                f"Channel entry {i} must have 'youtube_channel_id' or 'handle'"
            )

        channels.append(
            WatchedChannel(
                name=str(entry.get("name") or handle or channel_id or f"channel-{i}"),
                youtube_channel_id=channel_id,
                handle=handle,
                video_count=int(entry.get("video_count", defaults.video_count)),
                transcript_languages=list(
                    entry.get("transcript_languages") or defaults.transcript_languages
                ),
            )
        )

    return WatchConfig(channels=channels, defaults=defaults)
