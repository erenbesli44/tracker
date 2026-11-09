"""Digest validation and the mechanical checks against the 2026-11-04 post."""

import pytest

from src.jobs.research_watch.digest import DigestFormatError, check_digest, validate_digest
from src.jobs.research_watch.parsers import parse_tcmb_article
from tests.research_watch.helpers import GOOD_DIGEST, read

BODY = parse_tcmb_article(read("article_2026_11_04.html")).body_text


def _digest(**changes) -> dict:
    return validate_digest({**GOOD_DIGEST, **changes})


def test_good_digest_passes_and_unknown_topics_are_dropped():
    digest = _digest()
    assert digest["topics"] == ["faiz-para-politikasi"]
    assert check_digest(digest, BODY) == []


def test_invented_or_rounded_number_is_flagged():
    findings = [*GOOD_DIGEST["findings"][:2], "Firmaların 120 milyar TL'si fonlarda."]
    problems = check_digest(_digest(findings=findings), BODY)
    assert problems == ["numbers not in source: 120"]


def test_number_inside_a_longer_number_does_not_count_as_present():
    # "1,2" is in the source; "1" alone must match a standalone 1 and "2,5" must not match.
    problems = check_digest(_digest(so_what="Pay 2,5 kat arttı."), BODY)
    assert problems == ["numbers not in source: 2,5"]


def test_paraphrased_quote_is_flagged():
    problems = check_digest(_digest(quote="Fona maruz firmaların ağırlığı düşük."), BODY)
    assert problems == ["quote is not a verbatim sentence of the source"]


def test_bank_forecast_wording_is_flagged():
    problems = check_digest(_digest(so_what="TCMB kredilerin artmasını bekliyor."), BODY)
    assert len(problems) == 1 and problems[0].startswith("forbidden wording")


def test_single_finding_is_kept_but_flagged():
    digest = _digest(findings=GOOD_DIGEST["findings"][:1])
    assert "fewer than 2 findings" in check_digest(digest, BODY)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {**GOOD_DIGEST, "headline": ""},
        {**GOOD_DIGEST, "findings": "tek metin"},
        {**GOOD_DIGEST, "findings": []},
        {k: v for k, v in GOOD_DIGEST.items() if k != "not_said"},
    ],
)
def test_wrong_shape_raises(raw):
    with pytest.raises(DigestFormatError):
        validate_digest(raw)
