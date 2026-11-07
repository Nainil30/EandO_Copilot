import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(warehouse):
    from app.api.main import app

    return TestClient(app)


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["database"] is True and ready["kb_chunks"] > 0


def test_ask_returns_typed_response(client):
    r = client.post("/v1/ask", json={"question": "Total excess value by supplier"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["result"]["row_count"] == 3
    assert "X-Request-ID" in r.headers


def test_ask_stream_sse(client):
    with client.stream("POST", "/v1/ask/stream", json={"question": "Count parts by lifecycle state"}) as r:
        text = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "start" and events[-1]["type"] == "final"


def test_sql_endpoint_blocks_unsafe(client):
    r = client.post("/v1/sql", json={"sql": "DELETE FROM dim_part"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_sql"


def test_validate_endpoint(client):
    r = client.post("/v1/sql/validate", json={"sql": "select part_number from dim_part"}).json()
    assert r["ok"] and "LIMIT" in r["sql"]


def test_schema_and_kb(client):
    schema = client.get("/v1/schema").json()
    assert len(schema["tables"]) == 9 and schema["joins"]
    stats = client.get("/v1/kb/stats").json()
    assert stats["by_kind"]["sql_example"] >= 15


def test_retrieve_debug(client):
    r = client.post("/v1/retrieve/debug", json={"question": "LTB orders by supplier"}).json()
    assert r["dense"] and r["fused"] and r["reranked"]


def test_feedback_and_metrics(client):
    ask = client.post("/v1/ask", json={"question": "Count parts by lifecycle state"}).json()
    assert client.post("/v1/feedback", json={"request_id": ask["request_id"], "rating": "up"}).json()["recorded"]
    m = client.get("/v1/metrics").json()
    assert m["requests"] >= 1 and m["feedback"]["thumbs_up"] >= 1


def test_api_key_auth(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_keys", "secret-key")
    assert client.post("/v1/ask", json={"question": "x"}).status_code == 401
    ok = client.post("/v1/ask", json={"question": "Count parts by lifecycle state"}, headers={"X-API-Key": "secret-key"})
    assert ok.status_code == 200


def test_request_validation(client):
    assert client.post("/v1/ask", json={"question": ""}).status_code == 422
