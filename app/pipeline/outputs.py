"""Structured-output contracts for every LLM step.

Each model is passed to the provider as a response schema, so the LLM is
constrained to emit valid JSON and we get typed objects back. Fields are all
required (no defaults / Optional) because that is the most portable subset of
JSON Schema across Gemini, OpenAI and local models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    intent: Literal["data_query", "definition", "out_of_scope", "unsafe"] = Field(
        description="data_query: needs SQL over the warehouse; definition: business/glossary "
        "question answerable from docs; out_of_scope: unrelated to E&O/supply chain; "
        "unsafe: asks to modify/delete data or to bypass rules."
    )
    reason: str = Field(description="One short sentence.")


class QueryRewrite(BaseModel):
    standalone_question: str = Field(
        description="The user's latest question rewritten to be fully self-contained, "
        "resolving pronouns/ellipsis using the chat history."
    )
    search_keywords: list[str] = Field(
        description="3-8 schema-oriented keywords (table/column/metric names) to boost keyword search."
    )


class RerankResult(BaseModel):
    selected_ids: list[str] = Field(description="Ids of the most useful chunks, best first.")
    reason: str = Field(description="One short sentence.")


class SQLGeneration(BaseModel):
    plan: str = Field(description="2-4 short steps: which tables, joins, filters, aggregations.")
    sql: str = Field(description="One PostgreSQL SELECT statement, no semicolon. Empty if clarification needed.")
    tables_used: list[str]
    assumptions: list[str] = Field(description="Interpretation choices made for ambiguous parts.")
    confidence: float = Field(ge=0, le=1, description="Self-assessed probability the SQL is correct.")
    needs_clarification: bool
    clarification_question: str = Field(description="Empty string unless needs_clarification.")


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "none"]
    x: str = Field(description="Column for x axis, or empty.")
    y: str = Field(description="Numeric column for y axis, or empty.")


class AnswerSummary(BaseModel):
    answer: str = Field(description="2-4 sentence business answer grounded ONLY in the rows provided.")
    key_insights: list[str] = Field(description="Up to 3 bullet insights with concrete numbers.")
    chart: ChartSpec


class DefinitionAnswer(BaseModel):
    answer: str
    cited_ids: list[str]


class JudgeVerdict(BaseModel):
    """LLM-as-judge used by the offline evaluation harness."""

    faithful: bool = Field(description="Is the answer fully supported by the SQL result rows?")
    answers_question: bool
    score: int = Field(ge=1, le=5)
    rationale: str
