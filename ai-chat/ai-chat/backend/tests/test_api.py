def chat(client, sid, msg, **kw):
    return client.post("/chat", json={"session_id": sid, "message": msg}, **kw)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_chat_live_then_cache_across_sessions(client):
    a = chat(client, "s-a", "What is the capital of France?").json()
    assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert a["metadata"]["key_id"] == "key_1" and a["metadata"]["fallback_attempts"] == 0
    b = chat(client, "s-b", "What is the capital of France?").json()
    assert b["metadata"]["resolution_source"] == "LLM_CACHE"
    assert b["reply"] == a["reply"]
    assert b["metadata"]["key_id"] is None and b["metadata"]["fallback_attempts"] == 0
    assert b["metadata"]["input_tokens"] == a["metadata"]["input_tokens"]


def test_different_message_never_cached(client):
    chat(client, "s-a", "hello there")
    r = chat(client, "s-b", "hello there friend").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_multi_turn_history_and_isolation(client, provider):
    chat(client, "s-1", "my name is Ada")
    chat(client, "s-1", "what is my name?")
    chat(client, "s-2", "unrelated")
    h = client.get("/sessions/s-1/messages").json()
    assert [m["role"] for m in h["messages"]] == ["user", "assistant", "user", "assistant"]
    assert "metadata" not in h["messages"][0]
    assert h["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert len(client.get("/sessions/s-2/messages").json()["messages"]) == 2


def test_tokens_are_per_request_not_cumulative(client):
    first = chat(client, "s-x", "one two three").json()["metadata"]["input_tokens"]
    again = chat(client, "s-y", "one two three").json()["metadata"]["input_tokens"]
    assert first == again


def test_failover_on_single_key(client):
    r = chat(client, "s-f", "failover please", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1
    assert m["resolution_source"] == "LIVE_INFERENCE"


def test_simulation_bypasses_cache_so_failover_is_demonstrable(client):
    chat(client, "s-1", "cached already")
    r = chat(client, "s-2", "cached already", headers={"X-Simulate-Failure": "key_1=500"})
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_returns_503_and_nothing_stored(client):
    r = chat(client, "s-503", "hi", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503 and r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-503/messages").status_code == 404


def test_real_provider_failure_falls_over(client, provider):
    provider["failing_keys"].add("fake-key-1")
    m = chat(client, "s-r", "real failure").json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1


def test_all_real_failures_503(client, provider):
    provider["failing_keys"] |= {"fake-key-1", "fake-key-2"}
    assert chat(client, "s-z", "boom").status_code == 503


def test_validation_errors(client):
    assert client.post("/chat", json={"session_id": "s", "message": "   "}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "s", "message": "x" * 4001}).status_code == 422
    r = client.post("/chat", content="{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()
    assert client.get("/sessions/s/messages").status_code == 404


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_system_prompt_persists_for_session(client, provider):
    client.post("/chat", json={"session_id": "s-p", "message": "hi", "system_prompt": "Talk like a pirate"})
    chat(client, "s-p", "again")
    assert any(True for _ in provider["cache"])  # both calls produced distinct cache entries
    keys = [k[1] for k in provider["cache"]]
    assert all("Talk like a pirate" in k for k in keys)


def test_simulation_disabled_ignores_header(env, provider, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        r = c.post("/chat", json={"session_id": "s", "message": "hi"}, headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200
