"""Ouvrières : unités de travail déclenchées par les traces (stigmergie).

Une ouvrière déclare :
  * `needs`    — les traces qui doivent exister (à la révision courante) pour qu'elle démarre :
                 un type de cellule (« script ») ou l'achèvement d'une autre ouvrière (« @montage ») ;
  * `after`    — un ordre souple : attendre tant que ces ouvrières sont exécutables ou en cours ;
  * `produces` — les types de traces qu'elle a le droit de déposer (protocole) ;
  * `keys()`   — ses exécutions indépendantes (une par langue, par scène, par vote…) ;
  * `inputs()` — ce qui détermine son résultat (haché pour l'idempotence et le cache).

Ajouter une capacité à la ruche = ajouter une classe au registre. Rien d'autre ne change :
le moteur la découvre, l'abeille la plus compétente l'enchérit, et elle démarre d'elle-même
dès que ses traces apparaissent sur le Rayon.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .board import Board, Cell

if TYPE_CHECKING:  # pragma: no cover
    from .agent import Agent
    from .colony import Colony


@dataclass
class WorkResult:
    status: str = "done"               # done | blocked | failed
    outputs: list[str] = field(default_factory=list)
    error: str | None = None
    provider: str | None = None
    summary: str = ""


class Ctx:
    """Vue d'une mission à sa révision courante, telle qu'une ouvrière la perçoit."""

    def __init__(self, colony: "Colony", mission: Cell):
        self.colony = colony
        self.board: Board = colony.board
        self.mission = mission
        self.rev: int = int(mission.data.get("rev", 1))
        self.task_id: str | None = None

    @property
    def profile(self) -> str:
        return str(self.mission.data.get("profile", "rapide"))

    def traces(self, kind: str, rev: int | None = None, **filters: Any) -> list[Cell]:
        rev = self.rev if rev is None else rev
        if kind == "mission":
            return [self.mission]
        return [c for c in self.board.query(mission_id=self.mission.id, kind=kind, rev=rev)
                if all(c.data.get(k) == v for k, v in filters.items())]

    def has(self, kind: str) -> bool:
        return bool(self.traces(kind))

    def latest(self, kind: str, **filters: Any) -> Cell | None:
        t = self.traces(kind, **filters)
        return t[-1] if t else None

    def previous(self, kind: str, **filters: Any) -> Cell | None:
        """Dernière trace de ce type dans une révision ANTÉRIEURE (pour réviser un travail)."""
        for rev in range(self.rev - 1, 0, -1):
            t = self.traces(kind, rev=rev, **filters)
            if t:
                return t[-1]
        return None

    def revision_notes(self) -> list[Cell]:
        return [c for c in self.board.query(mission_id=self.mission.id, kind="message")
                if c.data.get("revision_note") and c.rev <= self.rev]

    def done(self, worker: str) -> bool:
        return self.colony.worker_done(self, worker)


class Worker:
    name = "worker"
    label = "Ouvrière"
    skill = "analysis"
    profiles: frozenset[str] = frozenset()
    needs: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    produces: frozenset[str] = frozenset()
    parallel = 1                 # exécutions indépendantes (diversité, votes)
    independent = False          # les exécutions ne voient pas les sorties de leurs pairs
    terminal = False             # son achèvement peut clore la mission
    version = "1"
    cacheable = True             # résultat réutilisable si les entrées sont identiques
    key_in_hash = True           # False : deux clés aux entrées identiques partagent le même résultat
    llm = False                  # utilise un modèle de langage (via une abeille)

    def applies(self, ctx: Ctx) -> bool:
        return ctx.profile in self.profiles

    def needs_for(self, ctx: Ctx) -> tuple[str, ...]:
        return self.needs

    def after_for(self, ctx: Ctx) -> tuple[str, ...]:
        return self.after

    def skill_for(self, ctx: Ctx, key: str) -> str:
        return self.skill

    def keys(self, ctx: Ctx) -> list[str]:
        return [f"vote{i + 1}" for i in range(self.parallel)] if self.parallel > 1 else ["main"]

    def ready(self, ctx: Ctx, key: str) -> bool:
        return True

    def on_replay(self, ctx: Ctx, key: str, data: dict[str, Any]) -> dict[str, Any]:
        """Adapte une trace réutilisée (cache) à sa nouvelle clé."""
        return data

    def inputs(self, ctx: Ctx, key: str) -> Any:
        """Ce qui détermine le résultat. Par défaut : rien de stable => pas de cache utile."""
        return {"mission": ctx.mission.id, "rev": ctx.rev}

    async def run(self, ctx: Ctx, key: str, bee: "Agent | None") -> WorkResult:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "skill": self.skill, "profiles": sorted(self.profiles),
                "needs": list(self.needs), "after": list(self.after), "produces": sorted(self.produces),
                "parallel": self.parallel, "llm": self.llm}


def needs_met(ctx: Ctx, needs: tuple[str, ...]) -> list[str]:
    """Renvoie la liste des besoins NON satisfaits (vide = prête)."""
    missing = []
    for n in needs:
        if n.startswith("@"):
            if not ctx.done(n[1:]):
                missing.append(n)
        elif not ctx.has(n):
            missing.append(n)
    return missing
