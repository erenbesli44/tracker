from types import SimpleNamespace

from src.ingestion import service
from src.ingestion.schemas import IngestionYoutubeRequest
from src.topics import service as topics_service


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
