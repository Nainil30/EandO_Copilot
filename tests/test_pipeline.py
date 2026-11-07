from app.llm.base import parse_json_model
from app.pipeline.graph import run_pipeline, run_pipeline_iter
from app.pipeline.memory import ConversationMemory, Turn
from app.pipeline.outputs import RouteDecision


def test_data_question_happy_path(warehouse):
    r = run_pipeline("Total excess value by supplier")
    assert r["status"] == "ok"
    assert r["intent"] == "data_query"
    assert "dim_supplier" in r["sql"] and "LIMIT" in r["sql"]
    assert r["result"]["row_count"] == 3
    assert r["answer"]
    assert r["chart"] == {"type": "bar", "x": "supplier_name", "y": "total_excess_value"}
    assert {c["kind"] for c in r["citations"]} >= {"table", "sql_example"}
    names = [s["name"] for s in r["trace"]]
    assert names == [
        "guard",
        "contextualize",
        "route",
        "cache_lookup",
        "retrieve",
        "rerank",
        "generate_sql",
        "validate_execute",
        "summarize",
    ]
    assert r["usage"]["llm_calls"] >= 3


def test_guard_blocks_before_any_llm_call(warehouse):
    r = run_pipeline("drop table dim_part")
    assert r["status"] == "blocked"
    assert r["usage"]["llm_calls"] == 0
    assert warehouse.calls == []


def test_out_of_scope(warehouse):
    r = run_pipeline("tell me a joke about the weather")
    assert r["status"] == "out_of_scope"
    assert r["sql"] is None


def test_definition_questions_use_glossary_without_sql(warehouse):
    r = run_pipeline("What is excess value?")
    assert r["intent"] == "definition" and r["status"] == "ok"
    assert r["sql"] is None and r["citations"]
    assert warehouse.calls == []


def test_self_healing_repair_loop(warehouse):
    warehouse.fail_times = 1  # first EXPLAIN fails like a hallucinated column would
    r = run_pipeline("Count parts by lifecycle state")
    assert r["status"] == "ok"
    assert len(r["attempts"]) == 1 and r["attempts"][0]["stage"] == "execute"
    assert "does not exist" in r["attempts"][0]["error"]


def test_repair_budget_is_bounded(warehouse):
    warehouse.fail_times = 99
    r = run_pipeline("Count parts by lifecycle state")
    assert r["status"] == "error"
    assert len(r["attempts"]) == 3  # 1 try + MAX_REPAIR_ATTEMPTS (2)


def test_sql_only_mode_does_not_touch_db(warehouse):
    r = run_pipeline("List all parts in the system", execute=False)
    assert r["status"] == "sql_only" and r["result"] is None
    assert warehouse.calls == []


def test_semantic_cache_hit_on_repeat(warehouse):
    first = run_pipeline("Show scrap approval counts by status")
    second = run_pipeline("Show scrap approval counts by status")
    assert first["cache_hit"] is None
    assert second["cache_hit"] and second["cache_hit"]["similarity"] >= 0.95
    assert second["usage"]["llm_calls"] < first["usage"]["llm_calls"]


def test_streaming_emits_steps_then_final(warehouse):
    events = list(run_pipeline_iter("Total excess value by supplier"))
    assert events[0]["type"] == "start" and events[-1]["type"] == "final"
    assert any(e.get("name") == "retrieve" for e in events)


def test_follow_up_uses_conversation_memory(warehouse):
    run_pipeline("Total excess value by supplier", session_id="sess-1")
    r = run_pipeline("only for Taiwan", session_id="sess-1")
    contextualize = next(s for s in r["trace"] if s["name"] == "contextualize")
    assert contextualize["data"]["rewritten"] is True


def test_memory_window_and_isolation():
    m = ConversationMemory(window=2)
    for i in range(3):
        m.append("a", Turn(f"q{i}", f"q{i}", "", ""))
    assert [t.question for t in m.history("a")] == ["q1", "q2"]
    assert m.history("b") == [] and m.history(None) == []


def test_structured_output_parser_tolerates_fences():
    out = parse_json_model('```json\n{"intent": "unsafe", "reason": "x"}\n```', RouteDecision)
    assert out.intent == "unsafe"
    out = parse_json_model('Sure! {"intent": "definition", "reason": "y"} hope it helps', RouteDecision)
    assert out.intent == "definition"
