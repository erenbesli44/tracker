"""Exception types for the YouTube watch job."""


class YouTubeWatchError(Exception):
    """Base exception for the watch job."""


class ProviderTransientError(YouTubeWatchError):
    """Recoverable provider/network error that can be retried."""


class ProviderPermanentError(YouTubeWatchError):
    """Non-retriable provider/domain error."""


class TranscriptTransientError(YouTubeWatchError):
    """Recoverable transcript fetch failure."""


class TranscriptPermanentError(YouTubeWatchError):
    """Non-retriable transcript fetch failure."""

