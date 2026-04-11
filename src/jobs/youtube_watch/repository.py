"""Persistence helpers for YouTube watch state and run logs."""


def list_tracked_channels() -> list[tuple[int, str]]:
    """Return list of (channel_pk, youtube_channel_id) pairs to process.

    Implementation is intentionally deferred to a later phase.
    """
    raise NotImplementedError("Repository layer is not implemented yet.")
