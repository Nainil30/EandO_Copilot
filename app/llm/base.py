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


def with_retries(fn: Callable[[], Any], *, max_retries: int = 3, base_delay: float = 0.8) -> Any:
    attempt = 0
    while True:
        try:
            return fn()
        except TransientLLMError:
            attempt += 1
            if attempt > max_retries:
                raise
            # exponential backoff with full jitter
            time.sleep(random.uniform(0, base_delay * (2 ** (attempt - 1))))


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
    def embed(self, texts: list[str], *, task: EmbedTask = "document") -> list[list[float]]: ...

    # ---- public API ------------------------------------------------------
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

        t0 = time.perf_counter()
        text, tin, tout, model = with_retries(
            lambda: self._generate_text(prompt, system=system, tier=tier, json_schema=json_schema),
            max_retries=get_settings().llm_max_retries,
        )
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
