import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from medgraph.agent.llm import LLMError, OllamaClient
from medgraph.settings import RemoteLLMError, Settings, check_local_llm


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for var in ("MEDGRAPH_LLM_BASE_URL", "MEDGRAPH_LLM_MODEL", "MEDGRAPH_ALLOW_REMOTE_LLM"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None, data_dir=tmp_path)


def ollama(reply: dict[str, Any], seen: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "digest": "abc"}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    return httpx.MockTransport(handler)


# --- local-only guard --------------------------------------------------------------------


def test_defaults_are_local(settings: Settings) -> None:
    assert settings.llm_base_url.startswith("http://127.0.0.1")
    assert settings.allow_remote_llm is False


@pytest.mark.parametrize(
    ("url", "model"),
    [
        ("https://api.example.com", "qwen3.5:9b"),
        ("http://192.168.1.20:11434", "qwen3.5:9b"),
        ("http://127.0.0.1:11434", "gemma4:cloud"),  # forwarded to ollama.com by Ollama
        ("http://localhost:11434", "qwen3.5:9b-cloud"),
    ],
)
def test_remote_endpoints_and_cloud_models_are_refused(url: str, model: str) -> None:
    with pytest.raises(RemoteLLMError):
        check_local_llm(url, model, allow_remote=False)
    check_local_llm(url, model, allow_remote=True)  # only with explicit permission


def test_settings_refuse_a_remote_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDGRAPH_LLM_BASE_URL", "https://api.example.com")
    with pytest.raises(ValueError, match="not on this machine"):
        Settings(_env_file=None)


def test_client_refuses_a_cloud_model_override(settings: Settings) -> None:
    with pytest.raises(RemoteLLMError):
        OllamaClient(settings, model="gemma4:cloud")


# --- client ------------------------------------------------------------------------------


def test_chat_json_sends_deterministic_structured_request(settings: Settings) -> None:
    seen: list[dict[str, Any]] = []
    reply = {
        "model": "qwen3.5:9b",
        "message": {"content": '{"rows": []}'},
        "prompt_eval_count": 12,
        "eval_count": 5,
    }
    client = OllamaClient(settings, transport=ollama(reply, seen))
    schema = {"type": "object", "properties": {"rows": {"type": "array"}}}
    response = client.chat_json("system", "user", schema)
    assert response.content == {"rows": []}
    assert (response.prompt_tokens, response.output_tokens) == (12, 5)
    (payload,) = seen
    assert payload["format"] == schema
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["seed"] == 42
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert client.model_digest() == "abc"


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_unusable_output_raises(settings: Settings, content: str) -> None:
    client = OllamaClient(settings, transport=ollama({"message": {"content": content}}, []))
    with pytest.raises(LLMError):
        client.chat_json("s", "u", {})


def test_embed_returns_vectors_in_order(settings: Settings) -> None:
    seen: list[dict[str, Any]] = []
    client = OllamaClient(
        settings, model="bge-m3", transport=ollama({"embeddings": [[0.5, 1], [0, -1]]}, seen)
    )
    assert client.embed(["a", "b"]) == [[0.5, 1.0], [0.0, -1.0]]
    assert seen == [{"model": "bge-m3", "input": ["a", "b"]}]
    with pytest.raises(LLMError, match="expected 3 embeddings"):
        client.embed(["a", "b", "c"])


def test_model_digest_reads_an_untagged_name_as_latest(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bge-m3:latest", "digest": "d"}]})

    transport = httpx.MockTransport(handler)
    assert OllamaClient(settings, model="bge-m3", transport=transport).model_digest() == "d"
    assert OllamaClient(settings, model="bge-m3:567m", transport=transport).model_digest() is None
