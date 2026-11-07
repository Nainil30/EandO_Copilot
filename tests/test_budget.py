"""The daily budget must stop *before* the network call, and cached embeddings must cost nothing."""

import pytest

from app.config import get_settings
from app.llm import budget
from app.llm.base import LLMProvider
from app.pipeline.outputs import RouteDecision


class CountingProvider(LLMProvider):
    """A 'paid' provider that counts how often it would hit the network."""

    name = "counting"
    metered = True
    embed_model = "counting-embed"

    def __init__(self):
        self.generate_calls = 0
        self.embedded: list[str] = []

    def _generate_text(self, prompt, *, system, tier, json_schema):
        self.generate_calls += 1
        return '{"intent": "data_query", "reason": "x"}', 100, 10, "counting-model"

    def _embed(self, texts, *, task):
        self.embedded.extend(texts)
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture()
def fresh_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "cache_dir", str(tmp_path))
    monkeypatch.setattr(budget, "_cache", None)
    yield
    monkeypatch.setattr(budget, "_cache", None)


def test_budget_blocks_before_network_call(fresh_budget, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_daily_call_limit", 2)
    p = CountingProvider()
    p.generate_structured("q", RouteDecision, task="route")
    p.generate_structured("q", RouteDecision, task="route")
    with pytest.raises(budget.BudgetExceededError):
        p.generate_structured("q", RouteDecision, task="route")
    assert p.generate_calls == 2  # the third request never reached the "API"
    assert budget.usage_today()["calls"] == 2


def test_token_limit(fresh_budget, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_daily_call_limit", 0)
    monkeypatch.setattr(get_settings(), "llm_daily_token_limit", 150)
    p = CountingProvider()
    p.generate_structured("q", RouteDecision, task="route")  # 110 tokens
    p.generate_structured("q", RouteDecision, task="route")  # 220 tokens
    with pytest.raises(budget.BudgetExceededError):
        p.generate_structured("q", RouteDecision, task="route")


def test_embedding_cache_avoids_repeat_calls(fresh_budget):
    p = CountingProvider()
    p.embed(["a", "b"], task="document")
    p.embed(["a", "b", "c"], task="document")
    assert p.embedded == ["a", "b", "c"]  # only "c" was new
    p.embed(["a"], task="query")  # different task type -> different vector space
    assert p.embedded[-1] == "a"
    assert budget.usage_today()["calls"] == 3


def test_fake_provider_is_never_metered(fresh_budget):
    from app.llm.fake import FakeProvider

    FakeProvider().embed(["x"])
    assert budget.usage_today()["calls"] == 0
