"""Centralised, typed configuration (12-factor style).

Every tunable knob of the system lives here and can be overridden via
environment variables or a `.env` file. Using pydantic-settings gives us
validation + type coercion for free, and one place to document defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- general -------------------------------------------------------
    environment: str = "development"
    log_level: str = "INFO"

    # ---- database ------------------------------------------------------
    database_url: str = "postgresql+psycopg2://copilot_user:dev_password@localhost:5432/eando_copilot"
    # Optional dedicated read-only role used for executing LLM-generated SQL.
    readonly_database_url: str | None = None
    sql_statement_timeout_ms: int = 8000
    sql_max_rows: int = 1000
    sql_default_limit: int = 100

    # ---- LLM provider ---------------------------------------------------
    # gemini  -> Google Gemini (default, free tier friendly)
    # openai  -> any OpenAI-compatible endpoint (OpenAI, Azure, Ollama, vLLM, LM Studio)
    # fake    -> deterministic offline provider used in tests / CI / demos w/o keys
    llm_provider: Literal["gemini", "openai", "fake"] = "gemini"
    gemini_api_key: str | None = None
    gemini_sql_model: str = "gemini-2.5-flash"
    gemini_fast_model: str = "gemini-2.5-flash-lite"
    gemini_embed_model: str = "gemini-embedding-001"
    embedding_dim: int = 768  # Matryoshka truncation for gemini-embedding-001

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_sql_model: str = "gpt-4.1-mini"
    openai_fast_model: str = "gpt-4.1-nano"
    openai_embed_model: str = "text-embedding-3-small"

    llm_temperature: float = 0.0
    llm_max_retries: int = 3

    # ---- RAG -----------------------------------------------------------
    chroma_dir: str = ".chroma"
    kb_version: str = "v2"
    retrieval_top_k: int = 12  # candidates after hybrid fusion
    context_top_n: int = 6  # chunks kept after rerank
    rrf_k: int = 60  # Reciprocal Rank Fusion constant (Cormack et al. 2009)
    context_max_chars: int = 9000
    use_rerank: bool = True
    use_query_rewrite: bool = True
    use_semantic_cache: bool = True
    semantic_cache_threshold: float = 0.95
    semantic_cache_ttl_s: int = 3600
    cache_dir: str = ".cache/rag"

    # ---- agentic loop ---------------------------------------------------
    max_repair_attempts: int = 2
    summarize_results: bool = True

    # ---- prompt versions (traceability) ----------------------------------
    prompt_version: str = "sql-v3"

    # ---- API security ----------------------------------------------------
    api_keys: str = ""  # comma-separated; empty = auth disabled (dev)
    rate_limit_per_minute: int = 60
    cors_origins: str = "*"

    # ---- observability ---------------------------------------------------
    audit_log_dir: str = ".logs"

    # Cost table (USD per 1M tokens) – used for per-request cost estimates.
    price_in_per_m: float = Field(default=0.30)
    price_out_per_m: float = Field(default=2.50)

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def sqlalchemy_url(self) -> str:
        return _normalise_pg_url(self.database_url)

    @property
    def sqlalchemy_readonly_url(self) -> str:
        return _normalise_pg_url(self.readonly_database_url or self.database_url)


def _normalise_pg_url(url: str) -> str:
    # Accept plain postgres:// URLs (Heroku/Render/Supabase style).
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
