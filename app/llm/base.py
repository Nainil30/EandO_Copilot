"""Provider-agnostic LLM interface.

The rest of the codebase only talks to `LLMProvider`. Swapping Gemini for
OpenAI / Ollama / a fake offline model is a config change, not a refactor.

Features implemented here (shared by all providers):
- structured outputs: pass a Pydantic model, get a validated instance back
- token usage + cost accounting per request (via contextvars)
- retries with exponential backoff + jitter for transient errors
"""

from __future__ import annotations

import contextvars
import json
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
Tier = Literal["smart", "fast"]
EmbedTask = Literal["document", "query"]


@dataclass
class LLMCall:
    task: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass
class UsageTracker:
    """Collects every LLM call made while serving a single request."""

    calls: list[LLMCall] = field(default_factory=list)

    def add(self, call: LLMCall) -> None:
        self.calls.append(call)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    def cost_usd(self, price_in_per_m: float, price_out_per_m: float) -> float:
        return round(
            self.input_tokens / 1e6 * price_in_per_m + self.output_tokens / 1e6 * price_out_per_m,
            6,
        )

    def as_dict(self, price_in_per_m: float, price_out_per_m: float) -> dict[str, Any]:
        return {
            "llm_calls": len(self.calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "est_cost_usd": self.cost_usd(price_in_per_m, price_out_per_m),
            "calls": [c.__dict__ for c in self.calls],
        }


_usage_var: contextvars.ContextVar[UsageTracker | None] = contextvars.ContextVar("llm_usage", default=None)


def start_usage_tracking() -> UsageTracker:
    tracker = UsageTracker()
    _usage_var.set(tracker)
    return tracker


def set_usage_tracker(tracker: UsageTracker | None) -> None:
    """Re-bind a tracker (needed when a generator resumes in a different thread/context)."""
    _usage_var.set(tracker)


def current_usage() -> UsageTracker | None:
    return _usage_var.get()


class LLMError(RuntimeError):
    pass


class TransientLLMError(LLMError):
    """Rate limit / timeout / 5xx – safe to retry."""


_RETRY_HINT = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
MAX_HINTED_WAIT_S = 40.0


def retry_delay(error: Exception, attempt: int, base_delay: float = 0.8) -> float:
    """Honour the server's "retry in Ns" hint (rate limits); otherwise exponential backoff with full jitter."""
    m = _RETRY_HINT.search(str(error))
    if m:
        return min(float(m.group(1)) + 1.0, MAX_HINTED_WAIT_S)
    return random.uniform(0, base_delay * (2 ** (attempt - 1)))


def with_retries(fn: Callable[[], Any], *, max_retries: int = 3, base_delay: float = 0.8) -> Any:
    attempt = 0
    while True:
        try:
            return fn()
        except TransientLLMError as e:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay(e, attempt, base_delay))


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_model(text: str, schema: type[T]) -> T:
    """Validate model output against a schema, tolerating ```json fences / chatter."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).rstrip("`").strip()
    try:
        return schema.model_validate_json(cleaned)
    except (ValidationError, ValueError):
        m = _JSON_BLOCK.search(cleaned)
        if not m:
            raise LLMError(f"Model did not return JSON for {schema.__name__}: {text[:200]!r}") from None
        try:
            return schema.model_validate(json.loads(m.group(0)))
        except (ValidationError, ValueError) as e:
            raise LLMError(f"Invalid JSON for {schema.__name__}: {e}") from e


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object that conforms to this JSON Schema. "
        "No markdown, no prose outside the JSON.\n"
        f"{json.dumps(schema.model_json_schema(), indent=None)}"
    )


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def _generate_text(
        self, prompt: str, *, system: str | None, tier: Tier, json_schema: type[BaseModel] | None
    ) -> tuple[str, int, int, str]:
        """Return (text, input_tokens, output_tokens, model_name)."""

    @abstractmethod
    def _embed(self, texts: list[str], *, task: EmbedTask) -> list[list[float]]:
        """Provider-specific embedding call (no caching / budgeting)."""

    #: Providers that cost money are metered against the daily budget.
    metered: bool = True
    embed_model: str = "default"

    # ---- public API ------------------------------------------------------
    def embed(self, texts: list[str], *, task: EmbedTask = "document") -> list[list[float]]:
        """Embed texts, serving repeats from the on-disk cache and enforcing the daily budget."""
        if not texts:
            return []
        if not self.metered:
            return self._embed(texts, task=task)
        from app.llm import budget

        cached = budget.get_cached_embeddings(self.embed_model, task, texts)
        missing = [t for t, v in zip(texts, cached) if v is None]
        if missing:
            budget.check_budget()
            from app.config import get_settings

            fresh = with_retries(lambda: self._embed(missing, task=task), max_retries=get_settings().llm_max_retries)
            budget.record_usage(calls=1, tokens=sum(len(t) for t in missing) // 4)
            budget.put_cached_embeddings(self.embed_model, task, missing, fresh)
            by_text = dict(zip(missing, fresh))
            cached = [v if v is not None else by_text[t] for t, v in zip(texts, cached)]
        return cached  # type: ignore[return-value]

    def generate(
        self,
        prompt: str,
        *,
        task: str,
        system: str | None = None,
        tier: Tier = "smart",
    ) -> str:
        text, _ = self._call(prompt, task=task, system=system, tier=tier, json_schema=None)
        return text

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        task: str,
        system: str | None = None,
        tier: Tier = "smart",
    ) -> T:
        text, _ = self._call(prompt, task=task, system=system, tier=tier, json_schema=schema)
        return parse_json_model(text, schema)

    def _call(
        self,
        prompt: str,
        *,
        task: str,
        system: str | None,
        tier: Tier,
        json_schema: type[BaseModel] | None,
    ) -> tuple[str, str]:
        from app.config import get_settings

        if self.metered:
            from app.llm import budget

            budget.check_budget()
        t0 = time.perf_counter()
        text, tin, tout, model = with_retries(
            lambda: self._generate_text(prompt, system=system, tier=tier, json_schema=json_schema),
            max_retries=get_settings().llm_max_retries,
        )
        if self.metered:
            budget.record_usage(calls=1, tokens=tin + tout)
        tracker = current_usage()
        if tracker is not None:
            tracker.add(
                LLMCall(
                    task=task,
                    model=model,
                    input_tokens=tin,
                    output_tokens=tout,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            )
        return text, model
