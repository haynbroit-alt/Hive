"""Client HTTP asynchrone minimal, 100 % bibliothèque standard (urllib dans un fil).

Le cœur de HIVE n'a ainsi AUCUNE dépendance externe : il tourne sur n'importe quel
Python ≥ 3.11 (PC, Raspberry Pi, vieux téléphone Android sous Termux).
Les tests remplacent `transport` par une fonction (aucun réseau).
"""
from __future__ import annotations

import asyncio
import json as _json
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class HTTPError(Exception):
    pass


@dataclass
class Response:
    status: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return _json.loads(self.content.decode("utf-8") or "null")

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise HTTPError(f"HTTP {self.status}: {self.text[:200]}")


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float


def _urllib_transport(req: Request) -> Response:
    r = urllib.request.Request(req.url, data=req.body, method=req.method, headers=req.headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(r, timeout=req.timeout, context=ctx) as resp:  # noqa: S310 (URL contrôlée)
            return Response(resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()})
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read() or b"", {k.lower(): v for k, v in (e.headers or {}).items()})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPError(str(e)) from e


# Point d'injection pour les tests : transport(Request) -> Response
transport: Callable[[Request], Response] = _urllib_transport


def multipart(fields: dict[str, Any], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"hive{uuid.uuid4().hex}"
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, data, mime) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


async def request(method: str, url: str, *, json: Any = None, data: dict[str, Any] | None = None,
                  files: dict[str, tuple[str, bytes, str]] | None = None, headers: dict[str, str] | None = None,
                  timeout: float = 60.0) -> Response:
    h = {"User-Agent": "hive-colony/0.2", **(headers or {})}
    body: bytes | None = None
    if json is not None:
        body = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"
    elif files is not None or data is not None:
        body, ctype = multipart(data or {}, files or {})
        h["Content-Type"] = ctype
    return await asyncio.to_thread(transport, Request(method.upper(), url, h, body, timeout))
