"""Minimal client for a local Ollama server, for structured (JSON-schema) outputs.

Deterministic settings by default: temperature 0, a fixed seed, and thinking turned off.
Every response records the model, its digest and token counts, so a run can be reproduced
and its cost reported.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from medgraph.settings import Settings, check_local_llm


class LLMError(RuntimeError):
    """The model did not return a usable response."""


@dataclass(frozen=True)
class LLMResponse:
    content: dict[str, Any]
    model: str
    prompt_tokens: int
    output_tokens: int
    seconds: float


class OllamaClient:
    def __init__(
        self,
        settings: Settings,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model or settings.llm_model
        check_local_llm(settings.llm_base_url, self.model, settings.allow_remote_llm)
        self._http = httpx.Client(
            base_url=settings.llm_base_url, timeout=settings.llm_timeout_s, transport=transport
        )

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        seed: int = 42,
        num_ctx: int = 8192,
    ) -> LLMResponse:
        """One chat turn whose answer must follow ``schema``; returns the parsed JSON."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "seed": seed, "num_ctx": num_ctx},
        }
        started = time.monotonic()
        response = self._http.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        try:
            content = json.loads(data["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"unparseable model output: {exc}") from exc
        if not isinstance(content, dict):
            raise LLMError("model output is not a JSON object")
        return LLMResponse(
            content=content,
            model=str(data.get("model", self.model)),
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            seconds=round(time.monotonic() - started, 2),
        )

    def model_digest(self) -> str | None:
        """Digest of the installed model, recorded with every evaluation run."""
        response = self._http.get("/api/tags")
        response.raise_for_status()
        for model in response.json().get("models", []):
            if model.get("name") == self.model or model.get("model") == self.model:
                return str(model.get("digest"))
        return None
