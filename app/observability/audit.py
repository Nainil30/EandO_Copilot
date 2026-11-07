"""Append-only JSONL audit trail + feedback log + aggregate metrics.

Each /ask request writes one record: question, retrieved & reranked chunk ids,
generated SQL, repair attempts, row count, per-stage latency, tokens, cost,
prompt/KB/model versions. That is enough to (a) replay any answer, (b) compute
online quality metrics, and (c) satisfy governance reviews.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from app.config import get_settings

_lock = threading.Lock()


def _path(name: str) -> Path:
    d = Path(get_settings().audit_log_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _append(name: str, record: dict[str, Any]) -> None:
    line = json.dumps({"ts": time.time(), **record}, ensure_ascii=False, default=str)
    with _lock, _path(name).open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_audit(record: dict[str, Any]) -> None:
    _append("audit.jsonl", record)


def write_feedback(record: dict[str, Any]) -> None:
    _append("feedback.jsonl", record)


def read_jsonl(name: str, limit: int = 1000) -> list[dict[str, Any]]:
    p = _path(name)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def metrics(limit: int = 1000) -> dict[str, Any]:
    recs = [r for r in read_jsonl("audit.jsonl", limit) if r.get("type") == "ask"]
    fb = read_jsonl("feedback.jsonl", limit)
    if not recs:
        return {"requests": 0}
    lat = [r.get("latency_ms", 0) for r in recs]
    status: dict[str, int] = {}
    for r in recs:
        status[r.get("status", "?")] = status.get(r.get("status", "?"), 0) + 1
    p95 = statistics.quantiles(lat, n=20)[-1] if len(lat) >= 20 else max(lat)
    up = sum(1 for f in fb if f.get("rating") == "up")
    return {
        "requests": len(recs),
        "status_breakdown": status,
        "success_rate": round(status.get("ok", 0) / len(recs), 3),
        "cache_hit_rate": round(sum(1 for r in recs if r.get("cache_hit")) / len(recs), 3),
        "repair_rate": round(sum(1 for r in recs if (r.get("repair_attempts") or 0) > 0) / len(recs), 3),
        "latency_ms": {"p50": int(statistics.median(lat)), "p95": int(p95), "mean": int(statistics.mean(lat))},
        "tokens_total": sum(
            (r.get("usage") or {}).get("input_tokens", 0) + (r.get("usage") or {}).get("output_tokens", 0) for r in recs
        ),
        "est_cost_usd_total": round(sum((r.get("usage") or {}).get("est_cost_usd", 0) for r in recs), 4),
        "feedback": {"total": len(fb), "thumbs_up": up, "thumbs_down": len(fb) - up},
    }
