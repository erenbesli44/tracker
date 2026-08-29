from types import SimpleNamespace

from src.channels import service as channels_service
from src.channels.schemas import ChannelCreate
from src.ingestion import service
from src.ingestion.schemas import IngestionYoutubeRequest
from src.llm.prompts import ANALYSIS_PROMPT_TEMPLATE
from src.market_inference.constants import INFERENCE_TOPIC_LABELS, INFERENCE_TOPIC_MAP
from src.topics import service as topics_service
from src.videos import service as videos_service
from src.videos.schemas import VideoCreate


def _summary_payload(**extras) -> dict:
    payload = {"summary": {"short": "s", "detailed": "d"}, "language": "tr"}
    payload.update(extras)
    return payload


def test_summary_payload_accepts_key_points_as_strings():
    result = service._summary_from_llm_payload(
        _summary_payload(key_points=["alpha", "beta", "gamma"]),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights == ["alpha", "beta", "gamma"]


def test_summary_payload_accepts_key_points_as_objects():
    result = service._summary_from_llm_payload(
        _summary_payload(key_points=[{"point": "alpha"}, {"point": "beta"}]),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights == ["alpha", "beta"]


def test_summary_payload_accepts_mixed_shapes_and_skips_empties():
    result = service._summary_from_llm_payload(
        _summary_payload(
            key_points=[
                "alpha",
                {"point": "beta"},
                {"point": ""},
                None,
                "   ",
                "gamma",
            ]
        ),
        transcript_language="tr",
    )
    assert result is not None
    # limit=5 is applied to the raw list, then empties drop out; matches the
    # prompt's "up to 5 bullets" contract — we don't over-read past slot 5.
    assert result.highlights == ["alpha", "beta"]


def test_summary_payload_caps_highlights_at_five():
    result = service._summary_from_llm_payload(
        _summary_payload(key_points=[f"b{i}" for i in range(10)]),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights == ["b0", "b1", "b2", "b3", "b4"]


def test_summary_payload_falls_back_to_actionable_takeaways():
    result = service._summary_from_llm_payload(
        _summary_payload(actionable_takeaways=[{"takeaway": "act1"}, "act2"]),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights == ["act1", "act2"]


def test_summary_payload_missing_key_points_yields_no_highlights():
    result = service._summary_from_llm_payload(
        _summary_payload(),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights is None


def test_summary_payload_non_list_key_points_is_ignored():
    result = service._summary_from_llm_payload(
        _summary_payload(key_points="not a list"),
        transcript_language="tr",
    )
    assert result is not None
    assert result.highlights is None


def test_extract_bullets_warns_when_non_empty_yields_nothing(caplog):
    with caplog.at_level("WARNING"):
        bullets = service._extract_bullets([{"unknown_key": "x"}], "point")
    assert bullets == []
    assert any("yielded 0 bullets" in r.getMessage() for r in caplog.records)


def test_prepare_transcript_for_llm_uses_timestamped_segments():
    formatted = service._prepare_transcript_for_llm(
        "Plain fallback transcript text",
        transcript_segments=[
            {"start": 0.0, "duration": 5.0, "text": "BIST tarafinda baski suruyor."},
            {"start": 5.0, "duration": 4.0, "text": "10200 direnci kritik."},
        ],
    )

    assert "[00:00-00:05] BIST tarafinda baski suruyor." in formatted
    assert "[00:05-00:09] 10200 direnci kritik." in formatted
    assert "Plain fallback transcript text" not in formatted


def test_build_llm_metadata_normalizes_channel_topic_context():
    data = IngestionYoutubeRequest(video={"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"})
    channel = SimpleNamespace(
        name="Sample Channel",
        primary_topic_slug="economy_finance",
        expected_subtopics=None,
    )
    video = SimpleNamespace(title="Sample Video", published_at=None)

    meta = service._build_llm_metadata(
        data=data,
        channel=channel,
        video=video,
        video_metadata=None,
    )

    assert meta["channel_primary_topic"] == "ekonomi"
    assert "channel_expected_subtopics" not in meta


def test_auto_fill_uses_default_output_language_for_english_transcript(session, monkeypatch):
    channel = channels_service.create(
        session,
        ChannelCreate(
            name="The Plain Bagel",
            channel_handle="@ThePlainBagel",
            primary_topic_slug="economy_finance",
        ),
    )
    video = videos_service.create(
        session,
        VideoCreate(
            channel_id=channel.id,
            video_url="https://www.youtube.com/watch?v=abc12345678",
            title="English finance video",
        ),
    )
    data = IngestionYoutubeRequest(video={"video_url": video.video_url})
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_llm_generation_enabled", lambda: True)
    monkeypatch.setattr(service.settings, "LLM_DEFAULT_OUTPUT_LANGUAGE", "tr")

    def fake_generate_analysis_json(**kwargs):
        captured.update(kwargs)
        return {
            "summary": {
                "short": "Video, finansal planlama konusunu Türkçe özetler.",
                "detailed": (
                    "Video, finansal planlama konusunu Türkçe ve tamamlanmış "
                    "cümlelerle özetler."
                ),
            },
            "key_points": ["Finansal planlama ana fikirdir."],
        }

    monkeypatch.setattr(service.llm_service, "generate_analysis_json", fake_generate_analysis_json)

    service._auto_fill_missing_analytics_for_new_video(
        session,
        data,
        transcript_text="This is an English transcript about personal finance.",
        transcript_segments=None,
        transcript_language="en",
        channel=channel,
        video=video,
        video_metadata=None,
    )

    assert captured["output_language"] == "tr"
    assert data.summary is not None
    assert data.summary.language == "tr"
    assert data.summary.short_summary.startswith("Video, finansal")


def test_classification_from_llm_payload_keeps_levels_and_times(session):
    bist = topics_service.get_by_slug(session, "bist-turk-piyasalari")
    assert bist is not None

    payload = {
        "primary_topic": {"label": "ekonomi"},
        "topic_segments": [
            {
                "subtopic": "bist",
                "summary": "BIST tarafinda baski suruyor.",
                "evidence": "BIST 100 zayif seyrediyor.",
                "key_levels": [10000, "10150"],
                "start_time": "00:30",
                "end_time": "02:10",
                "stance": "negative",
                "confidence": 0.8,
            },
            {
                "subtopic": "bist-turk-piyasalari",
                "summary": "10200 direncinin uzeri kalici olamadi.",
                "evidence": "",
                "key_levels": ["10200"],
                "start_time": "02:20",
                "end_time": "03:00",
                "stance": "negative",
                "confidence": 0.6,
            },
        ],
    }

    parsed = service._classification_from_llm_payload(session, payload)
    assert parsed is not None
    assert parsed.source == "llm"
    assert len(parsed.topic_mentions) == 1

    mention = parsed.topic_mentions[0]
    assert mention.topic_id == bist.id
    assert mention.sentiment == "bearish"
    assert mention.key_levels == ["10000", "10150", "10200"]
    assert mention.start_time == "00:30"
    assert mention.end_time == "03:00"
    assert mention.confidence == 0.7
    assert "BIST tarafinda baski suruyor." in mention.summary
    assert "10200 direncinin uzeri kalici olamadi." in mention.summary


def test_analysis_prompt_requires_clear_view_for_tracked_assets():
    assert "TRACKED ITEMS" in ANALYSIS_PROMPT_TEMPLATE
    assert "Bitcoin" in ANALYSIS_PROMPT_TEMPLATE
    assert "Altın gr" in ANALYSIS_PROMPT_TEMPLATE
    assert "USD/TRY" in ANALYSIS_PROMPT_TEMPLATE
    assert "NASDAQ 100" in ANALYSIS_PROMPT_TEMPLATE
    assert "If the speaker does not express a clear opinion" in ANALYSIS_PROMPT_TEMPLATE
    assert '"has_speaker_view": true' in ANALYSIS_PROMPT_TEMPLATE


def test_classification_from_llm_payload_skips_items_without_speaker_view(session):
    payload = {
        "primary_topic": {"label": "ekonomi"},
        "topic_segments": [
            {
                "subtopic": "bitcoin",
                "tracked_item": "Bitcoin",
                "has_speaker_view": False,
                "summary": "Bitcoin sadece örnek olarak geçiyor.",
                "evidence": "Bitcoin de var.",
                "key_levels": [],
                "stance": "neutral",
                "confidence": 0.9,
            },
        ],
    }

    assert service._classification_from_llm_payload(session, payload) is None


def test_classification_from_llm_payload_uses_positive_negative_for_politics(session):
    jeopolitik = topics_service.get_by_slug(session, "jeopolitik")
    assert jeopolitik is not None

    payload = {
        "primary_topic": {"label": "siyaset"},
        "topic_segments": [
            {
                "subtopic": "jeopolitik",
                "tracked_item": "jeopolitik",
                "has_speaker_view": True,
                "summary": "Konuşmacı gerilimin bölgesel riskleri artırdığını söylüyor.",
                "evidence": "Bölgesel tansiyon yükseliyor.",
                "key_levels": [],
                "stance": "negative",
                "confidence": 0.8,
            },
        ],
    }

    parsed = service._classification_from_llm_payload(session, payload)
    assert parsed is not None
    assert len(parsed.topic_mentions) == 1
    mention = parsed.topic_mentions[0]
    assert mention.topic_id == jeopolitik.id
    assert mention.sentiment == "negative"


def test_inference_topics_match_tracked_asset_surface():
    expected = {
        "bitcoin": "Bitcoin",
        "altin-gr": "Altın gr",
        "usd-try": "USD/TRY",
        "bist-100": "BIST 100",
        "nasdaq-100": "NASDAQ 100",
        "sp-500": "S&P 500",
        "brent-petrol": "Brent petrol",
        "gumus": "Gümüş",
        "jeopolitik": "Jeopolitik",
        "ic-siyaset": "İç Siyaset",
    }
    for key, label in expected.items():
        assert key in INFERENCE_TOPIC_MAP
        assert INFERENCE_TOPIC_LABELS[key] == label
