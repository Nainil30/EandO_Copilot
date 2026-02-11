from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from api_client import ApiClient


APP_TITLE = "E&O Copilot"
DEFAULT_API_URL = "http://127.0.0.1:8000"


def _is_error(resp: Dict[str, Any]) -> bool:
    # FastAPI typically returns {"detail": "..."} or {"detail": {...}} for errors.
    # But note: your successful responses also include "detail" sometimes? (rare)
    # We treat as error if detail contains typical error structure or if response lacks expected keys.
    if not isinstance(resp, dict):
        return True
    if "detail" in resp:
        return True
    return False


def _render_error(resp: Dict[str, Any], title: str = "Request failed") -> None:
    st.error(title)
    if isinstance(resp, dict) and "detail" in resp:
        st.json(resp["detail"])
    else:
        st.json(resp)


def _as_df(result: Optional[Dict[str, Any]]) -> Optional[pd.DataFrame]:
    """
    Backend returns:
      result = { "columns": [...], "rows": [...], "row_count": N }

    This must never throw.
    """
    if not result or not isinstance(result, dict):
        return None
    cols = result.get("columns") or []
    rows = result.get("rows")
    if rows is None:
        return None
    try:
        return pd.DataFrame(rows, columns=cols if cols else None)
    except Exception:
        # Last resort fallback
        try:
            return pd.DataFrame(rows)
        except Exception:
            return None


def _divider() -> None:
    st.markdown("---")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded")
    st.title(APP_TITLE)
    st.caption("Natural language → SQL (guardrails) + RAG visibility. No gimmicks, just the workflow.")

    # Sidebar: backend connectivity + explicit actions
    st.sidebar.header("Backend")
    base_url = st.sidebar.text_input("API Base URL", value=DEFAULT_API_URL)
    client = ApiClient(base_url=base_url)

    st.sidebar.caption("If Swagger works, this should be the same base URL you used there.")

    col_a, col_b = st.sidebar.columns(2)
    if col_a.button("Health", use_container_width=True):
        resp = client.health()
        if _is_error(resp):
            _render_error(resp, "Health check failed")
        else:
            st.sidebar.success("Backend is reachable.")
            st.sidebar.json(resp)

    if col_b.button("Schema", use_container_width=True):
        resp = client.schema()
        if _is_error(resp):
            _render_error(resp, "Schema fetch failed")
        else:
            st.session_state["schema_snapshot"] = resp
            st.sidebar.success("Schema snapshot loaded.")

    _divider()

    st.sidebar.header("Knowledge Base")
    st.sidebar.caption("RAG Build: builds embeddings + vector index for docs/schema/examples used by NLQ.")
    if st.sidebar.button("POST /rag/build", use_container_width=True):
        resp = client.rag_build()
        if _is_error(resp):
            _render_error(resp, "RAG build failed")
        else:
            st.sidebar.success("RAG build complete.")
            st.sidebar.json(resp)

    st.sidebar.caption("RAG Debug: shows what was retrieved + what reranker kept before SQL generation.")
    dbg_q = st.sidebar.text_area("RAG Debug question", value="Total excess value by supplier", height=80)
    if st.sidebar.button("POST /rag/debug", use_container_width=True):
        if not dbg_q.strip():
            st.sidebar.warning("Enter a debug question.")
        else:
            resp = client.rag_debug(dbg_q.strip())
            if _is_error(resp):
                _render_error(resp, "RAG debug failed")
            else:
                st.session_state["last_rag_debug"] = resp
                st.sidebar.success("RAG debug ok.")

    # Main layout
    _divider()

    left, right = st.columns([2, 1], gap="large")

    with left:
        st.subheader("Ask a question (NLQ)")
        st.caption("NLQ: converts a natural-language question into a safe PostgreSQL SELECT, runs it, returns results.")

        q = st.text_area(
            "Question",
            value="Top 10 parts by excess value (calculated_excess * unit_cost) for EOL parts",
            height=90,
        )

        run_nlq = st.button("Run NLQ", type="primary", use_container_width=True)

        if run_nlq:
            if not q.strip():
                st.warning("Enter a question first.")
            else:
                t0 = time.time()
                resp = client.nlq(q.strip())
                latency = time.time() - t0
                st.session_state["last_nlq"] = resp
                st.session_state["last_nlq_latency_s"] = latency

        nlq_resp = st.session_state.get("last_nlq")

        if nlq_resp:
            if _is_error(nlq_resp):
                _render_error(nlq_resp, "NLQ failed")
            else:
                st.success(f"NLQ complete in {st.session_state.get('last_nlq_latency_s', 0.0):.2f}s")

                sql = nlq_resp.get("generated_sql", "")
                if sql:
                    st.markdown("### Generated SQL")
                    st.code(sql, language="sql")

                result = nlq_resp.get("result")
                df = _as_df(result)

                st.markdown("### Result")
                if df is None:
                    st.info("No tabular rows returned (or result payload missing).")
                    st.json(result if result else nlq_resp)
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    if isinstance(result, dict) and "row_count" in result:
                        st.caption(f"Row count: {result.get('row_count')}")

                with st.expander("Citations + Retrieval preview", expanded=False):
                    st.write("Citations (doc ids used as grounding):")
                    st.json(nlq_resp.get("citations", []))

                    st.write("Retrieval preview (top items):")
                    st.json(nlq_resp.get("retrieval_preview", []))

    with right:
        st.subheader("Run SQL directly")
        st.caption("Query: runs a SQL string with validator guardrails (safe SELECT only).")

        default_sql = "SELECT part_number, lifecycle_state, unit_cost FROM dim_part LIMIT 10"
        sql_text = st.text_area("SQL", value=default_sql, height=160)

        if st.button("Run SQL", use_container_width=True):
            t0 = time.time()
            resp = client.query(sql_text)
            latency = time.time() - t0
            st.session_state["last_query"] = resp
            st.session_state["last_query_latency_s"] = latency

        query_resp = st.session_state.get("last_query")
        if query_resp:
            if _is_error(query_resp):
                _render_error(query_resp, "SQL execution failed")
            else:
                st.success(f"SQL complete in {st.session_state.get('last_query_latency_s', 0.0):.2f}s")
                dfq = _as_df(query_resp)
                if dfq is None:
                    st.info("No rows returned.")
                    st.json(query_resp)
                else:
                    st.dataframe(dfq, use_container_width=True, hide_index=True)
                    if "row_count" in query_resp:
                        st.caption(f"Row count: {query_resp.get('row_count')}")

    _divider()

    st.subheader("Debug panel")
    st.caption("This is for you (dev) — so you can verify RAG selection and schema snapshot quickly.")

    tabs = st.tabs(["RAG Debug", "Schema Snapshot", "Raw NLQ JSON"])

    with tabs[0]:
        dbg = st.session_state.get("last_rag_debug")
        if not dbg:
            st.info("Run /rag/debug from the sidebar to see retrieval + rerank selection.")
        else:
            st.json(dbg)

    with tabs[1]:
        schema = st.session_state.get("schema_snapshot")
        if not schema:
            st.info("Click Schema in the sidebar to load it.")
        else:
            st.json(schema)

    with tabs[2]:
        nlq_resp = st.session_state.get("last_nlq")
        st.json(nlq_resp or {})


if __name__ == "__main__":
    main()
