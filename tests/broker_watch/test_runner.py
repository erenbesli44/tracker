"""CLI argument handling of the broker watch runner (no database, no network)."""

from datetime import date

import pytest

from src.jobs.broker_watch import runner


def test_default_invocation_is_a_normal_run():
    args = runner._parse_args([])
    assert (args.backfill, args.since) == (False, None)


def test_backfill_requires_since_and_reads_delay_and_cap():
    args = runner._parse_args(["--backfill", "--since", "2026-01-01"])
    assert (args.backfill, args.since) == (True, date(2026, 1, 1))
    assert (args.delay, args.max_documents) == (1.0, 40)

    args = runner._parse_args(
        ["--backfill", "--since", "2026-06-01", "--delay", "0.5", "--max-documents", "5"]
    )
    assert (args.delay, args.max_documents) == (0.5, 5)


@pytest.mark.parametrize(
    "argv",
    [
        ["--backfill"],
        ["--since", "2026-01-01"],
        ["--since", "x"],
        ["--delay", "2"],  # backfill-only options without --backfill
        ["--max-documents", "5"],
    ],
)
def test_backfill_options_are_rejected_without_backfill(argv):
    with pytest.raises(SystemExit):
        runner._parse_args(argv)
