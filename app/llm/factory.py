from __future__ import annotations

import threading

from app.config import get_settings
from app.llm.base import LLMProvider

_lock = threading.Lock()
_provider: LLMProvider | None = None


def get_llm() -> LLMProvider:
    """Process-wide singleton (clients hold HTTP connection pools)."""
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = _build(get_settings().llm_provider)
    return _provider


def set_llm(provider: LLMProvider | None) -> None:
    """Override the provider (used by tests)."""
    global _provider
    _provider = provider


def _build(name: str) -> LLMProvider:
    if name == "gemini":
        from app.llm.gemini import GeminiProvider

        return GeminiProvider()
    if name == "openai":
        from app.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider()
    if name == "fake":
        from app.llm.fake import FakeProvider

        return FakeProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {name}")
