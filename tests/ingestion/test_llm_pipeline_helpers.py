from types import SimpleNamespace

from src.ingestion import service
from src.ingestion.schemas import IngestionYoutubeRequest
from src.topics import service as topics_service


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


def _segs(starts: list[float]) -> list[dict]:
    return [{"start": s, "duration": 5.0, "text": f"t{int(s)}"} for s in starts]


def test_render_segments_marks_every_segment_by_default():
    out = service._render_segments_for_llm(_segs([0, 5, 10]))
    assert out == "[00:00-00:05] t0\n[00:05-00:10] t5\n[00:10-00:15] t10"


def test_render_segments_sparse_marks_the_first_segment_and_each_boundary():
    out = service._render_segments_for_llm(_segs([0, 5, 10, 25, 31, 45, 60, 62]), every_seconds=30)
    assert out.splitlines() == ["[00:00] t0 t5 t10 t25", "[00:31] t31 t45", "[01:00] t60 t62"]


def test_render_segments_sparse_uses_hhmmss_past_one_hour():
    out = service._render_segments_for_llm(_segs([3599, 3605]), every_seconds=30)
    assert out == "[59:59] t3599\n[01:00:05] t3605"


def test_render_segments_interval_comes_from_settings(monkeypatch):
    monkeypatch.setattr(service.settings, "ANALYSIS_TIMESTAMP_EVERY_SECONDS", 10)
    out = service._render_segments_for_llm(_segs([0, 5, 10, 15, 20]))
    assert out == "[00:00] t0 t5\n[00:10] t10 t15\n[00:20] t20"


def test_render_segments_skips_empty_text_in_both_forms():
    segments = [
        {"start": 0.0, "duration": 5.0, "text": "  "},
        {"start": 5.0, "duration": 5.0, "text": "a"},
    ]
    assert service._render_segments_for_llm(segments) == "[00:05-00:10] a"
    assert service._render_segments_for_llm(segments, every_seconds=30) == "[00:05] a"


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
