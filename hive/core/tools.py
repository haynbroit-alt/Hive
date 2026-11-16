"""Outils de la ruche : chaque outil sait se SIMULER avant d'être exécuté.

Chaque outil déclare : risque de base, effet externe ou non, réversibilité, dépense.
La colonie joue toujours `simulate()` (bac à sable) puis soumet l'action aux Gardiennes,
qui décident : exécution automatique, ou validation humaine.
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .governance import Risk


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    content: str
    taint: bool = False
    data: dict[str, Any] = field(default_factory=dict)


class Tool:
    name = "tool"
    description = ""
    risk = Risk.LOW
    external = False
    reversible = True
    spends_money = False

    def allowed(self, args: dict[str, Any]) -> bool:
        return True

    def simulate(self, args: dict[str, Any]) -> str:
        return f"{self.name}({args})"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "risk": self.risk.label,
                "external": self.external, "reversible": self.reversible}


# -- calc : arithmétique sûre (aucun eval) ---------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expr: str) -> float:
    if len(expr) > 200:
        raise ToolError("expression trop longue")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolError("exposant trop grand")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ToolError("expression non autorisée")

    try:
        return ev(ast.parse(expr, mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError) as e:
        raise ToolError(str(e)) from e


class CalcTool(Tool):
    name = "calc"
    description = "Calcul arithmétique sûr. args: {expression}"

    def simulate(self, args: dict[str, Any]) -> str:
        return f"calculer {args.get('expression')!r} (aucun effet de bord)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(str(safe_eval(str(args.get("expression", "")))))


# -- espace de travail confiné ---------------------------------------------------
class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        rel = str(rel).replace("\\", "/").lstrip("/")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError("chemin hors de l'espace de travail")
        return p


class WorkspaceReadTool(Tool):
    name = "workspace.read"
    description = "Lit un fichier texte de l'espace de travail. args: {path}"

    def __init__(self, ws: Workspace):
        self.ws = ws

    def simulate(self, args: dict[str, Any]) -> str:
        return f"lire {args.get('path')} (lecture seule)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        p = self.ws.resolve(args.get("path", ""))
        if not p.is_file():
            raise ToolError("fichier introuvable")
        return ToolResult(p.read_text(encoding="utf-8", errors="replace")[:20000])


class WorkspaceWriteTool(Tool):
    name = "workspace.write"
    description = "Écrit un fichier texte dans l'espace de travail (versionné). args: {path, content}"
    risk = Risk.MEDIUM

    def __init__(self, ws: Workspace):
        self.ws = ws

    def allowed(self, args: dict[str, Any]) -> bool:
        try:
            self.ws.resolve(args.get("path", ""))
        except ToolError:
            return False
        return len(str(args.get("content", ""))) <= 200_000

    def simulate(self, args: dict[str, Any]) -> str:
        p = self.ws.resolve(args.get("path", ""))
        existed = p.exists()
        n = len(str(args.get("content", "")))
        return (f"{'remplacer' if existed else 'créer'} {p.relative_to(self.ws.root)} ({n} caractères) ; "
                f"{'version précédente conservée' if existed else 'nouveau fichier'} ; aucun effet hors de la machine")

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        p = self.ws.resolve(args.get("path", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():  # réversibilité : on garde la version précédente
            n = 1
            while p.with_name(f"{p.name}.v{n}").exists():
                n += 1
            p.rename(p.with_name(f"{p.name}.v{n}"))
        p.write_text(str(args.get("content", "")), encoding="utf-8")
        return ToolResult(f"écrit : {p.relative_to(self.ws.root)}", data={"path": str(p.relative_to(self.ws.root))})


# -- web.fetch : lecture web sur liste blanche, résultat contaminé ------------------
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


class WebFetchTool(Tool):
    name = "web.fetch"
    description = "Lit une page web (domaines autorisés uniquement). Le contenu est NON FIABLE. args: {url}"
    risk = Risk.MEDIUM

    def __init__(self, allowed_domains: list[str], max_bytes: int = 500_000):
        self.allowed_domains = [d.lower().lstrip(".") for d in allowed_domains]
        self.max_bytes = max_bytes

    def allowed(self, args: dict[str, Any]) -> bool:
        u = urlparse(str(args.get("url", "")))
        host = (u.hostname or "").lower()
        if u.scheme != "https" or not host:
            return False
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)

    def simulate(self, args: dict[str, Any]) -> str:
        return f"GET {args.get('url')} (lecture ; l'URL elle-même sort de la machine)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        if not self.allowed(args):
            raise ToolError("domaine non autorisé")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
                r = await c.get(str(args["url"]), headers={"User-Agent": "hive-colony/0.1"})
        except httpx.HTTPError as e:
            raise ToolError(f"échec réseau : {e}") from e
        body = r.content[: self.max_bytes].decode(r.encoding or "utf-8", errors="replace")
        text = " ".join(_TAGS.sub(" ", body).split())
        return ToolResult(text[:8000], taint=True, data={"status": r.status_code})


# -- webhook.post : effet EXTERNE, toujours sous validation ---------------------------
class WebhookPostTool(Tool):
    name = "webhook.post"
    description = "Envoie un JSON à un webhook autorisé (effet externe). args: {url, payload}"
    risk = Risk.HIGH
    external = True
    reversible = False

    def __init__(self, allowed_urls: list[str]):
        self.allowed_urls = allowed_urls

    def allowed(self, args: dict[str, Any]) -> bool:
        return str(args.get("url", "")) in self.allowed_urls

    def simulate(self, args: dict[str, Any]) -> str:
        return f"POST vers {args.get('url')} — EFFET EXTERNE IRRÉVERSIBLE — contenu : {str(args.get('payload'))[:300]}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        if not self.allowed(args):
            raise ToolError("URL non autorisée")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(str(args["url"]), json=args.get("payload"))
        except httpx.HTTPError as e:
            raise ToolError(f"échec réseau : {e}") from e
        return ToolResult(f"HTTP {r.status_code}", data={"status": r.status_code})


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError(f"outil inconnu : {name}")
        return self.tools[name]

    def specs(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        return [t.spec() for n, t in self.tools.items() if names is None or n in names]


def default_tools(workspace_dir: Path, web_domains: list[str], webhook_urls: list[str]) -> ToolRegistry:
    ws = Workspace(workspace_dir)
    return ToolRegistry([CalcTool(), WorkspaceReadTool(ws), WorkspaceWriteTool(ws),
                         WebFetchTool(web_domains), WebhookPostTool(webhook_urls)])
