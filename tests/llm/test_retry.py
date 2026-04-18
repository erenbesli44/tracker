import io
import json
from urllib.error import HTTPError

import pytest

from src.llm import service as llm_service


class _DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _gemini_success_payload(content: dict) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(content)}],
                }
            }
        ]
    }


def test_call_gemini_json_retries_on_rate_limit_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(_request, timeout):  # noqa: ANN001
        assert timeout >= 15
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(
                url="https://example.test",
                code=429,
                msg="Too Many Requests",
                hdrs={"Retry-After": "0"},
                fp=io.BytesIO(b'{"error":"high demand, try again later"}'),
            )
        return _DummyResponse(_gemini_success_payload({"ok": True}))

    monkeypatch.setattr(llm_service, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_service.settings, "GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    monkeypatch.setattr(llm_service.settings, "GEMINI_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(llm_service.settings, "GEMINI_TIMEOUT_SECONDS", 30)

    payload = llm_service._call_gemini_json("test prompt")

    assert payload == {"ok": True}
    assert calls["count"] == 2


def test_retryable_http_error_uses_phrase_detection():
    assert llm_service._is_retryable_http_error(429, "")
    assert llm_service._is_retryable_http_error(400, "Model under high demand, try again")
    assert not llm_service._is_retryable_http_error(400, "invalid model name")


@pytest.mark.parametrize(
    ("attempt", "retry_after", "expected"),
    [
        (1, None, 1.5),
        (2, None, 3.0),
        (10, None, 20.0),
        (1, 100.0, 20.0),
        (1, 2.0, 2.0),
    ],
)
def test_retry_delay_seconds_respects_caps(monkeypatch, attempt, retry_after, expected):
    monkeypatch.setattr(llm_service.settings, "GEMINI_RETRY_BASE_DELAY_SECONDS", 1.5)
    monkeypatch.setattr(llm_service.settings, "GEMINI_RETRY_MAX_DELAY_SECONDS", 20.0)
    assert llm_service._retry_delay_seconds(attempt, retry_after) == expected
