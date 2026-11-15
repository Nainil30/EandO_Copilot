"""Datadog metrics for the E&O Copilot (standalone, not yet wired into the pipeline).

Why this file exists:
    The JSONL audit log (app/observability/audit.py) is great for replaying one request
    after the fact. It is bad at answering "is the service healthy *right now*?" and it
    cannot page anyone. Datadog fills that gap: the app pushes small numeric metrics,
    Datadog aggregates them, draws dashboards and fires alerts.

How it works:
    The app sends each metric as a tiny UDP packet to the Datadog Agent's DogStatsD
    listener (port 8125). UDP is fire-and-forget, so a slow or missing agent never slows
    down a user request. The agent batches the packets and forwards them to Datadog.

Design:
    - MetricsEmitter is the interface (an ABC). The rest of the app depends only on it.
    - DatadogEmitter sends real metrics; NoopEmitter does nothing (local dev, tests, CI).
    - get_emitter() picks one from environment variables, so switching is config, not code.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# ---- metric names (one place, so dashboards and code never drift apart) -------------
REQUEST_COUNT = "eando.request.count"
REQUEST_LATENCY_MS = "eando.request.latency_ms"
LLM_TOKENS_USED = "eando.llm.tokens_used"
LLM_COST_USD = "eando.llm.cost_usd"
CACHE_HIT = "eando.cache.hit"
SQL_REPAIR_ATTEMPTS = "eando.sql.repair_attempts"
GUARDRAIL_BLOCKED = "eando.guardrail.blocked"


class MetricsEmitter(ABC):
    """Interface every emitter implements. Three primitive metric types."""

    @abstractmethod
    def increment(self, metric: str, tags: dict) -> None:
        """Counter: add 1. Datadog shows it as a rate (events per second / minute)."""

    @abstractmethod
    def histogram(self, metric: str, value: float, tags: dict) -> None:
        """Distribution of values: Datadog computes avg, median, p95, max for you."""

    @abstractmethod
    def gauge(self, metric: str, value: float, tags: dict) -> None:
        """Point-in-time value: the last value reported wins for each interval."""

    # ---- domain helpers: the 7 metrics the copilot emits ----------------------------
    # They live on the base class so every emitter gets them for free.

    def record_request(self, intent: str, status: str, latency_ms: float) -> None:
        # eando.request.count{intent,status}: traffic and error rate.
        # Alert when status:error / total > 5% over 10 min (the pipeline is failing).
        self.increment(REQUEST_COUNT, {"intent": intent, "status": status})
        # eando.request.latency_ms{intent}: user-facing speed.
        # Alert when p95 > 15 s (LLM provider is slow or the repair loop is running a lot).
        self.histogram(REQUEST_LATENCY_MS, latency_ms, {"intent": intent})

    def record_llm_usage(self, model: str, tokens: int, cost_usd: float) -> None:
        # eando.llm.tokens_used{model}: spend driver per model tier.
        # Alert on a sudden jump: a prompt change or a retrieval bug that bloats context.
        self.gauge(LLM_TOKENS_USED, tokens, {"model": model})
        # eando.llm.cost_usd: dollars per request.
        # Alert when the daily sum exceeds the budget (catches runaway loops or abuse).
        self.gauge(LLM_COST_USD, cost_usd, {})

    def record_cache_hit(self) -> None:
        # eando.cache.hit: semantic cache hits (each one is 0 LLM calls).
        # Divide by request.count for hit rate; a drop to ~0 after a deploy means the
        # cache key changed (KB_VERSION / PROMPT_VERSION bump) or the cache is broken.
        self.increment(CACHE_HIT, {})

    def record_sql_repair(self, attempts: int) -> None:
        # eando.sql.repair_attempts: how many self-repair rounds a request needed.
        # A rising average is a data-quality signal: schema drifted, a column was renamed,
        # or retrieval stopped returning the right tables.
        self.histogram(SQL_REPAIR_ATTEMPTS, attempts, {})

    def record_guardrail_block(self, reason: str) -> None:
        # eando.guardrail.blocked{reason}: inputs refused before any LLM call.
        # A spike usually means someone is probing the system (prompt injection attempts).
        self.increment(GUARDRAIL_BLOCKED, {"reason": reason})


def _to_tags(tags: dict) -> list[str]:
    """Datadog wants tags as ["key:value", ...], not a dict."""
    return [f"{k}:{v}" for k, v in tags.items()]


class DatadogEmitter(MetricsEmitter):
    """Sends metrics to the Datadog Agent over DogStatsD (UDP)."""

    def __init__(self, host: str = "localhost", port: int = 8125) -> None:
        # Imported here, not at the top, so the app and tests run even when the
        # `datadog` package is not installed and metrics are disabled.
        from datadog.dogstatsd import DogStatsd

        # constant_tags are added to every metric so dashboards can filter by service/env.
        self._client = DogStatsd(
            host=host,
            port=port,
            constant_tags=["service:eando-copilot", f"env:{os.getenv('ENVIRONMENT', 'development')}"],
        )

    def increment(self, metric: str, tags: dict) -> None:
        self._client.increment(metric, tags=_to_tags(tags))

    def histogram(self, metric: str, value: float, tags: dict) -> None:
        self._client.histogram(metric, value, tags=_to_tags(tags))

    def gauge(self, metric: str, value: float, tags: dict) -> None:
        self._client.gauge(metric, value, tags=_to_tags(tags))


class NoopEmitter(MetricsEmitter):
    """Same interface, does nothing. Callers never need `if datadog_enabled:` checks."""

    def increment(self, metric: str, tags: dict) -> None:
        return None

    def histogram(self, metric: str, value: float, tags: dict) -> None:
        return None

    def gauge(self, metric: str, value: float, tags: dict) -> None:
        return None


def get_emitter() -> MetricsEmitter:
    """Factory: DatadogEmitter if DATADOG_ENABLED is true, otherwise NoopEmitter."""
    enabled = os.getenv("DATADOG_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return NoopEmitter()
    # In Kubernetes this is usually the node's IP (Datadog Agent runs as a DaemonSet).
    host = os.getenv("DATADOG_STATSD_HOST", "localhost")
    return DatadogEmitter(host=host)
