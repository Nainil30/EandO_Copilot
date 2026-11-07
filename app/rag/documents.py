from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["table", "join", "glossary", "sql_example"]


@dataclass
class Chunk:
    id: str
    kind: Kind
    title: str
    text: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # retrieval-time annotations
    score: float = 0.0
    signals: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_id(kind: str, title: str, text: str) -> str:
        """Content-addressed id: re-building an unchanged KB is idempotent."""
        h = hashlib.sha256(f"{kind}|{title}|{text}".encode()).hexdigest()[:12]
        return f"{kind}:{h}"

    def chroma_metadata(self) -> dict[str, Any]:
        meta = {"kind": self.kind, "title": self.title, "source": self.source}
        for k, v in self.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            elif isinstance(v, (list, tuple)):
                meta[k] = ",".join(map(str, v))  # Chroma metadata must be scalar
        return meta

    @classmethod
    def from_store(cls, id: str, text: str, meta: dict[str, Any]) -> Chunk:
        meta = dict(meta or {})
        kind = meta.pop("kind", "glossary")
        title = meta.pop("title", "")
        source = meta.pop("source", "")
        return cls(id=id, kind=kind, title=title, text=text, source=source, metadata=meta)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["preview"] = self.text[:240]
        d.pop("text")
        return d
