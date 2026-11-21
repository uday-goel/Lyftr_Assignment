import os
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("LLM_API_KEYS", "fake-key-1,fake-key-2")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("CACHE_TYPE", "local")


class FakeResponse:
    """Minimal stand-in for a LiteLLM ModelResponse."""

    def __init__(self, text, prompt_tokens, completion_tokens, cache_hit=False):
        message = type("M", (), {"content": text})()
        self.choices = [type("C", (), {"message": message})()]
        self.usage = type("U", (), {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})()
        self._hidden_params = {"cache_hit": cache_hit}


@pytest.fixture()
def provider(monkeypatch):
    """Replaces the network call with a deterministic fake that has its own tiny cache,
    mirroring LiteLLM semantics (cache key = model + messages; bypass skips the read)."""
    from app import llm

    state = {"calls": [], "failing_keys": set(), "cache": {}}

    async def fake(settings, messages, api_key, bypass_cache):
        key = (settings.model, repr(messages))
        if not bypass_cache and key in state["cache"]:
            text, pt, ct = state["cache"][key]
            return FakeResponse(text, pt, ct, cache_hit=True)
        state["calls"].append(api_key)
        if api_key in state["failing_keys"]:
            raise RuntimeError("429 quota exceeded")
        text = f"echo: {messages[-1]['content']}"
        pt = sum(len(m["content"].split()) for m in messages)
        state["cache"][key] = (text, pt, 5)
        return FakeResponse(text, pt, 5)

    monkeypatch.setattr(llm, "_provider_call", fake)
    return state


@pytest.fixture()
def client(env, provider):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
