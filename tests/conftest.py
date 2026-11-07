"""Test fixtures: everything runs offline with the deterministic fake LLM and
an in-memory fake warehouse, so `pytest` needs no API key, no Docker and no DB."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def _env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eando")
    os.environ.update(
        {
            "LLM_PROVIDER": "fake",
            "CHROMA_DIR": str(tmp / "chroma"),
            "CACHE_DIR": str(tmp / "cache"),
            "AUDIT_LOG_DIR": str(tmp / "logs"),
            "KB_VERSION": "test",
            "API_KEYS": "",
            "RATE_LIMIT_PER_MINUTE": "1000",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield


@pytest.fixture(scope="session")
def kb(_env):
    from app.rag.kb_builder import build_kb

    return build_kb(include_live_values=False)


class FakeWarehouse:
    """Stands in for Postgres: records SQL and returns canned rows (or an error)."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail_times = 0
        self.error = 'column "lifecycle" does not exist'

    def explain(self, sql):
        self.calls.append(sql)
        if self.fail_times > 0:
            self.fail_times -= 1
            from app.sql.executor import SQLExecutionError

            raise SQLExecutionError(self.error)
        return {"estimated_rows": 5, "total_cost": 1.0, "node": "Seq Scan"}

    def run_sql(self, sql, **_):
        return {
            "columns": ["supplier_name", "total_excess_value"],
            "rows": [["Acme Corp", 1200.5], ["Beta Ltd", 900.0], ["Gamma", 10.0]],
            "row_count": 3,
            "truncated": False,
            "execution_ms": 2,
        }


@pytest.fixture()
def warehouse(monkeypatch, kb):
    from app.rag import semantic_cache
    from app.sql import executor

    semantic_cache.clear_semantic_cache()
    wh = FakeWarehouse()
    monkeypatch.setattr(executor, "explain", wh.explain)
    monkeypatch.setattr(executor, "run_sql", wh.run_sql)
    monkeypatch.setattr(executor, "ping", lambda: True)
    return wh
