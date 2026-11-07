"""Knowledge-base explorer + retrieval debugger (dense vs BM25 vs fused vs reranked)."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from api_client import DEFAULT_API_URL, ApiClient

st.set_page_config(page_title="Knowledge Base · E&O Copilot", page_icon="🧠", layout="wide")
api = ApiClient(st.session_state.get("api_url", DEFAULT_API_URL), api_key=st.session_state.get("api_key") or None)

st.title("🧠 Knowledge base & retrieval lab")

stats = api.kb_stats()
if isinstance(stats, dict) and "error" not in stats:
    cols = st.columns(len(stats.get("by_kind", {})) + 1)
    cols[0].metric("Chunks", stats.get("chunks", 0))
    for col, (k, v) in zip(cols[1:], stats.get("by_kind", {}).items()):
        col.metric(k, v)
    st.caption(f"collection `{stats.get('collection')}`")

st.subheader("Retrieval debugger")
q = st.text_input("Question", "Which suppliers have the most excess value in Taiwan?")
rerank = st.toggle("Apply LLM reranker", value=True)
if st.button("Retrieve", type="primary"):
    r = api.retrieve_debug(q, rerank=rerank)
    if "error" in r:
        st.error(r)
    else:
        reranked = set(r["reranked"])
        df = pd.DataFrame(
            [
                {
                    "kept": "✅" if c["id"] in reranked else "",
                    "kind": c["kind"],
                    "title": c["title"],
                    "rrf_score": c["score"],
                    "dense_rank": c["signals"].get("dense_rank"),
                    "dense_sim": c["signals"].get("dense_similarity"),
                    "bm25_rank": c["signals"].get("bm25_rank"),
                    "bm25": c["signals"].get("bm25"),
                }
                for c in r["fused"]
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(
            f"dense hits: {len(r['dense'])} · bm25 hits: {len(r['bm25'])} · graph-expanded: {len(r['graph_expanded'])} · "
            f"rerank: {r['rerank'].get('reason', '')}"
        )

st.subheader("Browse chunks")
kind = st.selectbox("Kind", ["all", "table", "join", "glossary", "sql_example"])
chunks = api.kb_chunks(None if kind == "all" else kind)
if isinstance(chunks, list):
    for c in chunks[:200]:
        with st.expander(f"[{c['kind']}] {c['title']}"):
            st.code(c["text"], language="sql" if c["kind"] == "sql_example" else "text")
