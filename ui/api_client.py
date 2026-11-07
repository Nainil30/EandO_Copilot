"""Small typed client for the E&O Copilot API (used by the Streamlit UI)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_API_URL = os.getenv("EANDO_API_URL", "http://127.0.0.1:8000")


@dataclass
class ApiClient:
    base_url: str = DEFAULT_API_URL
    api_key: str | None = None
    timeout_s: float = 180.0
    _headers: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.api_key:
            self._headers["X-API-Key"] = self.api_key

    def _req(self, method: str, path: str, **kw) -> dict[str, Any] | list:
        try:
            with httpx.Client(timeout=self.timeout_s, headers=self._headers) as c:
                r = c.request(method, f"{self.base_url}{path}", **kw)
        except httpx.HTTPError as e:
            return {"error": "connection_failed", "detail": str(e)}
        try:
            data = r.json()
        except ValueError:
            data = {"error": "non_json_response", "detail": r.text[:500]}
        if r.status_code >= 400 and isinstance(data, dict):
            data.setdefault("error", f"http_{r.status_code}")
        return data

    # ---- endpoints -----------------------------------------------------
    def health(self):
        return self._req("GET", "/health")

    def ready(self):
        return self._req("GET", "/ready")

    def ask(self, question: str, *, execute: bool = True, session_id: str | None = None):
        return self._req("POST", "/v1/ask", json={"question": question, "execute": execute, "session_id": session_id})

    def ask_stream(self, question: str, *, execute: bool = True, session_id: str | None = None) -> Iterator[dict]:
        """Yield pipeline events from the SSE endpoint."""
        payload = {"question": question, "execute": execute, "session_id": session_id}
        try:
            with (
                httpx.Client(timeout=self.timeout_s, headers=self._headers) as c,
                c.stream("POST", f"{self.base_url}/v1/ask/stream", json=payload) as r,
            ):
                if r.status_code >= 400:
                    r.read()
                    yield {"type": "error", "detail": r.text[:500], "status": r.status_code}
                    return
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except httpx.HTTPError as e:
            yield {"type": "error", "detail": str(e)}

    def sql(self, sql: str):
        return self._req("POST", "/v1/sql", json={"sql": sql})

    def kb_build(self):
        return self._req("POST", "/v1/kb/build")

    def kb_stats(self):
        return self._req("GET", "/v1/kb/stats")

    def kb_chunks(self, kind: str | None = None):
        return self._req("GET", "/v1/kb/chunks", params={"kind": kind} if kind else None)

    def retrieve_debug(self, question: str, rerank: bool = True):
        return self._req("POST", "/v1/retrieve/debug", json={"question": question, "rerank": rerank})

    def feedback(self, request_id: str, rating: str, comment: str | None = None):
        return self._req("POST", "/v1/feedback", json={"request_id": request_id, "rating": rating, "comment": comment})

    def metrics(self):
        return self._req("GET", "/v1/metrics")

    def audit(self, limit: int = 50):
        return self._req("GET", "/v1/audit", params={"limit": limit})

    def schema(self):
        return self._req("GET", "/v1/schema")
