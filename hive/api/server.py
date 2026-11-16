"""API du Conseil + application mobile (PWA) + flux temps réel (SSE).

Sécurité :
* écoute sur 127.0.0.1 par défaut ; accès distant via un tunnel privé (Tailscale) — jamais
  d'exposition publique directe (cf. incidents OpenClaw 2026, docs/SECURITY.md) ;
* jeton d'accès (≥ 192 bits) ; cookie HttpOnly SameSite=Strict ; en-tête X-Hive exigé sur
  toute requête modifiante (anti-CSRF) ; CSP stricte sans script tiers ;
* limitation des tentatives de connexion.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Hive
from ..core.board import Cell
from ..core.cycle import PROFILE_LABELS
from ..core.dreaming import Dreamer
from ..core.ledger import Event
from ..core.views import STATUS_FR, event_summary, mission_brief

STATIC = Path(__file__).parent / "static"
COOKIE = "hive_session"
SAFE_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "audio/wav", "audio/mpeg", "audio/ogg"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'; object-src 'none'")


class LoginIn(BaseModel):
    token: str = Field(max_length=200)


class DecisionIn(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)


class FeedbackIn(BaseModel):
    score: float = Field(ge=0, le=1)
    comment: str = Field(default="", max_length=1000)


class OutcomeIn(BaseModel):
    outcome: bool


class DirectiveIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    mission_id: str | None = None


class NoteIn(BaseModel):
    note: str = Field(min_length=2, max_length=2000)


class TopupIn(BaseModel):
    amount: float = Field(gt=0, le=1000)


class BudgetIn(BaseModel):
    cap: float = Field(ge=0, le=10_000)
    reason: str = Field(min_length=3, max_length=500)


def cell_json(c: Cell) -> dict[str, Any]:
    d = c.to_dict()
    d["data"] = {k: v for k, v in c.data.items() if k not in ("bids",)} if c.kind != "task" else c.data
    return d


def create_app(hive: Hive, token: str, *, run_colony: bool = True, secure_cookie: bool = False) -> FastAPI:
    colony, board, gov = hive.colony, hive.board, hive.governance
    session_value = hmac.new(token.encode(), b"hive-session-v1", hashlib.sha256).hexdigest()
    failures: dict[str, list[float]] = {}
    subscribers: set[asyncio.Queue] = set()
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()

        def on_event(ev: Event) -> None:
            summary = event_summary(ev)
            for q in list(subscribers):
                loop.call_soon_threadsafe(_offer, q, summary)

        unsubscribe = hive.ledger.subscribe(on_event)
        stop = asyncio.Event()
        tasks = [asyncio.create_task(colony.run_forever(float(hive.config["colony"]["tick_s"]), stop))] \
            if run_colony else []
        tasks += [asyncio.create_task(fn(stop)) for fn in app.state.background]  # ex. canal Telegram
        state["stop"] = stop
        try:
            yield
        finally:
            stop.set()
            unsubscribe()
            for task in tasks:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(title="HIVE", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.background = []  # tâches de fond supplémentaires : fn(stop_event) -> coroutine

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
                and request.headers.get("x-hive") != "1":
            return JSONResponse({"detail": "en-tête X-Hive manquant"}, status_code=403)
        if request.url.path.startswith("/api/"):
            hive.ledger.catch_up()  # voir aussi ce que la CLI ou un autre processus vient d'écrire
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Permissions-Policy"] = "microphone=(self), camera=(self), geolocation=()"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def authed(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), token.encode()):
            return "conseil"
        cookie = request.cookies.get(COOKIE, "")
        if cookie and hmac.compare_digest(cookie.encode(), session_value.encode()):
            return "conseil"
        raise HTTPException(401, "authentification requise")

    # ------------------------------------------------------------ PWA
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    async def sw():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                            headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------ session
    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response):
        ip = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in failures.get(ip, []) if now - t < 300]
        if len(recent) >= 5:
            raise HTTPException(429, "trop de tentatives, réessayez dans 5 minutes")
        if not hmac.compare_digest(body.token.encode(), token.encode()):
            failures[ip] = recent + [now]
            hive.ledger.append("auth.failed", "inconnu", {"ip": ip})
            raise HTTPException(401, "jeton invalide")
        failures.pop(ip, None)
        response.set_cookie(COOKIE, session_value, httponly=True, samesite="strict", secure=secure_cookie,
                            max_age=30 * 24 * 3600, path="/")
        hive.ledger.append("auth.login", "conseil", {"ip": ip})
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ------------------------------------------------------------ état
    @app.get("/api/status")
    async def status(who: str = Depends(authed)):
        return {**colony.status(), "profiles": PROFILE_LABELS}

    @app.get("/api/missions")
    async def missions(who: str = Depends(authed)):
        out = []
        for m in reversed(board.query(kind="mission")):
            out.append({"id": m.id, "number": m.data.get("number"), "text": m.content[:300], "status": m.status,
                        "status_fr": STATUS_FR.get(m.status, m.status), "profile": m.data.get("profile"),
                        "rev": m.data.get("rev", 1), "step": colony.current_step(m), "score": m.data.get("score"),
                        "outcome": m.data.get("outcome"), "created_at": m.created_at,
                        "end_reason": m.data.get("end_reason")})
        return out[:100]

    @app.get("/api/missions/{mission_id}")
    async def mission_detail(mission_id: str, who: str = Depends(authed)):
        m = board.get(mission_id)
        if m is None or m.kind != "mission":
            raise HTTPException(404, "mission inconnue")
        cells = [cell_json(c) for c in board.query(mission_id=mission_id)]
        return {"mission": {**cell_json(m), "step": colony.current_step(m)}, "brief": mission_brief(colony, m),
                "cells": cells}

    @app.post("/api/missions/{mission_id}/revise")
    async def revise(mission_id: str, body: NoteIn, who: str = Depends(authed)):
        try:
            ok = await colony.request_revision(mission_id, body.note, by=who)
        except KeyError:
            raise HTTPException(404, "mission inconnue")
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": ok, "rev": board.get(mission_id).data.get("rev")}

    @app.post("/api/missions/{mission_id}/topup")
    async def topup(mission_id: str, body: TopupIn, who: str = Depends(authed)):
        if board.get(mission_id) is None:
            raise HTTPException(404, "mission inconnue")
        return colony.topup(mission_id, body.amount, by=who)

    @app.get("/api/workers")
    async def workers(who: str = Depends(authed)):
        return [w.describe() for w in colony.workers.values()]

    @app.post("/api/missions")
    async def create_mission(text: str = Form(..., max_length=8000), profile: str = Form("auto"),
                             files: list[UploadFile] = File(default=[]), who: str = Depends(authed)):
        arts = []
        for f in files[:6]:
            data = await f.read()
            try:
                arts.append(hive.artifacts.put(data, f.filename or "fichier", f.content_type))
            except ValueError as e:
                raise HTTPException(400, str(e))
        try:
            m = await colony.submit_mission(text, by=who, profile=None if profile == "auto" else profile,
                                            attachments=arts)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": m.id, "number": m.data["number"], "profile": m.data["profile"], "plan": m.data["plan"]}

    @app.post("/api/missions/{mission_id}/cancel")
    async def cancel(mission_id: str, who: str = Depends(authed)):
        colony.cancel_mission(mission_id, who)
        return {"ok": True}

    @app.post("/api/missions/{mission_id}/feedback")
    async def feedback(mission_id: str, body: FeedbackIn, who: str = Depends(authed)):
        try:
            await colony.feedback(mission_id, body.score, body.comment, by=who)
        except KeyError:
            raise HTTPException(404, "mission inconnue")
        return {"ok": True}

    @app.get("/api/artifacts/{art_id}")
    async def artifact(art_id: str, who: str = Depends(authed)):
        a = hive.artifacts.get(art_id)
        if a is None:
            raise HTTPException(404, "inconnu")
        # seuls les formats inertes s'affichent en ligne ; le reste (SVG, HTML, texte...) est téléchargé
        inline = a.mime in SAFE_INLINE or a.mime.startswith(("audio/", "video/"))
        return FileResponse(a.path, media_type=a.mime if inline else "application/octet-stream",
                            filename=a.name, content_disposition_type="inline" if inline else "attachment")

    # ------------------------------------------------------------ validations
    @app.get("/api/approvals")
    async def approvals(who: str = Depends(authed)):
        needs = [c for c in board.query(kind="need", status="open") if c.data.get("for_human")]
        forecasts = [c for c in board.query(kind="forecast") if not c.data.get("resolved")]
        return {"approvals": [cell_json(c) for c in gov.pending_approvals()],
                "lessons": [cell_json(c) for c in board.query(kind="lesson", status="proposed")],
                "forecasts": [cell_json(c) for c in forecasts[-20:]],
                "needs": [cell_json(c) for c in needs[-20:]]}

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: DecisionIn, who: str = Depends(authed)):
        try:
            c = await colony.resolve_approval(approval_id, body.approved, by=who, comment=body.comment)
        except KeyError:
            raise HTTPException(404, "validation inconnue")
        except ValueError as e:
            raise HTTPException(409, str(e))
        return cell_json(c)

    @app.post("/api/lessons/{lesson_id}")
    async def lesson(lesson_id: str, body: DecisionIn, who: str = Depends(authed)):
        try:
            return cell_json(await colony.review_lesson(lesson_id, body.approved, by=who))
        except KeyError:
            raise HTTPException(404, "leçon inconnue")

    @app.post("/api/forecasts/{forecast_id}")
    async def forecast(forecast_id: str, body: OutcomeIn, who: str = Depends(authed)):
        try:
            return {"brier": await colony.resolve_forecast(forecast_id, body.outcome, by=who)}
        except KeyError:
            raise HTTPException(404, "prévision inconnue")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/needs/{need_id}/close")
    async def close_need(need_id: str, who: str = Depends(authed)):
        c = board.get(need_id)
        if c is None or c.kind != "need":
            raise HTTPException(404, "inconnu")
        board.update(need_id, who, reason="traité par le Conseil", status="done")
        return {"ok": True}

    # ------------------------------------------------------------ colonie
    @app.get("/api/agents")
    async def agents(who: str = Depends(authed)):
        rep = hive.reputation.snapshot()
        return {"agents": [{**a.spec.to_dict(), "reputation": rep.get(a.name, {})} for a in colony.agents.values()],
                "providers": hive.router.describe(), "tools": hive.colony.tools.specs()}

    @app.post("/api/colony/pause")
    async def pause(who: str = Depends(authed)):
        gov.pause(who, "interrupteur d'arrêt (smartphone)")
        return colony.status()

    @app.post("/api/colony/resume")
    async def resume(who: str = Depends(authed)):
        gov.resume(who, "reprise (smartphone)")
        return colony.status()

    @app.post("/api/directives")
    async def directive(body: DirectiveIn, who: str = Depends(authed)):
        c = await colony.post_directive(body.text, body.mission_id, by=who)
        return cell_json(c)

    @app.post("/api/dream")
    async def dream(who: str = Depends(authed)):
        return cell_json(Dreamer(colony).dream())

    @app.post("/api/budget/request")
    async def budget_request(body: BudgetIn, who: str = Depends(authed)):
        c = colony.request_budget(body.cap, body.reason, by=who)
        return cell_json(c)

    @app.get("/api/ledger")
    async def ledger(since: int = 0, limit: int = 200, who: str = Depends(authed)):
        evs = hive.ledger.events(since_seq=since, limit=min(limit, 1000)) if since else hive.ledger.tail(min(limit, 1000))
        return [event_summary(e) for e in evs]

    @app.get("/api/ledger/verify")
    async def verify(who: str = Depends(authed)):
        ok, bad = hive.ledger.verify()
        return {"ok": ok, "first_bad_seq": bad, "events": hive.ledger.last_seq}

    @app.get("/api/stream")
    async def stream(request: Request, who: str = Depends(authed)):
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        subscribers.add(q)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return app


def _offer(q: asyncio.Queue, item: dict[str, Any]) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
