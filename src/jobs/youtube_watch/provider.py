"""YouTube source provider abstraction for channel polling."""

from collections.abc import Sequence

from src.jobs.youtube_watch.schemas import ProviderVideoItem


def list_recent_videos(channel_id: str, limit: int = 10) -> Sequence[ProviderVideoItem]:
    """Return recent videos for a channel.

    Implementation is intentionally deferred to a later phase.
    """
    raise NotImplementedError("Provider integration is not implemented yet.")

