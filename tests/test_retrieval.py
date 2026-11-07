from app.rag.bm25 import tokenize
from app.rag.context import build_context
from app.rag.documents import Chunk
from app.rag.kb_builder import glossary_chunks, schema_chunks, split_markdown, sql_example_chunks
from app.rag.retriever import expand_with_schema_graph, retrieve, rrf_fuse


def _c(i, kind="table", title=None, **meta):
    return Chunk(i, kind, title or i, f"text {i}", "test", meta)


def test_rrf_rewards_agreement_between_retrievers():
    dense = [_c("a"), _c("b"), _c("c")]
    sparse = [_c("c"), _c("a"), _c("d")]
    fused = rrf_fuse({"dense": dense, "bm25": sparse}, k=60)
    ids = [c.id for c in fused]
    assert ids[:2] == ["a", "c"]  # appear in both lists
    assert set(ids) == {"a", "b", "c", "d"}
    assert fused[0].signals["dense_rank"] == 1 and fused[0].signals["bm25_rank"] == 2


def test_rrf_scores_are_monotonic():
    fused = rrf_fuse({"x": [_c("a"), _c("b")]})
    assert fused[0].score > fused[1].score


def test_bm25_tokenizer_splits_snake_case():
    toks = tokenize("fact_excess_calculation.calculated_excess")
    assert "fact_excess_calculation" in toks and "excess" in toks and "calculated" in toks


def test_schema_graph_expansion_adds_bridge_tables_and_joins():
    pool = schema_chunks()
    example = Chunk("ex", "sql_example", "x", "SQL", "t", {"tables": "dim_supplier,fact_excess_calculation"})
    out = expand_with_schema_graph([example], pool)
    titles = {c.title for c in out}
    assert {"dim_supplier", "fact_excess_calculation"} <= titles
    assert "fact_excess_calculation -> dim_supplier" in titles


def test_markdown_splitting_and_sql_examples():
    sections = split_markdown("# T\n## A\nbody a\n## B\nbody b\n")
    assert sections == [("A", "body a"), ("B", "body b")]
    examples = sql_example_chunks()
    assert len(examples) >= 15
    assert all("SQL:" in e.text and "SELECT" in e.text.upper() for e in examples)
    assert len(glossary_chunks()) >= 8


def test_context_orders_schema_before_examples_and_respects_budget():
    chunks = [_c("e", "sql_example"), _c("t", "table"), _c("g", "glossary")]
    text, used = build_context(chunks, max_chars=10_000)
    assert [c.kind for c in used] == ["table", "glossary", "sql_example"]
    assert text.index('kind="table"') < text.index('kind="sql_example"')
    _, used_small = build_context(chunks, max_chars=80)
    assert len(used_small) == 1


def test_hybrid_retrieval_end_to_end(kb):
    r = retrieve("total excess value by supplier", keywords=["calculated_excess", "unit_cost"])
    titles = [c.title for c in r.chunks]
    assert "fact_excess_calculation" in titles
    assert "dim_supplier" in titles
    assert r.sparse_ids, "BM25 should contribute candidates"
