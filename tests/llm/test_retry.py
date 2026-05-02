import pytest

from src.llm import service as llm_service


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
    monkeypatch.setattr(llm_service.settings, "MINIMAX_RETRY_BASE_DELAY_SECONDS", 1.5)
    monkeypatch.setattr(llm_service.settings, "MINIMAX_RETRY_MAX_DELAY_SECONDS", 20.0)
    assert llm_service._retry_delay_seconds(attempt, retry_after) == expected
