"""Le Rayon : tableau collectif (blackboard) de la ruche.

Règle fondatrice : les agents ne se parlent JAMAIS en privé. Toute communication
(observation, croyance, demande d'aide, découverte, décision, demande de validation)
est une *cellule* déposée sur le Rayon. Conséquences :
  * l'humain voit tout (observabilité totale depuis le smartphone) ;
  * la gouvernance peut intercepter toute écriture ;
  * tout est rejouable, car le Rayon est la projection du registre (ledger).

Chaque cellule porte une *phéromone* qui s'évapore (demi-vie) : l'attention de la
colonie se porte naturellement sur ce qui est récent, urgent et renforcé, sans
chef d'orchestre central (stigmergie).
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from .ledger import Event, Ledger

# Types de cellules (vocabulaire du protocole HIVE)
KINDS = {
    "mission",        # objectif donné par le Conseil humain
    "task",           # travail à faire (enchères entre agents)
    "observation",    # fait observé (OBSERVER)
    "belief",         # croyance du modèle du monde (fact / hypothesis / unknown)
    "contradiction",  # écart entre observation et croyance (CONFRONTER)
    "confirmation",   # observation qui confirme une croyance
    "hypothesis",     # explication candidate (HYPOTHÈSES)
    "forecast",       # prévision probabilisée, pré-enregistrée pour calibration
    "risk", "opportunity",
    "option",         # option priorisée (impact / ROI / urgence)
    "proposal",       # proposition indépendante de décision (quorum)
    "decision",       # décision retenue (DÉCIDER)
    "simulation",     # résultat de bac à sable (SIMULER)
    "approval",       # demande de validation humaine (HUMAN REVIEW)
    "result",         # résultat d'exécution (EXÉCUTER)
    "report",         # livrable final d'une mission
    "measurement",    # mesure vs objectifs (MESURER)
    "lesson",         # compétence / règle apprise (APPRENDRE)
    "discovery",      # découverte partagée (« danse » de recrutement)
    "need",           # demande d'aide / besoin d'information
    "message",        # message du Conseil ou notification
    "artifact",       # média (image, audio, vidéo, document)
    "dream",          # rapport de consolidation (DREAMING)
}
TASK_STATES = {"open", "claimed", "waiting", "done", "failed", "cancelled"}
BELIEF_STATES = {"fact", "hypothesis", "unknown"}
APPROVAL_STATES = {"pending", "approved", "rejected", "expired"}
MISSION_STATES = {"active", "done", "stopped", "cancelled"}

MUTABLE_FIELDS = {"status", "content", "data", "confidence", "claimed_by", "refs", "taint", "phase"}


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

    def __init__(self, ledger: Ledger, half_life_s: float = 900.0,
                 clock: Callable[[], float] = time.time):
        self.ledger = ledger
        self.half_life_s = half_life_s
        self.clock = clock
        self.cells: dict[str, Cell] = {}
        for ev in ledger.events():
            self._apply(ev)

    # -- phéromones ---------------------------------------------------------
    def intensity(self, cell: Cell, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        dt = max(0.0, now - cell.pheromone_ts)
        return cell.pheromone * (0.5 ** (dt / self.half_life_s))

    # -- écritures (toutes via le registre) ---------------------------------
    def post(self, kind: str, author: str, content: str, *, mission_id: str | None = None,
             data: dict[str, Any] | None = None, status: str | None = None,
             confidence: float | None = None, phase: str | None = None,
             skills: Iterable[str] = (), parent_id: str | None = None,
             refs: Iterable[str] = (), pheromone: float = 1.0, taint: bool = False,
             cell_id: str | None = None) -> Cell:
        if kind not in KINDS:
            raise ValueError(f"type de cellule inconnu : {kind}")
        if confidence is not None:
            confidence = max(0.0, min(1.0, float(confidence)))
        payload = {
            "id": cell_id or new_id(kind[:3]), "kind": kind, "author": author,
            "content": str(content), "mission_id": mission_id, "data": data or {},
            "status": status, "confidence": confidence, "phase": phase,
            "skills": list(skills), "parent_id": parent_id, "refs": list(refs),
            "pheromone": float(pheromone), "taint": bool(taint),
        }
        ev = self.ledger.append("cell.posted", author, payload, mission_id=mission_id)
        self._apply(ev)
        return self.cells[payload["id"]]

    def update(self, cell_id: str, actor: str, reason: str = "", **changes: Any) -> Cell:
        cell = self.cells[cell_id]
        bad = set(changes) - MUTABLE_FIELDS
        if bad:
            raise ValueError(f"champs non modifiables : {sorted(bad)}")
        if "taint" in changes and not changes["taint"] and cell.taint:
            raise ValueError("une cellule contaminée ne peut pas être « décontaminée »")
        if "confidence" in changes and changes["confidence"] is not None:
            changes["confidence"] = max(0.0, min(1.0, float(changes["confidence"])))
        ev = self.ledger.append("cell.updated", actor,
                                {"id": cell_id, "changes": changes, "reason": reason},
                                mission_id=cell.mission_id)
        self._apply(ev)
        return self.cells[cell_id]

    def reinforce(self, cell_id: str, actor: str, amount: float, reason: str = "") -> Cell:
        cell = self.cells[cell_id]
        ev = self.ledger.append("cell.reinforced", actor,
                                {"id": cell_id, "amount": float(amount), "reason": reason},
                                mission_id=cell.mission_id)
        self._apply(ev)
        return self.cells[cell_id]

    # -- projection ---------------------------------------------------------
    def _apply(self, ev: Event) -> None:
        p = ev.payload
        if ev.type == "cell.posted":
            self.cells[p["id"]] = Cell(
                id=p["id"], kind=p["kind"], author=p["author"], content=p["content"],
                mission_id=p.get("mission_id"), data=dict(p.get("data") or {}),
                status=p.get("status"), confidence=p.get("confidence"), phase=p.get("phase"),
                skills=list(p.get("skills") or []), parent_id=p.get("parent_id"),
                refs=list(p.get("refs") or []), pheromone=float(p.get("pheromone", 1.0)),
                pheromone_ts=ev.ts, taint=bool(p.get("taint")), created_at=ev.ts, updated_at=ev.ts,
            )
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
            if cell is None:
                return
            cell.pheromone = max(0.0, self.intensity(cell, ev.ts) + float(p["amount"]))
            cell.pheromone_ts = ev.ts
            cell.updated_at = ev.ts

    # -- requêtes -----------------------------------------------------------
    def get(self, cell_id: str) -> Cell | None:
        return self.cells.get(cell_id)

    def query(self, *, kind: str | Iterable[str] | None = None, mission_id: str | None = None,
              status: str | Iterable[str] | None = None, phase: str | None = None,
              author: str | None = None, parent_id: str | None = None) -> list[Cell]:
        kinds = {kind} if isinstance(kind, str) else (set(kind) if kind else None)
        states = {status} if isinstance(status, str) else (set(status) if status else None)
        out = [
            c for c in self.cells.values()
            if (kinds is None or c.kind in kinds)
            and (mission_id is None or c.mission_id == mission_id)
            and (states is None or c.status in states)
            and (phase is None or c.phase == phase)
            and (author is None or c.author == author)
            and (parent_id is None or c.parent_id == parent_id)
        ]
        return sorted(out, key=lambda c: (c.created_at, c.id))

    def open_tasks(self, now: float | None = None) -> list[Cell]:
        now = self.clock() if now is None else now
        tasks = self.query(kind="task", status="open")
        return sorted(tasks, key=lambda c: (-self.intensity(c, now), c.created_at, c.id))

    def find_duplicate(self, mission_id: str | None, kind: str, content: str) -> Cell | None:
        key = content_key(kind, content)
        for c in self.cells.values():
            if c.mission_id == mission_id and c.kind == kind and content_key(c.kind, c.content) == key:
                return c
        return None

    def tainted(self, cell_ids: Iterable[str]) -> bool:
        return any(self.cells[i].taint for i in cell_ids if i in self.cells)
