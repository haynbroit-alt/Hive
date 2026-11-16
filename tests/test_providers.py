"""Fournisseurs de modèles : forme des requêtes (sans réseau, transport simulé)."""
import json

import httpx
import pytest

from hive.core.llm import LLMRequest, OllamaProvider, OpenAICompatProvider, ProviderError
from tests.conftest import run


def patch_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Fake)


def test_ollama_request_shape_with_image(monkeypatch):
    seen = {}

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"cells": []}'},
                                         "prompt_eval_count": 12, "eval_count": 5})

    patch_httpx(monkeypatch, handler)
    p = OllamaProvider("local", "gemma4:e4b")
    assert run(p.available())
    r = run(p.complete(LLMRequest("sys", "prompt", images=[(b"\x89PNG", "image/png")])))
    assert r.text == '{"cells": []}' and r.cost == 0 and r.tokens_in == 12
    assert seen["format"] == "json" and seen["messages"][1]["images"] and seen["stream"] is False


def test_openai_compatible_request_and_cost(monkeypatch):
    monkeypatch.setenv("K", "secret")
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}})

    patch_httpx(monkeypatch, handler)
    p = OpenAICompatProvider("groq", "m", "https://api.example.test/v1", "K", price_in=0.5)
    r = run(p.complete(LLMRequest("s", "p", images=[(b"x", "image/jpeg")])))
    assert seen["auth"] == "Bearer secret" and seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert r.cost == pytest.approx(0.5)


def test_provider_errors_are_contained(monkeypatch):
    patch_httpx(monkeypatch, lambda req: httpx.Response(500, json={}))
    with pytest.raises(ProviderError):
        run(OpenAICompatProvider("x", "m", "https://api.example.test/v1").complete(LLMRequest("s", "p")))
