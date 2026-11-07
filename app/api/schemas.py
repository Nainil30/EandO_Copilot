from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000, examples=["Top 10 suppliers by total excess value"])
    execute: bool = True
    session_id: str | None = Field(default=None, description="Pass the same id to enable follow-up questions.")


class Citation(BaseModel):
    id: str
    kind: str
    title: str
    source: str
    score: float
    signals: dict[str, Any]


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    execution_ms: int | None = None


class AskResponse(BaseModel):
    request_id: str
    session_id: str | None
    status: Literal["ok", "blocked", "clarification", "out_of_scope", "error", "sql_only"]
    question: str
    standalone_question: str
    intent: str
    answer: str
    key_insights: list[str]
    chart: dict[str, Any] | None
    sql: str | None
    plan: str | None
    assumptions: list[str]
    confidence: float | None
    warnings: list[str]
    error: str | None
    result: QueryResult | None
    attempts: list[dict[str, Any]]
    citations: list[Citation]
    retrieval: dict[str, Any]
    cache_hit: dict[str, Any] | None
    trace: list[dict[str, Any]]
    usage: dict[str, Any]
    versions: dict[str, str]
    latency_ms: int


class SQLRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=10_000)


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    rerank: bool = True


class FeedbackRequest(BaseModel):
    request_id: str
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)
    corrected_sql: str | None = Field(default=None, max_length=10_000)
