"""E&O Copilot – chat UI.

Run:  streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st
from api_client import DEFAULT_API_URL, ApiClient

st.set_page_config(page_title="E&O Copilot", page_icon="📦", layout="wide")

EXAMPLES = [
    "Top 10 suppliers by total excess value",
    "Which commodities contribute most to excess value?",
    "Show the weekly forecast trend",
    "Largest pending scrap approvals",
    "Which EOL parts have both LTB orders and excess?",
    "What is consignment and why does it matter?",
]

STEP_LABELS = {
    "guard": "Input guardrails",
    "contextualize": "Resolving follow-up context",
    "route": "Routing intent",
    "cache_lookup": "Semantic cache lookup",
    "retrieve": "Hybrid retrieval (BM25 + dense, RRF)",
    "rerank": "LLM reranking",
    "generate_sql": "Generating SQL (structured output)",
    "validate_execute": "Validating + executing (self-healing)",
    "summarize": "Writing grounded answer",
    "answer_definition": "Answering from glossary",
}

# ---------------------------------------------------------------- state ----
ss = st.session_state
ss.setdefault("session_id", uuid.uuid4().hex[:10])
ss.setdefault("messages", [])  # list of {"role", "content" | "response"}
ss.setdefault("api_url", DEFAULT_API_URL)


def client() -> ApiClient:
    return ApiClient(ss.api_url, api_key=ss.get("api_key") or None)


@st.cache_data(ttl=15, show_spinner=False)
def readiness(api_url: str, api_key: str | None) -> dict:
    return ApiClient(api_url, api_key=api_key).ready()


# -------------------------------------------------------------- sidebar ----
with st.sidebar:
    st.title("📦 E&O Copilot")
    st.caption("Agentic RAG text-to-SQL for Excess & Obsolete inventory")

    with st.expander("Connection", expanded=False):
        ss.api_url = st.text_input("API URL", ss.api_url)
        ss.api_key = st.text_input("API key (optional)", type="password")

    ready = readiness(ss.api_url, ss.get("api_key") or None)
    if isinstance(ready, dict) and "error" not in ready:
        ok = ready.get("status") == "ok"
        st.markdown(
            f"{'🟢' if ok else '🟡'} **{ready.get('status')}** · DB {'✅' if ready.get('database') else '❌'} · "
            f"KB {ready.get('kb_chunks')} chunks · `{ready.get('llm_provider')}`"
        )
        if ready.get("kb_chunks", 0) <= 0:
            st.warning("Knowledge base is empty – build it below.")
    else:
        st.error("API unreachable. Start it with `uvicorn app.api.main:app`.")

    execute = st.toggle("Execute SQL", value=True, help="Off = generate + validate SQL only")
    stream = st.toggle("Stream pipeline steps", value=True)

    c1, c2 = st.columns(2)
    if c1.button("New chat", use_container_width=True):
        ss.messages, ss.session_id = [], uuid.uuid4().hex[:10]
        st.rerun()
    if c2.button("Build KB", use_container_width=True):
        with st.spinner("Embedding schema, glossary and SQL examples..."):
            st.json(client().kb_build())

    st.divider()
    st.caption("Try:")
    for ex in EXAMPLES:
        if st.button(ex, key=f"ex-{ex}", use_container_width=True):
            ss.pending = ex
    st.caption(f"session `{ss.session_id}` – follow-ups like *“only Taiwan”* use memory")


# ------------------------------------------------------------- renderers ---
def md_safe(text: str) -> str:
    """Escape `$` so dollar amounts are not rendered as LaTeX math by Streamlit markdown."""
    return (text or "").replace("$", r"\$")


def render_chart(df: pd.DataFrame, chart: dict | None) -> None:
    if not chart or chart.get("x") not in df.columns or chart.get("y") not in df.columns:
        return
    data = df[[chart["x"], chart["y"]]].copy()
    data[chart["y"]] = pd.to_numeric(data[chart["y"]], errors="coerce")
    if chart["type"] == "line":
        st.line_chart(data, x=chart["x"], y=chart["y"])
    else:
        # keep the query's ranking (largest first) instead of Streamlit's default alphabetical sort
        top = data.dropna().sort_values(chart["y"], ascending=False).head(25)
        st.bar_chart(top, x=chart["x"], y=chart["y"], horizontal=len(top) > 8, sort=False)


def render_response(r: dict, idx: int) -> None:
    status = r.get("status")
    if r.get("error") and status not in ("ok",) and "detail" in r:
        st.error(f"Request failed: {r.get('detail')}")
        return

    icon = {"ok": "✅", "blocked": "🛡️", "clarification": "❓", "out_of_scope": "🧭", "error": "⚠️", "sql_only": "📝"}.get(
        status, ""
    )
    if r.get("answer"):
        st.markdown(f"{icon} {md_safe(r['answer'])}")
    for ins in r.get("key_insights") or []:
        st.markdown(f"- {md_safe(ins)}")
    if status == "error":
        st.warning(r.get("error"))
    if status == "sql_only":
        st.markdown("📝 SQL generated and validated against the guardrails (execution is turned off).")
        st.code(r.get("sql") or "", language="sql")

    result = r.get("result")
    df = pd.DataFrame(result["rows"], columns=result["columns"]) if result else None
    if df is not None and not df.empty:
        render_chart(df, r.get("chart"))

    meta = []
    if r.get("confidence") is not None:
        meta.append(f"confidence {r['confidence']:.0%}")
    meta.append(f"{r.get('latency_ms', 0)} ms")
    usage = r.get("usage") or {}
    meta.append(f"{usage.get('llm_calls', 0)} LLM calls · {usage.get('input_tokens', 0) + usage.get('output_tokens', 0)} tokens")
    if usage.get("est_cost_usd"):
        meta.append(f"${usage['est_cost_usd']:.5f}")
    if r.get("cache_hit"):
        meta.append(f"⚡ semantic cache hit ({r['cache_hit']['similarity']})")
    if r.get("attempts"):
        meta.append(f"🔧 self-repaired x{len(r['attempts'])}")
    st.caption(" · ".join(meta))

    tabs = st.tabs(["Data", "SQL", "Sources", "Trace"])
    with tabs[0]:
        if df is None:
            st.info("No rows (SQL-only mode, or the request did not reach execution).")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            if result.get("truncated"):
                st.caption("Result truncated at the row cap.")
            st.download_button("Download CSV", df.to_csv(index=False), f"eando_{r['request_id']}.csv", key=f"dl-{idx}")
    with tabs[1]:
        if r.get("sql"):
            st.code(r["sql"], language="sql")
        if r.get("plan"):
            st.markdown(f"**Plan:** {r['plan']}")
        for a in r.get("assumptions") or []:
            st.markdown(f"- _assumption:_ {a}")
        for w in r.get("warnings") or []:
            st.caption(f"⚠ {w}")
        for a in r.get("attempts") or []:
            with st.expander(f"Repair attempt {a['attempt'] + 1} ({a['stage']})"):
                st.code(a["sql"], language="sql")
                st.error(a["error"])
    with tabs[2]:
        cites = r.get("citations") or []
        if cites:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "kind": c["kind"],
                            "title": c["title"],
                            "rrf": c["score"],
                            "dense_rank": c["signals"].get("dense_rank"),
                            "bm25_rank": c["signals"].get("bm25_rank"),
                            "rerank_rank": c["signals"].get("rerank_rank"),
                            "expanded": c["signals"].get("expanded", ""),
                        }
                        for c in cites
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No retrieved context for this response.")
    with tabs[3]:
        trace = r.get("trace") or []
        if trace:
            st.bar_chart(pd.DataFrame([{"step": t["name"], "ms": t["ms"]} for t in trace]), x="step", y="ms")
        st.json({"versions": r.get("versions"), "usage": usage, "retrieval": r.get("retrieval")}, expanded=False)

    if r.get("request_id"):
        f1, f2, _ = st.columns([1, 1, 10])
        if f1.button("👍", key=f"up-{idx}"):
            client().feedback(r["request_id"], "up")
            st.toast("Thanks for the feedback!")
        if f2.button("👎", key=f"down-{idx}"):
            client().feedback(r["request_id"], "down")
            st.toast("Logged – this becomes an eval case candidate.")


# ----------------------------------------------------------------- main ----
st.header("Ask your E&O data")
if not ss.messages:
    st.info(
        "Ask in plain English. The copilot retrieves schema + business rules, writes guarded SQL, "
        "runs it read-only, self-repairs on errors and explains the result with sources."
    )

for i, m in enumerate(ss.messages):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            render_response(m["response"], i)

question = st.chat_input("e.g. Top 10 suppliers by excess value") or ss.pop("pending", None)
if question:
    ss.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        response: dict = {}
        if stream:
            with st.status("Thinking...", expanded=True) as box:
                for ev in client().ask_stream(question, execute=execute, session_id=ss.session_id):
                    if ev["type"] == "step":
                        st.write(f"✓ {STEP_LABELS.get(ev['name'], ev['name'])} · {ev['ms']} ms")
                    elif ev["type"] == "final":
                        response = ev["response"]
                    elif ev["type"] == "error":
                        response = {"error": "stream_failed", "detail": ev.get("detail")}
                box.update(label=f"Done · {response.get('status', 'error')}", state="complete", expanded=False)
        else:
            with st.spinner("Thinking..."):
                response = client().ask(question, execute=execute, session_id=ss.session_id)
        render_response(response, len(ss.messages))
    ss.messages.append({"role": "assistant", "response": response})
