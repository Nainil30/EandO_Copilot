"""Hard daily usage cap + embedding cache: keeps API spend near zero.

Two independent protections:

1. **Daily budget**: every LLM generation and embedding request is counted
   (calls + tokens) in a small on-disk ledger keyed by UTC date. Once either
   limit is reached, further calls raise `BudgetExceededError` *before* any
   network request is made. A bug, a retry storm or a long eval run can
   therefore never run up a bill. Set a limit to 0 to disable it.

2. **Embedding cache**: embeddings are deterministic for (model, task, text),
   so they are cached on disk. Rebuilding the knowledge base, or asking the
   same question again, costs zero API calls.

The offline `fake` provider is never counted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
from typing import Any

from diskcache import Cache

from app.config import get_settings

_lock = threading.Lock()
_cache: Cache | None = None


class BudgetExceededError(RuntimeError):
    """Raised instead of calling the API once today's budget is used up."""


def _store() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache(f"{get_settings().cache_dir}/llm")
    return _cache


def _today_key() -> str:
    return f"budget:{dt.datetime.now(dt.UTC).date().isoformat()}"


def usage_today() -> dict[str, Any]:
    s = get_settings()
    used = _store().get(_today_key(), {"calls": 0, "tokens": 0})
    return {
        "date_utc": _today_key().split(":", 1)[1],
        "calls": used["calls"],
        "tokens": used["tokens"],
        "call_limit": s.llm_daily_call_limit,
        "token_limit": s.llm_daily_token_limit,
    }


def check_budget() -> None:
    s = get_settings()
    u = usage_today()
    if s.llm_daily_call_limit and u["calls"] >= s.llm_daily_call_limit:
        raise BudgetExceededError(
            f"Daily LLM call limit reached ({u['calls']}/{s.llm_daily_call_limit}). "
            "Raise LLM_DAILY_CALL_LIMIT in .env or wait until tomorrow (UTC)."
        )
    if s.llm_daily_token_limit and u["tokens"] >= s.llm_daily_token_limit:
        raise BudgetExceededError(
            f"Daily LLM token limit reached ({u['tokens']}/{s.llm_daily_token_limit}). "
            "Raise LLM_DAILY_TOKEN_LIMIT in .env or wait until tomorrow (UTC)."
        )


def record_usage(calls: int, tokens: int) -> None:
    with _lock:
        key = _today_key()
        used = _store().get(key, {"calls": 0, "tokens": 0})
        used = {"calls": used["calls"] + calls, "tokens": used["tokens"] + tokens}
        _store().set(key, used, expire=3 * 86400)


# ---- embedding cache -------------------------------------------------------
def _emb_key(model: str, task: str, text: str) -> str:
    return "emb:" + hashlib.sha256(f"{model}|{task}|{text}".encode()).hexdigest()


def get_cached_embeddings(model: str, task: str, texts: list[str]) -> list[list[float] | None]:
    store = _store()
    return [store.get(_emb_key(model, task, t)) for t in texts]


def put_cached_embeddings(model: str, task: str, texts: list[str], vectors: list[list[float]]) -> None:
    store = _store()
    for t, v in zip(texts, vectors):
        store.set(_emb_key(model, task, t), v, expire=30 * 86400)
