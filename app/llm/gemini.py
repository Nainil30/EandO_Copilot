"""Google Gemini provider (google-genai SDK).

- Native structured output (response_schema) with a prompt-level fallback
- Asymmetric embeddings: RETRIEVAL_DOCUMENT for KB chunks, RETRIEVAL_QUERY
  for user questions (this matters – using the same task type for both is a
  common, silent recall killer)
- Matryoshka truncation (output_dimensionality) + re-normalisation
"""

from __future__ import annotations

import math

from app.config import get_settings
from app.llm.base import EmbedTask, LLMError, LLMProvider, Tier, TransientLLMError, schema_instructions

_BATCH = 100  # Gemini embed_content max batch size


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        from google import genai

        s = get_settings()
        if not s.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is missing. Set it in .env or use LLM_PROVIDER=fake.")
        self._client = genai.Client(api_key=s.gemini_api_key)
        self._s = s

    def _model(self, tier: Tier) -> str:
        return self._s.gemini_sql_model if tier == "smart" else self._s.gemini_fast_model

    def _generate_text(self, prompt, *, system, tier, json_schema):
        from google.genai import errors, types

        model = self._model(tier)
        cfg: dict = {"temperature": self._s.llm_temperature}
        if system:
            cfg["system_instruction"] = system
        if json_schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = json_schema

        def _do(config_kwargs: dict, contents: str):
            return self._client.models.generate_content(
                model=model, contents=contents, config=types.GenerateContentConfig(**config_kwargs)
            )

        try:
            try:
                resp = _do(cfg, prompt)
            except (ValueError, TypeError, errors.ClientError) as e:
                # Some schema features are unsupported by older SDKs/models:
                # fall back to "JSON mode + schema in the prompt".
                if json_schema is None or _is_retryable(e):
                    raise
                cfg.pop("response_schema", None)
                resp = _do(cfg, f"{prompt}\n\n{schema_instructions(json_schema)}")
        except Exception as e:  # noqa: BLE001 – normalise provider errors
            if _is_retryable(e):
                raise TransientLLMError(str(e)) from e
            raise LLMError(f"Gemini call failed: {e}") from e

        usage = getattr(resp, "usage_metadata", None)
        tin = int(getattr(usage, "prompt_token_count", 0) or 0)
        tout = int(getattr(usage, "candidates_token_count", 0) or 0)
        return (resp.text or "").strip(), tin, tout, model

    def embed(self, texts: list[str], *, task: EmbedTask = "document") -> list[list[float]]:
        from google.genai import types

        if not texts:
            return []
        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i : i + _BATCH]
            try:
                resp = self._client.models.embed_content(
                    model=self._s.gemini_embed_model,
                    contents=batch,
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._s.embedding_dim),
                )
            except Exception as e:  # noqa: BLE001
                if _is_retryable(e):
                    raise TransientLLMError(str(e)) from e
                raise LLMError(f"Gemini embedding failed: {e}") from e
            out.extend(_l2_normalise(list(e.values)) for e in resp.embeddings)
        return out


def _is_retryable(e: Exception) -> bool:
    msg = str(e).lower()
    return any(t in msg for t in ("429", "resource_exhausted", "503", "500", "unavailable", "timeout", "deadline"))


def _l2_normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


__all__ = ["GeminiProvider"]
