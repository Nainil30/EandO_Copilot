"""Deterministic offline LLM used for tests, CI and key-less demos.

It is *not* a toy stub: it implements cheap heuristics for every pipeline
task (routing, reranking, few-shot SQL selection, summarisation) so the whole
agentic pipeline can run end-to-end without network access. This is how we
unit-test orchestration logic without paying for tokens or flaky outputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from app.llm.base import EmbedTask, LLMProvider, Tier

_TOKEN = re.compile(r"[a-z0-9_]+")
_DOC = re.compile(r'<doc id="([^"]+)" kind="([^"]+)" title="([^"]*)">\n(.*?)\n</doc>', re.DOTALL)
_CAND = re.compile(r'<candidate id="([^"]+)"')
_TAG = lambda name: re.compile(rf"<{name}[^>]*>\n?(.*?)\n?</{name}>", re.DOTALL)  # noqa: E731

UNSAFE_WORDS = {"drop", "delete", "update", "insert", "truncate", "alter", "grant", "revoke"}
DEFINITION_HINTS = ("what is", "what does", "define", "definition", "meaning of", "explain ")
OFF_TOPIC = {"weather", "joke", "poem", "football", "recipe", "movie"}
STOP = {
    "the",
    "a",
    "an",
    "of",
    "by",
    "for",
    "and",
    "or",
    "to",
    "in",
    "on",
    "with",
    "show",
    "me",
    "list",
    "top",
    "what",
    "which",
    "is",
    "are",
}


def _tokens(s: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(s.lower()):
        if t in STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _tag(name: str, text: str) -> str:
    m = _TAG(name).search(text)
    return m.group(1).strip() if m else ""


class FakeProvider(LLMProvider):
    name = "fake"
    dim = 384

    # ---- embeddings: signed feature hashing over word tokens --------------
    def embed(self, texts: list[str], *, task: EmbedTask = "document") -> list[list[float]]:
        vecs = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in _tokens(t):
                for part in [tok, *tok.split("_")]:
                    h = int(hashlib.md5(part.encode()).hexdigest(), 16)
                    v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / n for x in v])
        return vecs

    # ---- generation --------------------------------------------------------
    def _generate_text(self, prompt, *, system, tier: Tier, json_schema):
        name = json_schema.__name__ if json_schema is not None else ""
        handler = {
            "RouteDecision": self._route,
            "QueryRewrite": self._rewrite,
            "RerankResult": self._rerank,
            "SQLGeneration": self._sql,
            "AnswerSummary": self._summary,
            "DefinitionAnswer": self._definition,
            "JudgeVerdict": self._judge,
        }.get(name)
        out = handler(prompt) if handler else {"text": "ok"}
        text = json.dumps(out) if handler else "ok"
        return text, len(prompt) // 4, len(text) // 4, f"fake-{tier}"

    def _route(self, prompt: str) -> dict:
        q = _tag("user_message", prompt).lower()
        words = set(_TOKEN.findall(q))
        if words & UNSAFE_WORDS:
            return {"intent": "unsafe", "reason": "Request asks to modify data."}
        if words & OFF_TOPIC:
            return {"intent": "out_of_scope", "reason": "Not related to E&O analytics."}
        if any(q.startswith(h) or f" {h}" in q for h in DEFINITION_HINTS) and "show" not in words:
            return {"intent": "definition", "reason": "Asks for a business definition."}
        return {"intent": "data_query", "reason": "Needs data from the warehouse."}

    def _rewrite(self, prompt: str) -> dict:
        q = _tag("latest_question", prompt)
        return {"standalone_question": q, "search_keywords": _tokens(q)[:8]}

    def _rerank(self, prompt: str) -> dict:
        ids = _CAND.findall(prompt)
        m = re.search(r"Select up to (\d+)", prompt)
        n = int(m.group(1)) if m else 6
        return {"selected_ids": ids[:n], "reason": "fake reranker keeps fused order"}

    def _docs(self, prompt: str) -> list[tuple[str, str, str, str]]:
        return _DOC.findall(_tag("context", prompt))

    def _sql(self, prompt: str) -> dict:
        question = _tag("question", prompt)
        failed = _tag("failed_sql", prompt)
        q_tokens = set(_tokens(question))
        best, best_score = None, -1.0
        for _id, kind, title, body in self._docs(prompt):
            if kind != "sql_example":
                continue
            head = set(_tokens(title + " " + body.split("SQL:", 1)[0]))
            score = len(q_tokens & head) / (len(q_tokens | head) or 1)  # Jaccard
            if score > best_score:
                best, best_score = body, score
        sql = ""
        if best and "SQL:" in best:
            sql = best.split("SQL:", 1)[1].strip()
        if not sql and failed:
            sql = failed
        if not sql:
            sql = "SELECT lifecycle_state, COUNT(*) AS parts FROM dim_part GROUP BY lifecycle_state ORDER BY parts DESC LIMIT 100"
        tables = sorted(set(re.findall(r"\b((?:dim|fact)_[a-z_]+)\b", sql)))
        return {
            "plan": "Pick the closest approved SQL example and adapt it.",
            "sql": sql,
            "tables_used": tables,
            "assumptions": ["Offline fake model: reused the most similar approved example."],
            "confidence": 0.5,
            "needs_clarification": False,
            "clarification_question": "",
        }

    def _summary(self, prompt: str) -> dict:
        cols = json.loads(_tag("columns", prompt) or "[]")
        rows_txt = _tag("rows", prompt)
        rows = [json.loads(line) for line in rows_txt.splitlines() if line.strip()]
        m = re.search(r'total="(\d+)"', prompt)
        total = int(m.group(1)) if m else len(rows)
        if not rows:
            return {"answer": "The query returned no rows.", "key_insights": [], "chart": {"type": "none", "x": "", "y": ""}}
        first = dict(zip(cols, rows[0]))
        numeric = [c for c, v in first.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        label = next((c for c in cols if c not in numeric), cols[0])
        y = numeric[-1] if numeric else ""
        insight = f"Top row: {label}={first.get(label)}" + (f", {y}={first.get(y)}" if y else "")
        chart = {"type": "bar", "x": label, "y": y} if y and len(rows) > 1 else {"type": "none", "x": "", "y": ""}
        if y and any(k in label for k in ("week", "date")):
            chart["type"] = "line"
        return {"answer": f"The query returned {total} rows.", "key_insights": [insight], "chart": chart}

    def _definition(self, prompt: str) -> dict:
        docs = self._docs(prompt)
        q = set(_tokens(_tag("question", prompt)))
        docs = sorted(docs, key=lambda d: -len(q & set(_tokens(d[2] + " " + d[3]))))
        if not docs:
            return {"answer": "I don't know based on the knowledge base.", "cited_ids": []}
        _id, _k, _t, body = docs[0]
        return {"answer": body[:600], "cited_ids": [_id]}

    def _judge(self, prompt: str) -> dict:
        return {"faithful": True, "answers_question": True, "score": 4, "rationale": "fake judge"}
