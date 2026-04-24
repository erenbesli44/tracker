"""Typed DTOs for the Twitter posting job."""

from dataclasses import dataclass


@dataclass
class TweetRunSummary:
    candidates_found: int = 0
    posted: int = 0
    skipped: int = 0
    failed: int = 0
