"""Usage logging and per-call reasoning_effort for the MiniMax call."""

import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from src.llm import service as llm_service


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _completion(content='{"ok": true}', usage=True, **tokens):
    usage_block = None
    if usage:
        usage_block = SimpleNamespace(
            prompt_tokens=tokens.get("prompt_tokens", 0),
            completion_tokens=tokens.get("completion_tokens", 0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=tokens.get("cached_tokens", 0)),
            completion_tokens_details=SimpleNamespace(
                reasoning_tokens=tokens.get("reasoning_tokens", 0)
            ),
        )
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
        usage=usage_block,
    )


def _status_error(status: int, message: str) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "http://minimax.test/v1"))
    return openai.APIStatusError(message, response=response, body=None)


@pytest.fixture
def minimax(monkeypatch):
    """Configure MiniMax and return a factory that installs a fake client with canned replies."""
    monkeypatch.setattr(llm_service.settings, "MINIMAX_BASE_URL", "http://minimax.test/v1")
    monkeypatch.setattr(llm_service.settings, "MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(llm_service.settings, "MINIMAX_RETRY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(llm_service, "_usage_totals", {})

    def factory(responses):
        completions = _FakeCompletions(responses)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(llm_service.openai, "OpenAI", lambda **_kw: client)
        return completions

    return factory


def test_configured_effort_is_sent_and_usage_recorded(minimax, monkeypatch, caplog):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_ANALYSIS", "medium")
    completions = minimax(
        [
            _completion(
                prompt_tokens=100, cached_tokens=10, completion_tokens=50, reasoning_tokens=30
            )
        ]
    )
    caplog.set_level(logging.INFO, logger="src.llm.service")

    assert llm_service._call_llm_json("prompt", purpose="analysis") == {"ok": True}

    request = completions.requests[0]
    assert request["reasoning_effort"] == "medium"
    assert request["extra_body"] == {"reasoning_split": True}
    assert request["messages"][1] == {"role": "user", "content": "prompt"}
    snapshot = llm_service.usage_snapshot()
    assert snapshot["by_purpose"]["analysis"] == {
        "calls": 1,
        "prompt_tokens": 100,
        "cached_tokens": 10,
        "completion_tokens": 50,
        "reasoning_tokens": 30,
    }
    assert snapshot["total"]["reasoning_tokens"] == 30
    line = next(r.getMessage() for r in caplog.records if "MiniMax usage" in r.getMessage())
    assert "purpose=analysis" in line and "effort=medium" in line and "reasoning_tokens=30" in line


def test_empty_effort_omits_the_field(minimax, monkeypatch, caplog):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_OUTLOOK", "")
    completions = minimax([_completion()])
    caplog.set_level(logging.INFO, logger="src.llm.service")

    llm_service._call_llm_json("prompt", purpose="outlook")

    assert "reasoning_effort" not in completions.requests[0]
    assert any("effort=default" in r.getMessage() for r in caplog.records)


def test_invalid_effort_is_ignored_with_a_warning(minimax, monkeypatch, caplog):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_QUOTE_CLEAN", "ultra")
    completions = minimax([_completion()])
    caplog.set_level(logging.WARNING, logger="src.llm.service")

    llm_service._call_llm_json("prompt", purpose="quote_clean")

    assert "reasoning_effort" not in completions.requests[0]
    assert any("MINIMAX_EFFORT_QUOTE_CLEAN" in r.getMessage() for r in caplog.records)


def test_unknown_purpose_uses_the_default_setting(minimax, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_DEFAULT", "High")
    completions = minimax([_completion()])

    llm_service._call_llm_json("prompt", purpose="economic_thesis")

    assert completions.requests[0]["reasoning_effort"] == "high"


def test_rejected_effort_is_resent_without_it(minimax, monkeypatch, caplog):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_ANALYSIS", "medium")
    completions = minimax(
        [_status_error(400, "invalid params: reasoning_effort not supported"), _completion()]
    )
    caplog.set_level(logging.WARNING, logger="src.llm.service")

    assert llm_service._call_llm_json("prompt", purpose="analysis") == {"ok": True}

    assert len(completions.requests) == 2
    assert completions.requests[0]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in completions.requests[1]
    assert llm_service.usage_snapshot()["total"]["calls"] == 1
    assert any("rejected reasoning_effort=medium" in r.getMessage() for r in caplog.records)


def test_other_400_errors_still_fail(minimax, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "MINIMAX_EFFORT_ANALYSIS", "medium")
    minimax([_status_error(400, "invalid model name")])

    with pytest.raises(llm_service.LLMGenerationError):
        llm_service._call_llm_json("prompt", purpose="analysis")
    assert llm_service.usage_snapshot()["total"]["calls"] == 0


def test_missing_usage_block_counts_zero_tokens(minimax):
    minimax([_completion(usage=False)])

    llm_service._call_llm_json("prompt", purpose="participants")

    assert llm_service.usage_snapshot()["by_purpose"]["participants"] == {
        "calls": 1,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
    }
