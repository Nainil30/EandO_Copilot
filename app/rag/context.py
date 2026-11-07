"""Context assembly with a character budget.

Ordering matters ("lost in the middle"): we put schema first (what exists),
then join edges, business definitions, and finally few-shot SQL examples
closest to the question – right before the question itself.
"""

from __future__ import annotations

from app.rag.documents import Chunk

_ORDER = {"table": 0, "join": 1, "glossary": 2, "sql_example": 3}


def build_context(chunks: list[Chunk], max_chars: int = 9000) -> tuple[str, list[Chunk]]:
    ordered = sorted(chunks, key=lambda c: _ORDER.get(c.kind, 9))
    blocks, used, total = [], [], 0
    for c in ordered:
        block = f'<doc id="{c.id}" kind="{c.kind}" title="{c.title}">\n{c.text.strip()}\n</doc>'
        if total + len(block) > max_chars and used:
            continue  # skip oversize chunk but keep trying smaller ones
        blocks.append(block)
        used.append(c)
        total += len(block)
    return "\n".join(blocks), used
