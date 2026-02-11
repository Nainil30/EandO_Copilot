# app/core/audit.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def _now_ms() -> int:
    return int(time.time() * 1000)


def write_audit_event(event: Dict[str, Any]) -> None:
    """
    Append a single JSON event to a JSONL file (one JSON object per line).
    Robust + easy to analyze later.
    """
    log_dir = os.getenv("AUDIT_LOG_DIR", ".logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    path = Path(log_dir) / "audit.jsonl"

    enriched = {
        "ts_ms": _now_ms(),
        "kb_version": os.getenv("KB_VERSION", "v1"),
        "prompt_version": os.getenv("PROMPT_VERSION", "v1"),
        "use_rerank": os.getenv("USE_RERANK", "true"),
        **event,
    }

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(enriched, ensure_ascii=False) + "\n")

