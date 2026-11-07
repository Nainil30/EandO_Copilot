"""Short-term conversation memory for multi-turn analytics.

Enables follow-ups like:
  Q1: "Top 10 suppliers by excess value"
  Q2: "only the ones in Taiwan"        -> rewritten using Q1 + its SQL
In-process store with TTL + window; swap for Redis in a multi-replica deployment.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Turn:
    question: str
    standalone_question: str
    sql: str
    answer: str


@dataclass
class _Session:
    turns: list[Turn] = field(default_factory=list)
    updated: float = field(default_factory=time.time)


class ConversationMemory:
    def __init__(self, window: int = 4, ttl_s: int = 3600, max_sessions: int = 1000) -> None:
        self.window, self.ttl_s, self.max_sessions = window, ttl_s, max_sessions
        self._data: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def history(self, session_id: str | None) -> list[Turn]:
        if not session_id:
            return []
        with self._lock:
            sess = self._data.get(session_id)
            if not sess or time.time() - sess.updated > self.ttl_s:
                return []
            return list(sess.turns[-self.window :])

    def append(self, session_id: str | None, turn: Turn) -> None:
        if not session_id:
            return
        with self._lock:
            sess = self._data.setdefault(session_id, _Session())
            sess.turns.append(turn)
            sess.turns = sess.turns[-self.window :]
            sess.updated = time.time()
            if len(self._data) > self.max_sessions:
                oldest = min(self._data, key=lambda k: self._data[k].updated)
                self._data.pop(oldest, None)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)


def format_history(turns: list[Turn]) -> str:
    if not turns:
        return "(none)"
    lines = []
    for i, t in enumerate(turns, 1):
        lines.append(f"Turn {i} user: {t.question}")
        if t.sql:
            lines.append(f"Turn {i} sql: {' '.join(t.sql.split())[:400]}")
        if t.answer:
            lines.append(f"Turn {i} answer: {t.answer[:200]}")
    return "\n".join(lines)


memory = ConversationMemory()
