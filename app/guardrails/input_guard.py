"""Input guardrail (defence in depth layer #1) – runs before any LLM call.

Cheap deterministic checks catch the obvious attacks for free:
- prompt-injection / jailbreak phrasing ("ignore previous instructions", ...)
- explicit data-modification intent (DROP/DELETE/UPDATE/TRUNCATE ...)
- oversized inputs (token-stuffing / cost attacks)

It is intentionally conservative; anything subtler is caught by the LLM router
("unsafe" intent), the SQL AST validator and finally the READ ONLY transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_QUESTION_CHARS = 1000

_INJECTION = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"disregard (the |your )?(system|previous) (prompt|instructions)",
    r"you are now (a|an|in) ",
    r"\b(developer|dan|jailbreak) mode\b",
    r"reveal (your|the) (system )?prompt",
    r"print (your|the) (system )?prompt",
    r"</?(system|context|question|doc)>",
]
_DESTRUCTIVE = [
    r"\b(drop|truncate|alter)\s+(table|schema|database|index|view)\b",
    r"\bdelete\s+(from|all|every|the)\b",
    r"\bupdate\s+\w+\s+set\b",
    r"\binsert\s+into\b",
    r"\b(grant|revoke)\s+\w+",
    r"\b(remove|wipe|erase|destroy)\s+(all\s+)?(the\s+)?(rows|records|data|tables?|inventory)\b",
    r"\bset\s+\w+\s*=\s*",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in _INJECTION]
_DES = [re.compile(p, re.IGNORECASE) for p in _DESTRUCTIVE]


@dataclass
class GuardResult:
    allowed: bool
    category: str = "ok"  # ok | too_long | prompt_injection | destructive_intent | empty
    reason: str = ""


def check_input(question: str) -> GuardResult:
    q = (question or "").strip()
    if not q:
        return GuardResult(False, "empty", "Question is empty.")
    if len(q) > MAX_QUESTION_CHARS:
        return GuardResult(False, "too_long", f"Question exceeds {MAX_QUESTION_CHARS} characters.")
    for p in _INJ:
        if p.search(q):
            return GuardResult(False, "prompt_injection", "The request looks like a prompt-injection attempt.")
    for p in _DES:
        if p.search(q):
            return GuardResult(
                False,
                "destructive_intent",
                "This copilot is read-only: it can analyse data but never modify or delete it.",
            )
    return GuardResult(True)
