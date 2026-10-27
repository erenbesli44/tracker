from datetime import date

from src.jobs.broker_watch import parsers
from tests.broker_watch import fixtures as fx

ALL_TEXTS = [
    fx.ISY_20_OCT_TEXT,
    fx.ISY_1_SEP_TEXT,
    fx.ISY_1_SEP_PDF_SENTENCE,
    fx.GEDIK_22_SEP_TEXT,
    fx.GEDIK_6_AUG_TEXT,
    fx.YK_14_SEP_PAGE1,
    fx.YK_14_SEP_PAGE2,
    fx.GEDIK_UNKNOWN_TICKER_TEXT,
    fx.ISY_15_JAN_TEXT,
    fx.ISY_12_MAR_TEXT,
    fx.GEDIK_5_MAY_TEXT,
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
    assert parsers.build_quote("Başlık parçası") == ""  # no sentence end: not a reason
    long_sentence = "kelime " * 60 + "son."
    quote = parsers.build_quote(long_sentence)
    assert 0 < len(quote) <= parsers.MAX_QUOTE_CHARS and quote in long_sentence


def test_gedik_header_followed_by_header_has_no_quote():
    text = "Tüpraş’ı (TUPRS) çıkarıyoruz: Astor Enerji’yi (ASTOR) ekliyoruz: Sebep burada."
    doc = parsers.parse_gedik_page(text, published_at=date(2026, 9, 22))

    events = _by_ticker(doc)
    assert events["TUPRS"].reason_quote == ""
    assert events["TUPRS"].needs_review
    assert events["ASTOR"].reason_quote == "Sebep burada."
    assert not events["ASTOR"].needs_review


def test_isy_company_name_counts_only_in_genitive_before_weight_noun():
    dative = (
        "Model Portföy Değişiklikler Sabancı Holding üzerinden Akbank'a dolaylı yatırım "
        "yaparken Sabancı Holding ağırlığını artırıyoruz. 20.10.2026"
    )
    doc = parsers.parse_isy_rss(dative)
    assert "AKBNK" not in _by_ticker(doc)
    assert doc.events == [] and doc.needs_review
    assert any("weight clause without a known ticker" in w for w in doc.warnings)

    ablative = "Akbank'tan sonra Garanti'nin ağırlığını %10'a yükseltiyoruz. 20.10.2026"
    doc = parsers.parse_isy_rss(ablative)
    assert "AKBNK" not in _by_ticker(doc)


def test_isy_weight_noun_and_verb_variants():
    text = (
        "Model Portföy Değişiklikler AKBNK ve GARAN ağırlıklarını artırırken ENJSA’nın "
        "ağrılığını %5’e çekiyoruz. 01.09.2026"
    )
    doc = parsers.parse_isy_rss(text)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("AKBNK", "weight_up"),
        ("GARAN", "weight_up"),
        ("ENJSA", "weight_down"),
    ]
    assert not doc.needs_review


def test_yk_uppercase_header_is_added_not_removed():
    text = (
        "MODEL PORTFÖYÜMÜZE BİM'İ EKLİYORUZ. Bim (BIMAS TI, 400.00TL) Öneri: AL, "
        "(Hedef Fiyat: 500.00TL) Bim'i model portföyümüze ekliyoruz."
    )
    doc = parsers.parse_yk_json(text, published_at=date(2026, 9, 14))

    assert [(e.ticker, e.action, e.needs_review) for e in doc.events] == [("BIMAS", "added", False)]


def test_yk_unknown_verb_flags_document_instead_of_guessing():
    text = "Model portföyümüzden Vakıfbank’ı koruyoruz. Vakıfbank (VAKBN TI, 38.08TL) Öneri: AL"
    doc = parsers.parse_yk_json(text, published_at=date(2026, 9, 14))

    assert doc.events == [] and doc.needs_review
    assert any("unrecognised verb" in w for w in doc.warnings)


def test_is_known_ticker_uses_config_list():
    assert parsers.is_known_ticker("AKBNK")
    assert parsers.is_known_ticker("akbnk")
    assert not parsers.is_known_ticker("TCMB")


# ── Step 2 residuals ──────────────────────────────────────────────────────────


def test_isy_weight_sentence_survives_pypdf_split_words():
    # Real pypdf output of the 1 Sep PDF: 'i ndirim' (a noun) and 'ağ ırlığını' are split,
    # the verb stems are intact. The feed excerpt (ISY_1_SEP_TEXT) has no splits.
    text = fx.ISY_1_SEP_PDF_SENTENCE
    doc = parsers.parse_isy_rss(text, published_at=fx.ISY_1_SEP_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("TUPRS", "weight_down"),
        ("AKBNK", "weight_up"),
        ("GARAN", "weight_up"),
        ("ENJSA", "weight_up"),
    ]
    assert not doc.needs_review
    assert any("TCMB" in w for w in doc.warnings)
    _assert_quotes_ok(doc, text)


def test_isy_weight_verb_stems_stay_strict():
    # Verb stems are not loosened on purpose: with optional gaps 'az altın' would match
    # 'azalt' and 'in dirençli' would match 'indir', inventing a direction across words.
    # A clause without a contiguous stem yields no weight event.
    for clause in (
        "Tüpraş’ın ağırlığını daha az altın fiyatına bağlı kalacak şekilde koruyoruz.",
        "AKBNK ağırlığını korurken in dirençli bankacılık sektörüne odaklanıyoruz.",
    ):
        doc = parsers.parse_isy_rss(f"Model Portföy Değişiklikler {clause} 20.10.2026")
        assert doc.events == [], clause


def test_isy_and_gedik_headers_tolerate_split_words():
    # Synthetic splits: the real 20 Oct and 22 Sep PDFs keep these headings intact
    # (checked 2026-10-26); this guards the _loose() patterns themselves.
    text = fx.ISY_20_OCT_TEXT.replace("Eklenen Şirket", "Ekle nen Şirket").replace(
        "Model Portföy Değişiklikler", "Model Portf öy Değişiklikler"
    )
    doc = parsers.parse_isy_rss(text, published_at=fx.ISY_20_OCT_DATE)
    assert [(e.ticker, e.action) for e in doc.events] == [("SAHOL", "added"), ("AKBNK", "removed")]
    assert doc.events[0].reason_quote.endswith("devam ediyoruz.")  # body cut before the label

    gedik = fx.GEDIK_22_SEP_TEXT.replace("(TUPRS) çıkarıyoruz:", "(TUPRS) çıkarı yoruz:")
    doc = parsers.parse_gedik_page(gedik)
    assert [(e.ticker, e.action) for e in doc.events] == [("TUPRS", "removed"), ("ASTOR", "added")]


def test_yk_holdings_come_from_page_2_and_known_tickers_only():
    page1 = "BIST 14.09.2026 raporu. 2026 16.01.2023"
    assert parsers.parse_yk_json(page1, published_at=date(2026, 9, 14)).composition == []

    page2 = "Şirket Kod Portföy Giriş Tarihi BIST 14.09.2026 Bim BIMAS 16.01.2023"
    doc = parsers.parse_yk_json(page1 + "\f" + page2, published_at=date(2026, 9, 14))
    assert [(h.ticker, h.entry_date) for h in doc.composition] == [("BIMAS", date(2023, 1, 16))]
    assert any("unknown holding BIST" in w for w in doc.warnings)


def test_yk_composition_basis_follows_the_table_title():
    doc = parsers.parse_yk_json(fx.YK_14_SEP_TEXT, published_at=fx.YK_14_SEP_DATE)
    assert doc.composition_basis == "pre_change"

    current = fx.YK_14_SEP_TEXT.replace(" (değişiklikler öncesi)", "")
    doc = parsers.parse_yk_json(current, published_at=fx.YK_14_SEP_DATE)
    assert doc.composition_basis == "current" and len(doc.composition) == 3
    assert any("without the '(değişiklikler öncesi)' title" in w for w in doc.warnings)
    assert parsers.parse_gedik_page(fx.GEDIK_22_SEP_TEXT).composition_basis == "current"


def test_yk_document_date_is_the_first_turkish_date_on_page_1():
    assert parsers.parse_yk_json(fx.YK_14_SEP_TEXT).document_date == date(2026, 9, 14)
    doc = parsers.parse_yk_json(fx.YK_14_SEP_TEXT, published_at=date(2026, 9, 15))
    assert (doc.document_date, doc.events[0].event_date) == (date(2026, 9, 14), date(2026, 9, 15))
    assert parsers.parse_gedik_page(fx.GEDIK_22_SEP_TEXT).document_date is None


def test_isy_weight_clause_naming_more_companies_than_tickers_is_flagged():
    text = (
        "Model Portföy Değişiklikler Garanti Bankası’nın ve Emlak GYO’nun ağırlığını "
        "artırırken AKBNK ağırlığını azaltıyoruz. 20.10.2026"
    )
    doc = parsers.parse_isy_rss(text)
    assert [(e.ticker, e.action) for e in doc.events] == [("AKBNK", "weight_down")]
    assert doc.needs_review

    # One company mapped (Tüpraş) but two named in the clause: flagged, event kept.
    text = "Model Portföy Değişiklikler Tüpraş’ın ve Garanti’nin ağırlığını azaltıyoruz. 20.10.2026"
    doc = parsers.parse_isy_rss(text)
    assert [(e.ticker, e.action) for e in doc.events] == [("TUPRS", "weight_down")]
    assert doc.needs_review and any("unmapped company" in w for w in doc.warnings)

    # As many genitives as tickers: not flagged (the 1 Sep note has TCMB’nin + 3 tickers).
    doc = parsers.parse_isy_rss(fx.ISY_1_SEP_TEXT, published_at=fx.ISY_1_SEP_DATE)
    assert not doc.needs_review


# ── Step 3: clause-merge guard ────────────────────────────────────────────────


def test_isy_split_verb_merges_into_next_clause_and_is_flagged():
    # The real 1 Sep sentence with its first verb split by pypdf ('i ndirirken'): the
    # Tüpraş clause has no recognised verb and falls into the scope of 'artırıyoruz'.
    # Nothing may come out of that scope as an unflagged event, least of all TUPRS
    # as weight_up; the direction is never guessed.
    text = fx.ISY_1_SEP_PDF_SENTENCE.replace("indirirken", "i ndirirken")
    assert text != fx.ISY_1_SEP_PDF_SENTENCE
    doc = parsers.parse_isy_rss(text, published_at=fx.ISY_1_SEP_DATE)

    events = _by_ticker(doc)
    assert "TUPRS" not in events or events["TUPRS"].needs_review
    assert all(e.needs_review for e in doc.events)
    assert doc.needs_review
    assert any("weight clause without a recognised verb" in w for w in doc.warnings)
    assert not any(e.action == "weight_down" for e in doc.events)  # 'indir' was never seen
    _assert_quotes_ok(doc, text)

    # The unsplit sentence keeps its four unflagged events (guard does not fire).
    doc = parsers.parse_isy_rss(fx.ISY_1_SEP_PDF_SENTENCE, published_at=fx.ISY_1_SEP_DATE)
    assert [(e.ticker, e.action, e.needs_review) for e in doc.events] == [
        ("TUPRS", "weight_down", False),
        ("AKBNK", "weight_up", False),
        ("GARAN", "weight_up", False),
        ("ENJSA", "weight_up", False),
    ]
    assert not doc.needs_review


def test_isy_unlisted_noun_form_in_merged_clause_is_flagged():
    # Shape of the 5 Feb 2026 note ("Aksa Enerji ve Medical Park ağırlıklarımızı %2 şer
    # arttırırken, … THY nin de ağırlığını %2 arttırıyoruz"), here with tickers: the first
    # noun form is not a strict clause noun, so the clause merges into the next one. The
    # guard counts nouns by stem and must flag every event rather than let AKSEN and MPARK
    # inherit "indir".
    text = (
        "Model Portföy Değişiklikler AKSEN ve MPARK ağırlıklarımızı %2 şer arttırırken, "
        "THYAO'nun ağırlığını %2 indiriyoruz. 05.02.2026"
    )
    doc = parsers.parse_isy_rss(text)

    assert {e.ticker for e in doc.events} <= {"AKSEN", "MPARK", "THYAO"} and doc.events
    assert all(e.needs_review for e in doc.events)
    assert doc.needs_review
    assert any("weight clause without a recognised verb" in w for w in doc.warnings)


def test_isy_unlisted_verb_clause_is_flagged_not_merged_silently():
    text = (
        "Model Portföy Değişiklikler TUPRS ağırlığını sabit tutarken, AKBNK ağırlığını "
        "artırıyoruz. 20.10.2026"
    )
    doc = parsers.parse_isy_rss(text)

    events = _by_ticker(doc)
    assert "TUPRS" not in events or events["TUPRS"].needs_review
    assert events["AKBNK"].needs_review  # same scope, same doubt
    assert doc.needs_review
    assert any(w.startswith("weight clause without a recognised verb: ") for w in doc.warnings)
    assert any(
        "TUPRS weight_up: needs review (weight clause without a recognised verb)" == w
        for w in doc.warnings
    )

    # Two recognised verbs in one sentence: two scopes, one noun each, nothing flagged.
    text = (
        "Model Portföy Değişiklikler TUPRS ağırlığını %7’ye indirirken, AKBNK ağırlığını "
        "artırıyoruz. 20.10.2026"
    )
    doc = parsers.parse_isy_rss(text)
    assert [(e.ticker, e.action, e.needs_review) for e in doc.events] == [
        ("TUPRS", "weight_down", False),
        ("AKBNK", "weight_up", False),
    ]
    assert not doc.needs_review


# ── OI-027 parser coverage: İş plural labels, per-ticker quotes, dated Gedik header ───


def test_isy_15_jan_plural_labels_with_per_ticker_quotes():
    doc = parsers.parse_isy_rss(fx.ISY_15_JAN_TEXT, published_at=fx.ISY_15_JAN_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("KCHOL", "added"),
        ("AKBNK", "added"),
        ("ENKAI", "removed"),
        ("YKBNK", "removed"),
    ]
    assert all(e.event_date == date(2026, 1, 15) and not e.needs_review for e in doc.events)
    assert not doc.needs_review
    _assert_quotes_ok(doc, fx.ISY_15_JAN_TEXT)
    events = _by_ticker(doc)
    # Each ticker quotes its own paragraph; the section headers and labels are stripped.
    assert events["ENKAI"].reason_quote.startswith("ENKAI hisselerini 24.03.2025 tarihli")
    assert events["ENKAI"].reason_quote.endswith("En Çok Önerilenler listemizden çıkarıyoruz.")
    assert events["AKBNK"].reason_quote == "Akbank ’ı model portföyümüze ekliyoruz."
    for fragment in ("Çıkanlar", "Eklenenler", ".IS:", "Çıkartılan"):
        assert all(fragment not in e.reason_quote for e in doc.events), fragment
    # A listed ticker without its own paragraph in the excerpt falls back to the first
    # paragraph; the label is explicit, so the event stays auto and only a warning is kept.
    assert events["YKBNK"].reason_quote == events["ENKAI"].reason_quote
    assert events["KCHOL"].reason_quote == events["ENKAI"].reason_quote
    assert "no per-ticker paragraph for YKBNK" in doc.warnings
    assert "no per-ticker paragraph for KCHOL" in doc.warnings


def test_isy_12_mar_label_list_tolerates_trailing_ampersand():
    doc = parsers.parse_isy_rss(fx.ISY_12_MAR_TEXT, published_at=fx.ISY_12_MAR_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("ENJSA", "added"),
        ("AKBNK", "removed"),
        ("EKGYO", "removed"),
        ("THYAO", "removed"),
    ]
    assert all(e.event_date == date(2026, 3, 12) and not e.needs_review for e in doc.events)
    assert not doc.needs_review
    _assert_quotes_ok(doc, fx.ISY_12_MAR_TEXT)
    events = _by_ticker(doc)
    assert events["AKBNK"].reason_quote.startswith("Akbank’ı model portföyümüzden")
    assert events["ENJSA"].reason_quote == (
        "ENJSA’yı 155TL hisse hedef fiyat ve %35 artış potansiyeli ile model portföye ekliyoruz."
    )
    assert "En Çok" not in events["ENJSA"].reason_quote  # the stray '&' did not leak text


def test_isy_plural_labels_tolerate_split_words():
    # Synthetic pypdf-style splits inside the plural label words (the real 12 Mar PDF keeps
    # them intact); guards the _loose() use in the label patterns and the body cut.
    text = fx.ISY_12_MAR_TEXT.replace("Çıkartılan Şirketlerler", "Çıkartıl an Şirketle rler")
    text = text.replace("Eklenen Şirketler", "Eklen en Şirket ler")
    doc = parsers.parse_isy_rss(text, published_at=fx.ISY_12_MAR_DATE)

    assert [(e.ticker, e.action) for e in doc.events] == [
        ("ENJSA", "added"),
        ("AKBNK", "removed"),
        ("EKGYO", "removed"),
        ("THYAO", "removed"),
    ]
    assert _by_ticker(doc)["ENJSA"].reason_quote.endswith("model portföye ekliyoruz.")


def test_gedik_5_may_dated_header_without_colon():
    doc = parsers.parse_gedik_page(fx.GEDIK_5_MAY_TEXT, published_at=date(2026, 5, 5))

    assert [(e.ticker, e.action, e.event_date, e.needs_review) for e in doc.events] == [
        ("KCAER", "added", date(2026, 5, 5), False)
    ]
    assert not doc.needs_review
    _assert_quotes_ok(doc, fx.GEDIK_5_MAY_TEXT)
    quote = doc.events[0].reason_quote
    assert quote.startswith("Kocaer Çelik’i model portföyümüze ekliyoruz.")
    assert "Model Portföy Güncellemesi" not in quote  # the title belongs to the header
    # The date comes from the title, as for the colon form.
    assert parsers.parse_gedik_page(fx.GEDIK_5_MAY_TEXT).events[0].event_date == date(2026, 5, 5)
