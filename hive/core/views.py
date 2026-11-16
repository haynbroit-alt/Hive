"""Vues lisibles par un humain (CLI, API, Telegram) — aucune dépendance."""
from __future__ import annotations

import json
import time
from typing import Any

from .cycle import PHASES
from .ledger import Event

STATUS_FR = {"active": "en cours", "awaiting_human": "attend le Conseil", "done": "terminée", "stopped": "arrêtée",
             "cancelled": "annulée"}


def event_summary(ev: Event) -> dict[str, Any]:
    p = ev.payload
    out: dict[str, Any] = {"seq": ev.seq, "ts": ev.ts, "type": ev.type, "actor": ev.actor, "mission_id": ev.mission_id}
    if ev.type == "cell.posted":
        out.update(kind=p.get("kind"), id=p.get("id"), status=p.get("status"), phase=p.get("phase"),
                   taint=p.get("taint"), rev=p.get("rev"), text=str(p.get("content", ""))[:220])
    elif ev.type == "cell.updated":
        ch = p.get("changes", {})
        out.update(id=p.get("id"), status=ch.get("status"), claimed_by=ch.get("claimed_by"), text=p.get("reason", ""))
    elif ev.type == "llm.called":
        out.update(text=f"{p.get('provider')}/{p.get('model')} · {p.get('tokens_in')}→{p.get('tokens_out')} jetons"
                        f" · {p.get('cost', 0):.4f} €")
    elif ev.type == "run.claimed":
        out.update(text=f"{p.get('bee') or '—'} prend « {p.get('worker')} » ({p.get('key')}, rév. {p.get('rev')})")
    elif ev.type in ("run.finished", "run.blocked"):
        out.update(text=f"« {p.get('worker')} » ({p.get('key')}) → {p.get('status')}"
                        + (f" : {p.get('error')}" if p.get("error") else ""))
    elif ev.type == "agent.joined":
        out.update(text=f"{p.get('title')} rejoint la ruche (modèles : {' › '.join(p.get('models', []))})")
    elif ev.type == "reputation.updated":
        out.update(text=f"{p.get('agent')} · {p.get('skill')} ← {p.get('score'):.2f} ({p.get('reason')})")
    elif ev.type in ("auth.login", "auth.failed", "auth.telegram_rejected"):
        out.update(text={"auth.login": "connexion du Conseil", "auth.failed": "tentative de connexion refusée",
                         "auth.telegram_rejected": "message Telegram d'un inconnu ignoré"}[ev.type])
    elif ev.type in ("colony.paused", "colony.resumed"):
        out.update(text=("⏸ ruche en pause" if ev.type == "colony.paused" else "▶ ruche relancée")
                   + (f" — {p['reason']}" if p.get("reason") else ""))
    elif ev.type in ("budget.charged", "budget.topup", "budget.cap_changed"):
        out.update(text=f"{ev.type.split('.')[1]} : {p.get('amount', p.get('cap'))} €")
    elif ev.type in ("protocol.violation", "budget.blocked", "llm.error", "task.error", "colony.error"):
        out.update(text=str(p.get("what") or p.get("reason") or p.get("error") or p.get("trace") or "")[-220:])
    elif ev.type == "mission.step":
        out.update(text=PHASES[p["step"]].label if p.get("step") in PHASES else str(p.get("step", "")).upper())
    else:
        out.update(text=json.dumps(p, ensure_ascii=False)[:220])
    return out


def hhmmss(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def mission_brief(colony, m) -> dict[str, Any]:
    from .workers import Ctx
    ctx = Ctx(colony, m)
    workers = {}
    for w in colony.workers.values():
        if not w.applies(ctx):
            continue
        runs = colony.runs.of(m.id, w.name, ctx.rev)
        workers[w.name] = ("✓" if colony.worker_done(ctx, w.name) and runs else
                           "…" if any(not r.finished for r in runs) else "·")
    outputs = []
    for kind in ("video", "report", "qa"):
        for c in ctx.traces(kind):
            art = colony.artifacts.get(c.data.get("artifact", "")) if c.data.get("artifact") else None
            outputs.append({"kind": kind, "text": c.content[:300], "file": art.path if art else None,
                            "artifact": c.data.get("artifact")})
    return {"id": m.id, "number": m.data.get("number"), "text": m.content, "status": m.status,
            "status_fr": STATUS_FR.get(m.status, m.status), "profile": m.data.get("profile"), "rev": ctx.rev,
            "revisions_used": m.data.get("revisions_used", 0), "score": m.data.get("score"),
            "outcome": m.data.get("outcome"), "end_reason": m.data.get("end_reason"),
            "running": colony.current_step(m), "workers": workers, "outputs": outputs,
            "pending_approvals": [{"id": a.id, "title": a.content} for a in colony.gov.pending_approvals(m.id)],
            "history": m.data.get("history", []), "created_at": m.created_at,
            "budget": {"cap": colony.gov.budget.mission_cap(m.id), "spent":
                       round(colony.gov.budget.mission_spent.get(m.id, 0.0), 4)}}
