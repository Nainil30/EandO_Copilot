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
        self.embed_model = f"{s.gemini_embed_model}:{s.embedding_dim}"

    def _model(self, tier: Tier) -> str:
        return self._s.gemini_sql_model if tier == "smart" else self._s.gemini_fast_model

    def _generate_text(self, prompt, *, system, tier, json_schema):
        """Try the tier's primary model, then each fallback model on overload/rate-limit errors."""
        models = [self._model(tier), *self._s.gemini_fallback_model_list]
        last: Exception | None = None
        for model in dict.fromkeys(models):  # de-duplicate, keep order
            try:
                return self._generate_once(model, prompt, system=system, json_schema=json_schema)
            except TransientLLMError as e:
                last = e  # overloaded / rate limited: try the next model
        assert last is not None
        raise last

    def _generate_once(self, model: str, prompt: str, *, system, json_schema):
        from google.genai import errors, types

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
            raise LLMError(f"Gemini call failed ({model}): {e}") from e

        usage = getattr(resp, "usage_metadata", None)
        tin = int(getattr(usage, "prompt_token_count", 0) or 0)
        tout = int(getattr(usage, "candidates_token_count", 0) or 0)
        return (resp.text or "").strip(), tin, tout, model

    def _embed(self, texts: list[str], *, task: EmbedTask) -> list[list[float]]:
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
    """Overload, rate-limit, timeout and network errors are transient; auth/validation errors are not."""
    msg = str(e).lower()
    markers = (
        "429",
        "resource_exhausted",
        "500",
        "503",
        "unavailable",
        "timeout",
        "timed out",
        "deadline",
        "connection",
        "max retries exceeded",
        "getaddrinfo",
        "name resolution",
        "remotedisconnected",
    )
    return any(t in msg for t in markers)


def _l2_normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


__all__ = ["GeminiProvider"]
