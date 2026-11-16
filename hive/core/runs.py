"""Exécutions des ouvrières : verrous d'idempotence + cache par hachage des entrées.

Chaque exécution est identifiée par (mission, ouvrière, clé, révision) et verrouillée
atomiquement dans le registre (deux processus ne peuvent pas prendre la même).
Son résultat est indexé par le hachage de ses ENTRÉES : si une révision redemande le
même travail avec les mêmes entrées, le résultat est réutilisé (comme un système de
build incrémental) au lieu d'être recalculé. Les échecs ne sont jamais mis en cache.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .ledger import AlreadyClaimed, Event, Ledger, canonical


def input_hash(worker: str, version: str, key: str, inputs: Any) -> str:
    return hashlib.sha256(canonical([worker, version, key, inputs]).encode()).hexdigest()[:32]


@dataclass
class Run:
    mission_id: str
    worker: str
    key: str
    rev: int
    input_hash: str
    bee: str
    status: str = "claimed"          # claimed | blocked | done | cached | failed | cancelled
    outputs: list[str] = field(default_factory=list)
    error: str | None = None
    task_id: str | None = None
    seq: int = 0

    @property
    def finished(self) -> bool:
        return self.status in ("done", "cached", "failed", "cancelled")

    @property
    def succeeded(self) -> bool:
        return self.status in ("done", "cached")


class Runs:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.runs: dict[tuple[str, str, str, int], Run] = {}
        self.by_hash: dict[tuple[str, str], Run] = {}       # (ouvrière, hachage) -> exécution réussie
        ledger.register(self)

    def apply(self, ev: Event) -> None:
        p = ev.payload
        if ev.type == "run.claimed":
            r = Run(ev.mission_id or "", p["worker"], p["key"], int(p["rev"]), p["input_hash"], p.get("bee", ""),
                    task_id=p.get("task_id"), seq=ev.seq)
            self.runs[(r.mission_id, r.worker, r.key, r.rev)] = r
        elif ev.type in ("run.finished", "run.blocked"):
            r = self.runs.get((ev.mission_id or "", p["worker"], p["key"], int(p["rev"])))
            if r is None:
                return
            r.status = p["status"]
            r.outputs = list(p.get("outputs") or r.outputs)
            r.error = p.get("error")
            if r.succeeded:
                self.by_hash.setdefault((r.worker, r.input_hash), r)

    # -- requêtes ---------------------------------------------------------------
    def get(self, mission_id: str, worker: str, key: str, rev: int) -> Run | None:
        return self.runs.get((mission_id, worker, key, rev))

    def of(self, mission_id: str, worker: str, rev: int) -> list[Run]:
        return [r for (m, w, _k, rv), r in self.runs.items() if m == mission_id and w == worker and rv == rev]

    def for_mission(self, mission_id: str, rev: int | None = None) -> list[Run]:
        return [r for (m, _w, _k, rv), r in self.runs.items() if m == mission_id and (rev is None or rv == rev)]

    def cached(self, worker: str, ihash: str) -> Run | None:
        return self.by_hash.get((worker, ihash))

    # -- écritures --------------------------------------------------------------
    def claim(self, mission_id: str, worker: str, key: str, rev: int, ihash: str, bee: str,
              task_id: str | None = None) -> Run:
        """Verrou atomique. Lève AlreadyClaimed si la tâche est déjà prise (ici ou ailleurs)."""
        self.ledger.append("run.claimed", bee or "colonie",
                           {"worker": worker, "key": key, "rev": rev, "input_hash": ihash, "bee": bee,
                            "task_id": task_id},
                           mission_id=mission_id, claim=(mission_id, worker, key, rev))
        return self.runs[(mission_id, worker, key, rev)]

    def finish(self, run: Run, status: str, outputs: list[str] | None = None, error: str | None = None,
               extra: dict[str, Any] | None = None) -> None:
        etype = "run.blocked" if status == "blocked" else "run.finished"
        self.ledger.append(etype, run.bee or "colonie",
                           {"worker": run.worker, "key": run.key, "rev": run.rev, "status": status,
                            "outputs": outputs or [], "error": error, "bee": run.bee, **(extra or {})},
                           mission_id=run.mission_id)


__all__ = ["AlreadyClaimed", "Run", "Runs", "input_hash"]
