from datetime import date

from src.jobs.broker_watch import parsers
from tests.broker_watch import fixtures as fx

ALL_TEXTS = [
    fx.ISY_20_OCT_TEXT,
    fx.ISY_1_SEP_TEXT,
    fx.GEDIK_22_SEP_TEXT,
    fx.GEDIK_6_AUG_TEXT,
    fx.YK_14_SEP_PAGE1,
    fx.YK_14_SEP_PAGE2,
    fx.GEDIK_UNKNOWN_TICKER_TEXT,
]


def _by_ticker(doc: parsers.ParsedDocument) -> dict[str, parsers.ParsedEvent]:
    return {e.ticker: e for e in doc.events}


def _assert_quotes_ok(doc: parsers.ParsedDocument, text: str) -> None:
    for event in doc.events:
        assert event.reason_quote, event
        assert len(event.reason_quote) <= parsers.MAX_QUOTE_CHARS
        assert event.reason_quote in text
        assert event.reason_quote in doc.text


def test_fixtures_are_trimmed_excerpts():
    for text in ALL_TEXTS:
        assert len(text) <= 400
    assert len(parsers.html_to_text(fx.GEDIK_COMPOSITION_HTML)) <= 400


# ── İş Yatırım ────────────────────────────────────────────────────────────────


def test_isy_20_oct_added_and_removed():
    doc = parsers.parse_isy_rss(fx.ISY_20_OCT_TEXT, published_at=fx.ISY_20_OCT_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("SAHOL", "added"),
        ("AKBNK", "removed"),
    ]
    assert all(e.event_date == date(2026, 10, 20) for e in doc.events)
    assert all(e.quote_page == 1 and not e.needs_review for e in doc.events)
    _assert_quotes_ok(doc, fx.ISY_20_OCT_TEXT)
    quote = doc.events[0].reason_quote
    assert quote.startswith("Model portföyümüzde Akbank'ı Sabancı Holding ile değiştirerek")
    assert quote.endswith("devam ediyoruz.")
    assert "Eklenen Şirket" not in quote


def test_isy_event_date_falls_back_to_text():
    doc = parsers.parse_isy_rss(fx.ISY_20_OCT_TEXT)
    assert doc.events[0].event_date == date(2026, 10, 20)


def test_isy_1_sep_weight_changes():
    doc = parsers.parse_isy_rss(fx.ISY_1_SEP_TEXT, published_at=fx.ISY_1_SEP_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("TUPRS", "weight_down"),
        ("AKBNK", "weight_up"),
        ("GARAN", "weight_up"),
        ("ENJSA", "weight_up"),
    ]
    assert all(e.event_date == date(2026, 9, 1) and not e.needs_review for e in doc.events)
    _assert_quotes_ok(doc, fx.ISY_1_SEP_TEXT)
    # TCMB is an uppercase token but not a ticker: skipped with a warning, never guessed.
    assert "TCMB" not in _by_ticker(doc)
    assert any("TCMB" in w for w in doc.warnings)


def test_isy_quote_is_verbatim_after_whitespace_normalisation():
    raw = fx.ISY_20_OCT_TEXT.replace("değiştirerek, ", "değiştirerek, \n")
    doc = parsers.parse_isy_rss(raw, published_at=fx.ISY_20_OCT_DATE)
    assert doc.events and all(e.reason_quote in doc.text for e in doc.events)
    assert "\n" not in doc.text


# ── Gedik Yatırım ─────────────────────────────────────────────────────────────


def test_gedik_22_sep_removed_and_added():
    doc = parsers.parse_gedik_page(fx.GEDIK_22_SEP_TEXT)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("TUPRS", "removed"),
        ("ASTOR", "added"),
    ]
    assert all(e.event_date == date(2026, 9, 22) and not e.needs_review for e in doc.events)
    _assert_quotes_ok(doc, fx.GEDIK_22_SEP_TEXT)
    events = _by_ticker(doc)
    assert events["TUPRS"].reason_quote.startswith("Endeksin Üzerinde Getiri")
    assert events["TUPRS"].reason_quote.endswith("Model Portföyümüzden çıkarıyoruz.")
    assert "Astor" not in events["TUPRS"].reason_quote  # next header's name is not a reason
    assert events["ASTOR"].reason_quote == "Astor Enerji'yi model portföyümüze ekliyoruz."


def test_gedik_6_aug_removed_and_added():
    doc = parsers.parse_gedik_page(fx.GEDIK_6_AUG_TEXT)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("ANHYT", "removed"),
        ("RGYAS", "added"),
    ]
    assert all(e.event_date == date(2026, 8, 6) for e in doc.events)
    _assert_quotes_ok(doc, fx.GEDIK_6_AUG_TEXT)


def test_gedik_unknown_ticker_is_flagged_not_dropped():
    doc = parsers.parse_gedik_page(fx.GEDIK_UNKNOWN_TICKER_TEXT)

    assert [(e.ticker, e.action, e.needs_review) for e in doc.events] == [("ORNEK", "added", True)]
    assert any("ORNEK" in w for w in doc.warnings)


def test_gedik_composition_from_html_table():
    holdings = parsers.parse_gedik_composition(fx.GEDIK_COMPOSITION_HTML)

    assert [h.ticker for h in holdings] == [t for _, t in fx.GEDIK_COMPOSITION_ROWS]
    assert all(h.entry_date is None for h in holdings)
    assert parsers.parse_gedik_composition("<p>no table here</p>") == []


# ── Yapı Kredi Yatırım ────────────────────────────────────────────────────────


def test_yk_14_sep_removed_with_recommendation_and_composition():
    doc = parsers.parse_yk_json(fx.YK_14_SEP_TEXT, published_at=fx.YK_14_SEP_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [("VAKBN", "removed")]
    event = doc.events[0]
    assert event.event_date == date(2026, 9, 14)
    assert event.recommendation == "AL"
    assert event.target_price == 41.0
    assert event.quote_page == 1
    assert not event.needs_review
    _assert_quotes_ok(doc, fx.YK_14_SEP_PAGE1)
    assert event.reason_quote.startswith("Vakıfbank’ı Model Portföyümüzden güçlü performansı")

    composition = {h.ticker: h.entry_date for h in doc.composition}
    assert composition["BIMAS"] == date(2023, 1, 16)
    assert composition["AKBNK"] == date(2025, 10, 16)
    assert composition["VAKBN"] == date(2026, 5, 13)


def test_yk_event_date_falls_back_to_text():
    doc = parsers.parse_yk_json(fx.YK_14_SEP_TEXT)
    assert doc.events[0].event_date == date(2026, 9, 14)


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_build_quote_limits_and_sentence_cuts():
    text = "Birinci cümle. İkinci cümle. Üçüncü cümle."
    assert parsers.build_quote(text, limit=20) == "Birinci cümle."
    assert parsers.build_quote(text, limit=28) == "Birinci cümle. İkinci cümle."
    assert parsers.build_quote(text) == text
    assert parsers.build_quote("Başlık parçası") == "Başlık parçası"
    long_sentence = "kelime " * 60
    quote = parsers.build_quote(long_sentence)
    assert len(quote) <= parsers.MAX_QUOTE_CHARS and quote in long_sentence


def test_is_known_ticker_uses_config_list():
    assert parsers.is_known_ticker("AKBNK")
    assert parsers.is_known_ticker("akbnk")
    assert not parsers.is_known_ticker("TCMB")
