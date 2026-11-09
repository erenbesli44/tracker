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


@pytest.mark.parametrize(
    "so_what,missing",
    [
        ("Kredilerin yüzde 2'si bu firmalarda.", "2"),  # 2 occurs, never as "yüzde 2"
        ("Kredi kullanımı 3 kat arttı.", "3"),  # 3 occurs, but never as "3 kat"
        ("Bu oran 4 yılda değişti.", "4"),  # [4] is a footnote marker, not a number
    ],
)
def test_small_integers_need_the_same_neighbouring_word(so_what, missing):
    problems = check_digest(_digest(so_what=so_what), BODY)
    assert problems == [f"numbers not in source: {missing}"]


def test_small_integers_with_their_word_pass():
    so_what = "Yaklaşık 3 bin firma; 57 milyar TL para piyasası fonlarında; on binde 3."
    assert check_digest(_digest(so_what=so_what), BODY) == []


@pytest.mark.parametrize(
    "quote",
    [
        "…fon maruziyeti yüksek firmaların ekonomideki ağırlığı görece düşük.",  # fragment
        "fon maruziyeti yüksek firmaların ekonomideki ağırlığı görece düşük",  # no sentence end
        "Diğer bir ifadeyle, fon maruziyeti yüksek firmaların ekonomideki ağırlığı",  # cut short
    ],
)
def test_quote_must_be_a_whole_sentence(quote):
    problems = check_digest(_digest(quote=quote), BODY)
    assert problems == ["quote is not a verbatim sentence of the source"]


def test_first_sentence_of_a_paragraph_is_a_valid_quote():
    quote = "Bir sonraki adımda firmaların ticari bağlantılarına bakıyoruz."
    assert check_digest(_digest(quote=quote), BODY) == []


@pytest.mark.parametrize(
    "so_what",
    ["TCMB'nin beklentisi kredilerin artması.", "Merkez Bankası faizlerin düşmesini öngörüyor."],
)
def test_wider_bank_forecast_wording_is_flagged(so_what):
    problems = check_digest(_digest(so_what=so_what), BODY)
    assert len(problems) == 1 and problems[0].startswith("forbidden wording")
