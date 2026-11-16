"""Fournisseurs de modèles : forme des requêtes (sans réseau, transport simulé)."""
import json

import pytest

from hive.core import http
from hive.core.llm import LLMRequest, OllamaProvider, OpenAICompatProvider, ProviderError
from tests.conftest import run


def fake_transport(monkeypatch, handler):
    monkeypatch.setattr(http, "transport", handler)


def test_ollama_request_shape_with_image(monkeypatch):
    seen = {}

    def handler(req: http.Request) -> http.Response:
        if req.url.endswith("/api/tags"):
            return http.Response(200, b'{"models": []}')
        seen.update(json.loads(req.body))
        return http.Response(200, json.dumps({"message": {"content": '{"cells": []}'},
                                               "prompt_eval_count": 12, "eval_count": 5}).encode())

    fake_transport(monkeypatch, handler)
    p = OllamaProvider("local", "gemma4:e4b")
    assert run(p.available())
    r = run(p.complete(LLMRequest("sys", "prompt", images=[(b"\x89PNG", "image/png")])))
    assert r.text == '{"cells": []}' and r.cost == 0 and r.tokens_in == 12
    assert seen["format"] == "json" and seen["messages"][1]["images"] and seen["stream"] is False


def test_openai_compatible_request_and_cost(monkeypatch):
    monkeypatch.setenv("K", "secret")
    seen = {}

    def handler(req: http.Request) -> http.Response:
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.body)
        return http.Response(200, json.dumps({"choices": [{"message": {"content": "ok"}}],
                                              "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}).encode())

    fake_transport(monkeypatch, handler)
    p = OpenAICompatProvider("groq", "m", "https://api.example.test/v1", "K", price_in=0.5)
    r = run(p.complete(LLMRequest("s", "p", images=[(b"x", "image/jpeg")])))
    assert seen["auth"] == "Bearer secret" and seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert r.cost == pytest.approx(0.5)


def test_provider_errors_are_contained(monkeypatch):
    fake_transport(monkeypatch, lambda req: http.Response(500, b"{}"))
    with pytest.raises(ProviderError):
        run(OpenAICompatProvider("x", "m", "https://api.example.test/v1").complete(LLMRequest("s", "p")))


def test_multipart_encoding_is_well_formed():
    body, ctype = http.multipart({"chat_id": 42}, {"video": ("v.mp4", b"\x00\x01", "video/mp4")})
    boundary = ctype.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode()) and body.rstrip().endswith(f"--{boundary}--".encode())
    assert b'name="video"; filename="v.mp4"' in body and b"\x00\x01" in body
