"""OpenAI-compatible provider (OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio, Groq...).

Implemented with plain httpx so there is no hard dependency on the openai SDK.
Point OPENAI_BASE_URL at http://localhost:11434/v1 to run fully local with Ollama.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.llm.base import EmbedTask, LLMError, LLMProvider, Tier, TransientLLMError, schema_instructions


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self._s = get_settings()
        headers = {"Content-Type": "application/json"}
        if self._s.openai_api_key:
            headers["Authorization"] = f"Bearer {self._s.openai_api_key}"
        self._http = httpx.Client(base_url=self._s.openai_base_url, headers=headers, timeout=60)

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._http.post(path, json=payload)
        except httpx.TimeoutException as e:
            raise TransientLLMError(str(e)) from e
        if r.status_code in (429, 500, 502, 503, 504):
            raise TransientLLMError(f"{r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code}: {r.text[:500]}")
        return r.json()

    def _generate_text(self, prompt, *, system, tier: Tier, json_schema):
        model = self._s.openai_sql_model if tier == "smart" else self._s.openai_fast_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user = prompt if json_schema is None else f"{prompt}\n\n{schema_instructions(json_schema)}"
        messages.append({"role": "user", "content": user})
        payload: dict = {"model": model, "messages": messages, "temperature": self._s.llm_temperature}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        data = self._post("/chat/completions", payload)
        usage = data.get("usage") or {}
        text = data["choices"][0]["message"]["content"] or ""
        return text.strip(), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), model

    def embed(self, texts: list[str], *, task: EmbedTask = "document") -> list[list[float]]:
        if not texts:
            return []
        data = self._post("/embeddings", {"model": self._s.openai_embed_model, "input": texts})
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
