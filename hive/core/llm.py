"""Couche modèles : fournisseurs interchangeables, routés sous contrôle budgétaire.

Fournisseurs :
  * offline   : déterministe, sans réseau, 0 € (tests, démo, secours). N'invente rien :
                il ne fait que reformuler/agréger ce qui est sur le Rayon, et le signale.
  * ollama    : modèles locaux (0 €, privé).
  * openai    : toute API compatible OpenAI (llama.cpp server, LM Studio, vLLM, Groq,
                OpenRouter, Mistral, GitHub Models, Gemini...). Gratuit ou payant selon config.
  * anthropic : Claude via le SDK officiel (payant => bloqué tant que le plafond est à 0 €).

Le routeur essaie les fournisseurs dans l'ordre de préférence de l'agent, saute ceux
qui sont indisponibles, limités en débit ou hors budget, et journalise chaque appel.
"""
from __future__ import annotations

import base64
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from . import http
from .governance import Budget, BudgetBlocked
from .ledger import Ledger


class ProviderError(Exception):
    pass


class NoProviderAvailable(Exception):
    pass


@dataclass
class LLMRequest:
    system: str
    prompt: str
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (octets, mime)
    max_tokens: int = 1500
    json_mode: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def approx_input_tokens(self) -> int:
        return (len(self.system) + len(self.prompt)) // 4 + 800 * len(self.images)


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


class Provider(ABC):
    kind = "abstract"

    def __init__(self, name: str, model: str = "", price_in: float = 0.0, price_out: float = 0.0,
                 vision: bool = False, rpm: int | None = None, timeout_s: float = 120.0):
        self.name = name
        self.model = model
        self.price_in = float(price_in)     # par million de jetons, devise du budget
        self.price_out = float(price_out)
        self.vision = vision
        self.rpm = rpm
        self.timeout_s = timeout_s
        self._calls: list[float] = []

    @property
    def free(self) -> bool:
        return self.price_in == 0 and self.price_out == 0

    def estimate_cost(self, req: LLMRequest) -> float:
        # Estimation volontairement pessimiste : on suppose max_tokens consommés en sortie.
        return (req.approx_input_tokens() * self.price_in + req.max_tokens * self.price_out) / 1e6

    def actual_cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6

    def rate_limited(self, now: float) -> bool:
        if not self.rpm:
            return False
        self._calls = [t for t in self._calls if now - t < 60.0]
        return len(self._calls) >= self.rpm

    def note_call(self, now: float) -> None:
        self._calls.append(now)

    async def available(self) -> bool:
        return True

    @abstractmethod
    async def complete(self, req: LLMRequest) -> LLMResponse: ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "model": self.model, "free": self.free,
                "vision": self.vision, "price_in": self.price_in, "price_out": self.price_out}


class OfflineProvider(Provider):
    kind = "offline"

    def __init__(self, name: str = "offline", **kw: Any):
        super().__init__(name, model="hive-offline-v0", **kw)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        from .offline import offline_answer
        text = offline_answer(req)
        return LLMResponse(text, self.name, self.model, req.approx_input_tokens(), len(text) // 4, 0.0)


class OllamaProvider(Provider):
    kind = "ollama"

    def __init__(self, name: str, model: str, base_url: str = "http://localhost:11434", **kw: Any):
        super().__init__(name, model=model, **kw)
        self.base_url = base_url.rstrip("/")
        self._avail: tuple[float, bool] | None = None

    async def available(self) -> bool:
        now = time.time()
        if self._avail and now - self._avail[0] < 60:
            return self._avail[1]
        ok = False
        try:
            r = await http.request("GET", f"{self.base_url}/api/tags", timeout=1.5)
            ok = r.status == 200
        except http.HTTPError:
            ok = False
        self._avail = (now, ok)
        return ok

    async def complete(self, req: LLMRequest) -> LLMResponse:
        user: dict[str, Any] = {"role": "user", "content": req.prompt}
        if req.images:
            user["images"] = [base64.b64encode(b).decode() for b, _ in req.images]
        body: dict[str, Any] = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": req.system}, user],
            "options": {"num_predict": req.max_tokens},
        }
        if req.json_mode:
            body["format"] = "json"
        try:
            r = await http.request("POST", f"{self.base_url}/api/chat", json=body, timeout=self.timeout_s)
            r.raise_for_status()
            data = r.json()
        except (http.HTTPError, ValueError) as e:
            raise ProviderError(f"ollama: {e}") from e
        text = (data.get("message") or {}).get("content", "")
        ti, to = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        return LLMResponse(text, self.name, self.model, ti, to, self.actual_cost(ti, to))


class OpenAICompatProvider(Provider):
    kind = "openai"

    def __init__(self, name: str, model: str, base_url: str, api_key_env: str | None = None,
                 json_mode_supported: bool = True, **kw: Any):
        super().__init__(name, model=model, **kw)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.json_mode_supported = json_mode_supported

    def _key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    async def available(self) -> bool:
        return self.api_key_env is None or bool(self._key())

    async def complete(self, req: LLMRequest) -> LLMResponse:
        content: Any = req.prompt
        if req.images:
            content = [{"type": "text", "text": req.prompt}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{base64.b64encode(b).decode()}"}}
                for b, mime in req.images
            ]
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": req.max_tokens,
            "messages": [{"role": "system", "content": req.system},
                         {"role": "user", "content": content}],
        }
        if req.json_mode and self.json_mode_supported:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key()}"} if self._key() else {}
        try:
            r = await http.request("POST", f"{self.base_url}/chat/completions", json=body, headers=headers,
                                   timeout=self.timeout_s)
            r.raise_for_status()
            data = r.json()
        except (http.HTTPError, ValueError) as e:
            raise ProviderError(f"{self.name}: {e}") from e
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: réponse inattendue") from e
        usage = data.get("usage") or {}
        ti, to = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        return LLMResponse(text, self.name, self.model, ti, to, self.actual_cost(ti, to))


class AnthropicProvider(Provider):
    """Claude via le SDK officiel `anthropic` (dépendance optionnelle `pip install .[claude]`).

    Payant : le routeur ne l'appelle que si le plafond budgétaire le permet, c'est-à-dire
    après une autorisation explicite du Conseil humain.
    """
    kind = "anthropic"

    def __init__(self, name: str, model: str = "claude-opus-5-5", effort: str = "medium",
                 price_in: float = 4.0, price_out: float = 20.0, **kw: Any):
        super().__init__(name, model=model, price_in=price_in, price_out=price_out, vision=True, **kw)
        self.effort = effort

    async def available(self) -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    async def complete(self, req: LLMRequest) -> LLMResponse:
        import anthropic

        client = anthropic.AsyncAnthropic()
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.standard_b64encode(b).decode("utf-8")}}
            for b, mime in req.images
        ]
        content.append({"type": "text", "text": req.prompt})
        try:
            resp = await client.beta.messages.create(
                model=self.model,
                max_tokens=max(req.max_tokens, 4000),
                system=req.system,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(f"{self.name}: limite de débit") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"{self.name}: erreur API {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"{self.name}: réseau indisponible") from e
        if resp.stop_reason == "refusal":
            raise ProviderError(f"{self.name}: requête refusée par le modèle")
        text = "".join(b.text for b in resp.content if b.type == "text")
        ti, to = resp.usage.input_tokens, resp.usage.output_tokens
        return LLMResponse(text, self.name, resp.model, ti, to, self.actual_cost(ti, to))


def build_provider(name: str, spec: dict[str, Any]) -> Provider:
    kind = spec.get("type", "offline")
    common = {k: spec[k] for k in ("price_in", "price_out", "vision", "rpm", "timeout_s") if k in spec}
    if kind == "offline":
        return OfflineProvider(name, **common)
    if kind == "ollama":
        return OllamaProvider(name, spec["model"], spec.get("base_url", "http://localhost:11434"), **common)
    if kind == "openai":
        return OpenAICompatProvider(name, spec["model"], spec["base_url"], spec.get("api_key_env"),
                                    spec.get("json_mode", True), **common)
    if kind == "anthropic":
        common.pop("vision", None)
        return AnthropicProvider(name, spec.get("model", "claude-opus-5-5"),
                                 spec.get("effort", "medium"), **common)
    raise ValueError(f"type de fournisseur inconnu : {kind}")


class Router:
    def __init__(self, providers: dict[str, Provider], budget: Budget, ledger: Ledger,
                 fallback: str | None = "offline"):
        self.providers = providers
        self.budget = budget
        self.ledger = ledger
        self.fallback = fallback

    async def complete(self, prefs: list[str], req: LLMRequest, actor: str,
                       mission_id: str | None = None) -> LLMResponse:
        order = list(prefs)
        if self.fallback and self.fallback not in order:
            order.append(self.fallback)
        skipped: list[str] = []
        for name in order:
            p = self.providers.get(name)
            if p is None:
                continue
            if req.images and not p.vision and p.kind != "offline":
                skipped.append(f"{name}: pas de vision")
                continue
            now = time.time()
            if p.rate_limited(now):
                skipped.append(f"{name}: limite de débit locale")
                continue
            if not await p.available():
                skipped.append(f"{name}: indisponible")
                continue
            est = p.estimate_cost(req)
            try:
                self.budget.check(est, f"appel {name}/{p.model}", mission_id)
            except BudgetBlocked as e:
                self.ledger.append("budget.blocked", actor,
                                   {"provider": name, "estimate": est, "reason": str(e)}, mission_id)
                skipped.append(f"{name}: hors budget")
                continue
            p.note_call(now)
            try:
                resp = await p.complete(req)
            except ProviderError as e:
                self.ledger.append("llm.error", actor, {"provider": name, "error": str(e)[:300]}, mission_id)
                skipped.append(f"{name}: erreur")
                continue
            self.budget.charge(resp.cost, f"appel {name}/{resp.model}", actor, mission_id)
            self.ledger.append("llm.called", actor, {
                "provider": name, "model": resp.model, "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out, "cost": resp.cost, "skipped": skipped,
                "phase": req.meta.get("phase"),
            }, mission_id)
            return resp
        raise NoProviderAvailable("; ".join(skipped) or "aucun fournisseur configuré")

    async def usable(self, prefs: list[str], mission_id: str | None = None) -> str | None:
        """Premier VRAI modèle (hors cerveau hors-ligne) joignable et compatible avec le budget."""
        probe = LLMRequest("", "x" * 2000, max_tokens=1500)
        for name in prefs:
            p = self.providers.get(name)
            if p is None or p.kind == "offline" or not await p.available():
                continue
            try:
                self.budget.check(p.estimate_cost(probe), f"sonde {name}", mission_id)
            except BudgetBlocked:
                continue
            return name
        return None

    def describe(self) -> list[dict[str, Any]]:
        return [p.describe() for p in self.providers.values()]


def parse_json_block(text: str) -> dict[str, Any] | None:
    """Extrait le premier objet JSON d'une réponse de modèle (tolérant au bruit)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None
