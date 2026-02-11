from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import httpx


@dataclass
class ApiClient:
    """
    Minimal client for the FastAPI backend.

    Design goals:
    - Always return a dict (even for errors)
    - Never raise for HTTP status in the UI layer (we surface the error body instead)
    """
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 60.0

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def health(self) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.get(self._url("/health"))
            return self._safe_json(r)

    def schema(self) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.get(self._url("/schema"))
            return self._safe_json(r)

    def rag_build(self) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(self._url("/rag/build"), json={})
            return self._safe_json(r)

    def rag_debug(self, question: str) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(self._url("/rag/debug"), json={"question": question})
            return self._safe_json(r)

    def nlq(self, question: str) -> Dict[str, Any]:
        # Must match Swagger payload exactly:
        # {"question": "..."}
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(self._url("/nlq"), json={"question": question})
            return self._safe_json(r)

    def query(self, sql: str) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(self._url("/query"), json={"sql": sql})
            return self._safe_json(r)

    @staticmethod
    def _safe_json(r: httpx.Response) -> Dict[str, Any]:
        """
        Always return a dict.
        If backend returns non-JSON (rare), return a structured error dict.
        """
        try:
            data = r.json()
            # If the backend returned a list/string, normalize into dict
            if isinstance(data, dict):
                return data
            return {"detail": {"error": "unexpected_json_type", "value": data, "status_code": r.status_code}}
        except Exception:
            return {
                "detail": {
                    "error": "non_json_response",
                    "status_code": r.status_code,
                    "text": r.text,
                }
            }
