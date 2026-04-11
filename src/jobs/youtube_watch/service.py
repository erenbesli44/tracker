"""Orchestration service for one YouTube watch run."""

from src.jobs.youtube_watch.schemas import JobRunSummary


def run_once() -> JobRunSummary:
    """Execute one polling cycle.

    Implementation is intentionally deferred to a later phase.
    """
    raise NotImplementedError("YouTube watch job service is not implemented yet.")

