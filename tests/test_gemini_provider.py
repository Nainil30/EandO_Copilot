"""Offline tests for the Gemini provider: model fallback chain and error normalisation (no network)."""

import pytest

from app.config import get_settings
from app.llm.base import TransientLLMError


@pytest.fixture()
def provider(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(s, "gemini_sql_model", "primary")
    monkeypatch.setattr(s, "gemini_fallback_models", "backup-1,backup-2")
    from app.llm.gemini import GeminiProvider

    return GeminiProvider()


def test_falls_back_when_primary_is_overloaded(provider, monkeypatch):
    tried = []

    def fake_once(model, prompt, *, system, json_schema):
        tried.append(model)
        if model == "primary":
            raise TransientLLMError("503 UNAVAILABLE")
        return "ok", 10, 2, model

    monkeypatch.setattr(provider, "_generate_once", fake_once)
    text, _, _, model = provider._generate_text("hi", system=None, tier="smart", json_schema=None)
    assert (text, model) == ("ok", "backup-1")
    assert tried == ["primary", "backup-1"]


def test_raises_transient_when_every_model_is_down(provider, monkeypatch):
    def down(model, prompt, *, system, json_schema):
        raise TransientLLMError(f"503 {model}")

    monkeypatch.setattr(provider, "_generate_once", down)
    with pytest.raises(TransientLLMError, match="backup-2"):
        provider._generate_text("hi", system=None, tier="smart", json_schema=None)


def test_retryable_error_detection():
    from app.llm.gemini import _is_retryable

    assert _is_retryable(Exception("429 RESOURCE_EXHAUSTED"))
    assert _is_retryable(Exception("503 UNAVAILABLE: high demand"))
    assert not _is_retryable(Exception("400 API key not valid"))


def test_retry_delay_honours_server_hint():
    from app.llm.base import retry_delay

    assert retry_delay(Exception("429 ... Please retry in 16.42s."), attempt=1) == pytest.approx(17.42)
    assert retry_delay(Exception("429 retry in 500s"), attempt=1) == 40.0  # capped
    assert 0 <= retry_delay(Exception("503 overloaded"), attempt=2) <= 1.6  # jittered backoff
