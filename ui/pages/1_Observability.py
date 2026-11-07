"""Online metrics + audit trail (what an LLMOps dashboard would show)."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from api_client import DEFAULT_API_URL, ApiClient

st.set_page_config(page_title="Observability · E&O Copilot", page_icon="📈", layout="wide")
api = ApiClient(st.session_state.get("api_url", DEFAULT_API_URL), api_key=st.session_state.get("api_key") or None)

st.title("📈 Observability")
m = api.metrics()
if not isinstance(m, dict) or "error" in m:
    st.error(f"Could not load metrics: {m}")
    st.stop()
if not m.get("requests"):
    st.info("No requests logged yet. Ask a few questions first.")
    st.stop()

c = st.columns(6)
c[0].metric("Requests", m["requests"])
c[1].metric("Success rate", f"{m['success_rate']:.0%}")
c[2].metric("p50 latency", f"{m['latency_ms']['p50']} ms")
c[3].metric("p95 latency", f"{m['latency_ms']['p95']} ms")
c[4].metric("Cache hit rate", f"{m['cache_hit_rate']:.0%}")
c[5].metric("Est. cost", f"${m['est_cost_usd_total']:.4f}")

left, right = st.columns(2)
with left:
    st.subheader("Status breakdown")
    st.bar_chart(pd.DataFrame(list(m["status_breakdown"].items()), columns=["status", "count"]), x="status", y="count")
with right:
    st.subheader("Feedback")
    fb = m["feedback"]
    st.metric("👍 / 👎", f"{fb['thumbs_up']} / {fb['thumbs_down']}")
    st.metric("Self-repair rate", f"{m['repair_rate']:.0%}")
    st.metric("Tokens used", f"{m['tokens_total']:,}")

st.subheader("Recent requests (audit log)")
rows = api.audit(100)
if isinstance(rows, list) and rows:
    df = pd.DataFrame(
        [
            {
                "request_id": r.get("request_id"),
                "status": r.get("status"),
                "intent": r.get("intent"),
                "question": r.get("question"),
                "rows": r.get("row_count"),
                "repairs": r.get("repair_attempts"),
                "cache": r.get("cache_hit"),
                "latency_ms": r.get("latency_ms"),
                "tokens": (r.get("usage") or {}).get("input_tokens", 0) + (r.get("usage") or {}).get("output_tokens", 0),
            }
            for r in rows
            if r.get("type") == "ask"
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)
    pick = st.selectbox("Inspect request", [r.get("request_id") for r in rows if r.get("type") == "ask"])
    rec = next((r for r in rows if r.get("request_id") == pick), None)
    if rec:
        if rec.get("sql"):
            st.code(rec["sql"], language="sql")
        st.json(rec, expanded=False)
