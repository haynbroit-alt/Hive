"""Le Rayon : tableau collectif (blackboard) de la ruche.

Règle fondatrice : les agents ne se parlent JAMAIS en privé. Toute communication
(observation, croyance, demande d'aide, découverte, script, image, vidéo, demande de
validation…) est une *trace* — une cellule — déposée sur le Rayon. Une ouvrière
démarre quand les traces qu'elle attend existent : c'est la stigmergie.

Le Rayon est une PROJECTION du registre : toute écriture passe par le registre,
puis le registre la renvoie au Rayon (même si elle vient d'un autre processus).
Chaque cellule porte la révision (`rev`) de la mission qui l'a produite, et une
phéromone qui s'évapore (demi-vie) pour orienter l'attention de la colonie.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from .ledger import Event, Ledger

# Vocabulaire de base du protocole HIVE ; les ouvrières peuvent en déclarer d'autres.
KINDS: set[str] = {
    "mission", "task", "observation", "belief", "contradiction", "confirmation", "hypothesis",
    "forecast", "risk", "opportunity", "option", "proposal", "decision", "simulation", "approval",
    "result", "report", "measurement", "lesson", "discovery", "need", "message", "artifact", "dream",
    "clearance",
}
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
BELIEF_STATES = {"fact", "hypothesis", "unknown"}
MUTABLE_FIELDS = {"status", "content", "data", "confidence", "claimed_by", "refs", "taint", "phase"}


def register_kinds(*kinds: str) -> None:
    for k in kinds:
        if not _KIND_RE.match(k):
            raise ValueError(f"nom de trace invalide : {k}")
        KINDS.add(k)


def new_id(prefix: str = "c") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def content_key(kind: str, content: str) -> str:
    norm = " ".join(content.lower().split())
    return hashlib.sha256(f"{kind}|{norm}".encode()).hexdigest()[:16]


@dataclass
class Cell:
    id: str
    kind: str
    author: str
    content: str
    mission_id: str | None = None
    rev: int = 1
    data: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    confidence: float | None = None
    phase: str | None = None
    skills: list[str] = field(default_factory=list)
    parent_id: str | None = None
    refs: list[str] = field(default_factory=list)
    pheromone: float = 1.0
    pheromone_ts: float = 0.0
    taint: bool = False
    claimed_by: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Board:
    """Projection en mémoire du registre. Toute écriture passe par le registre."""

    def __init__(self, ledger: Ledger, half_life_s: float = 900.0, clock: Callable[[], float] = time.time):
        self.ledger = ledger
        self.half_life_s = half_life_s
        self.clock = clock
        self.cells: dict[str, Cell] = {}
        self._by_mission: dict[str | None, list[str]] = {}
        ledger.register(self)

    # -- phéromones ---------------------------------------------------------
    def intensity(self, cell: Cell, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        return cell.pheromone * (0.5 ** (max(0.0, now - cell.pheromone_ts) / self.half_life_s))

    # -- écritures (toutes via le registre) ---------------------------------
    def post(self, kind: str, author: str, content: str, *, mission_id: str | None = None, rev: int = 1,
             data: dict[str, Any] | None = None, status: str | None = None, confidence: float | None = None,
             phase: str | None = None, skills: Iterable[str] = (), parent_id: str | None = None,
             refs: Iterable[str] = (), pheromone: float = 1.0, taint: bool = False,
             cell_id: str | None = None) -> Cell:
        if kind not in KINDS:
            raise ValueError(f"type de cellule inconnu : {kind}")
        if confidence is not None:
            confidence = max(0.0, min(1.0, float(confidence)))
        payload = {
            "id": cell_id or new_id(kind[:3]), "kind": kind, "author": author, "content": str(content),
            "mission_id": mission_id, "rev": int(rev), "data": data or {}, "status": status,
            "confidence": confidence, "phase": phase, "skills": list(skills), "parent_id": parent_id,
            "refs": list(refs), "pheromone": float(pheromone), "taint": bool(taint),
        }
        self.ledger.append("cell.posted", author, payload, mission_id=mission_id)
        return self.cells[payload["id"]]

    def update(self, cell_id: str, actor: str, reason: str = "", **changes: Any) -> Cell:
        cell = self.cells[cell_id]
        bad = set(changes) - MUTABLE_FIELDS
        if bad:
            raise ValueError(f"champs non modifiables : {sorted(bad)}")
        if "taint" in changes and not changes["taint"] and cell.taint:
            raise ValueError("une cellule contaminée ne peut pas être « décontaminée »")
        if changes.get("confidence") is not None:
            changes["confidence"] = max(0.0, min(1.0, float(changes["confidence"])))
        self.ledger.append("cell.updated", actor, {"id": cell_id, "changes": changes, "reason": reason},
                           mission_id=cell.mission_id)
        return self.cells[cell_id]

    def reinforce(self, cell_id: str, actor: str, amount: float, reason: str = "") -> Cell:
        cell = self.cells[cell_id]
        self.ledger.append("cell.reinforced", actor, {"id": cell_id, "amount": float(amount), "reason": reason},
                           mission_id=cell.mission_id)
        return self.cells[cell_id]

    # -- projection ---------------------------------------------------------
    def apply(self, ev: Event) -> None:
        p = ev.payload
        if ev.type == "cell.posted":
            c = Cell(id=p["id"], kind=p["kind"], author=p["author"], content=p["content"],
                     mission_id=p.get("mission_id"), rev=int(p.get("rev", 1)), data=dict(p.get("data") or {}),
                     status=p.get("status"), confidence=p.get("confidence"), phase=p.get("phase"),
                     skills=list(p.get("skills") or []), parent_id=p.get("parent_id"),
                     refs=list(p.get("refs") or []), pheromone=float(p.get("pheromone", 1.0)),
                     pheromone_ts=ev.ts, taint=bool(p.get("taint")), created_at=ev.ts, updated_at=ev.ts)
            self.cells[c.id] = c
            self._by_mission.setdefault(c.mission_id, []).append(c.id)
        elif ev.type == "cell.updated":
            cell = self.cells.get(p["id"])
            if cell is None:
                return
            for k, v in p["changes"].items():
                if k == "data":
                    cell.data = {**cell.data, **(v or {})}
                elif k == "taint":
                    cell.taint = cell.taint or bool(v)
                else:
                    setattr(cell, k, v)
            cell.updated_at = ev.ts
        elif ev.type == "cell.reinforced":
            cell = self.cells.get(p["id"])
            if cell is not None:
                cell.pheromone = max(0.0, self.intensity(cell, ev.ts) + float(p["amount"]))
                cell.pheromone_ts = cell.updated_at = ev.ts

    # -- requêtes -----------------------------------------------------------
    def get(self, cell_id: str) -> Cell | None:
        return self.cells.get(cell_id)

    def query(self, *, kind: str | Iterable[str] | None = None, mission_id: str | None = None,
              status: str | Iterable[str] | None = None, phase: str | None = None, author: str | None = None,
              parent_id: str | None = None, rev: int | None = None) -> list[Cell]:
        kinds = {kind} if isinstance(kind, str) else (set(kind) if kind else None)
        states = {status} if isinstance(status, str) else (set(status) if status else None)
        pool = (self.cells[i] for i in self._by_mission.get(mission_id, [])) if mission_id is not None \
            else self.cells.values()
        out = [c for c in pool
               if (kinds is None or c.kind in kinds) and (states is None or c.status in states)
               and (phase is None or c.phase == phase) and (author is None or c.author == author)
               and (parent_id is None or c.parent_id == parent_id) and (rev is None or c.rev == rev)]
        return sorted(out, key=lambda c: (c.created_at, c.id))

    def latest(self, mission_id: str, kind: str, rev: int | None = None, **filters: Any) -> Cell | None:
        cells = [c for c in self.query(mission_id=mission_id, kind=kind, rev=rev)
                 if all(c.data.get(k) == v for k, v in filters.items())]
        return cells[-1] if cells else None

    def open_tasks(self, now: float | None = None) -> list[Cell]:
        now = self.clock() if now is None else now
        return sorted(self.query(kind="task", status="open"),
                      key=lambda c: (-self.intensity(c, now), c.created_at, c.id))

    def find_duplicate(self, mission_id: str | None, kind: str, content: str, rev: int | None = None) -> Cell | None:
        key = content_key(kind, content)
        for c in self.query(mission_id=mission_id, kind=kind, rev=rev):
            if content_key(c.kind, c.content) == key:
                return c
        return None

    def tainted(self, cell_ids: Iterable[str]) -> bool:
        return any(self.cells[i].taint for i in cell_ids if i in self.cells)
