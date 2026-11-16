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
from ..core.cycle import PHASES, PROFILE_LABELS
from ..core.dreaming import Dreamer
from ..core.ledger import Event

STATIC = Path(__file__).parent / "static"
COOKIE = "hive_session"
SAFE_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp"}

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


class BudgetIn(BaseModel):
    cap: float = Field(ge=0, le=10_000)
    reason: str = Field(min_length=3, max_length=500)


def cell_json(c: Cell) -> dict[str, Any]:
    d = c.to_dict()
    d["data"] = {k: v for k, v in c.data.items() if k not in ("bids",)} if c.kind != "task" else c.data
    return d


def event_summary(ev: Event) -> dict[str, Any]:
    p = ev.payload
    out: dict[str, Any] = {"seq": ev.seq, "ts": ev.ts, "type": ev.type, "actor": ev.actor,
                           "mission_id": ev.mission_id}
    if ev.type == "cell.posted":
        out.update(kind=p.get("kind"), id=p.get("id"), status=p.get("status"), phase=p.get("phase"),
                   taint=p.get("taint"), text=str(p.get("content", ""))[:220])
    elif ev.type == "cell.updated":
        ch = p.get("changes", {})
        out.update(id=p.get("id"), status=ch.get("status"), claimed_by=ch.get("claimed_by"),
                   text=p.get("reason", ""))
    elif ev.type == "llm.called":
        out.update(text=f"{p.get('provider')}/{p.get('model')} · {p.get('tokens_in')}→{p.get('tokens_out')} jetons"
                        f" · {p.get('cost', 0):.4f} €")
    elif ev.type in ("protocol.violation", "budget.blocked", "llm.error", "task.error", "colony.error"):
        out.update(text=str(p.get("what") or p.get("reason") or p.get("error") or p.get("trace") or "")[-220:])
    elif ev.type == "agent.joined":
        out.update(text=f"{p.get('title')} rejoint la ruche (modèles : {' › '.join(p.get('models', []))})")
    elif ev.type == "task.finished":
        out.update(text=f"{p.get('agent')} a {'terminé' if p.get('ok') else 'échoué'} : {p.get('phase')}")
    elif ev.type == "reputation.updated":
        out.update(text=f"{p.get('agent')} · {p.get('skill')} ← {p.get('score'):.2f} ({p.get('reason')})")
    elif ev.type in ("auth.login", "auth.failed", "auth.telegram_rejected"):
        out.update(text={"auth.login": "connexion du Conseil", "auth.failed": "tentative de connexion refusée",
                         "auth.telegram_rejected": "message Telegram d'un inconnu ignoré"}[ev.type])
    elif ev.type in ("colony.paused", "colony.resumed"):
        out.update(text=("⏸ ruche en pause" if ev.type == "colony.paused" else "▶ ruche relancée")
                   + (f" — {p['reason']}" if p.get("reason") else ""))
    elif ev.type == "mission.step":
        out.update(text=PHASES[p["step"]].label if p.get("step") in PHASES else str(p.get("step", "")).upper())
    else:
        out.update(text=json.dumps(p, ensure_ascii=False)[:220])
    return out


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
            out.append({"id": m.id, "text": m.content[:300], "status": m.status, "profile": m.data.get("profile"),
                        "step": colony.current_step(m), "plan": m.data.get("plan"), "score": m.data.get("score"),
                        "created_at": m.created_at, "waiting_on": m.data.get("waiting_on"),
                        "end_reason": m.data.get("end_reason")})
        return out[:100]

    @app.get("/api/missions/{mission_id}")
    async def mission_detail(mission_id: str, who: str = Depends(authed)):
        m = board.get(mission_id)
        if m is None or m.kind != "mission":
            raise HTTPException(404, "mission inconnue")
        cells = [cell_json(c) for c in board.query(mission_id=mission_id)]
        return {"mission": {**cell_json(m), "step": colony.current_step(m)}, "cells": cells}

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
        return {"id": m.id, "profile": m.data["profile"], "plan": m.data["plan"]}

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
