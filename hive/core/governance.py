"""Les Gardiennes : gouvernance, sécurité et contrôle humain.

Principes (voir docs/SECURITY.md) :
1. Une politique DÉTERMINISTE décide d'abord (outil, arguments, identité, contamination).
   Un clic d'approbation n'est pas une politique : il ne vient qu'en second rideau.
2. Budget 0 € par défaut : aucun fournisseur payant n'est appelé sans autorisation
   explicite du Conseil humain (plafond relevé via une validation).
3. Données ≠ instructions : tout contenu externe (web, fichiers, images, audio) est
   « contaminé » (taint). Une action dérivée d'un contenu contaminé monte d'un cran de risque.
4. « Règle de deux » : un même agent ne cumule pas entrée non fiable + capacité d'agir
   à l'extérieur sans validation humaine.
5. Capacités atténuables : un sous-agent n'a jamais plus de droits que son parent.
6. Interrupteur d'arrêt : le Conseil peut mettre la ruche en pause à tout moment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .board import Board, Cell
from .ledger import Ledger


class Risk(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

    @property
    def label(self) -> str:
        return {0: "faible", 1: "modéré", 2: "élevé", 3: "critique"}[int(self)]


class BudgetBlocked(Exception):
    """Une dépense dépasserait le plafond autorisé par le Conseil."""


class CapabilityDenied(Exception):
    """Un agent tente d'utiliser un outil hors de ses capacités."""


class ColonyPaused(Exception):
    """L'interrupteur d'arrêt est actif."""


@dataclass
class Policy:
    budget_cap_eur: float = 0.0               # plafond de dépense autorisé (0 € par défaut)
    auto_execute_max: Risk = Risk.LOW         # au-delà : validation humaine obligatoire
    max_steps_per_mission: int = 60           # disjoncteurs (pannes en cascade)
    max_llm_calls_per_mission: int = 80
    max_mission_seconds: float = 3600.0
    approval_ttl_s: float = 7 * 24 * 3600.0
    require_human_for_lessons: bool = True    # anti-empoisonnement de la mémoire


@dataclass
class Assessment:
    risk: Risk
    reasons: list[str] = field(default_factory=list)
    requires_human: bool = False
    allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"risk": int(self.risk), "risk_label": self.risk.label, "reasons": self.reasons,
                "requires_human": self.requires_human, "allowed": self.allowed}


class Budget:
    """Grand livre des dépenses (événementiel). Montants dans la devise du plafond."""

    def __init__(self, ledger: Ledger, policy: Policy):
        self.ledger = ledger
        self.policy = policy
        self.spent = 0.0
        for ev in ledger.events(types=["budget.charged", "budget.cap_changed"]):
            self._apply(ev)

    def _apply(self, ev) -> None:
        if ev.type == "budget.charged":
            self.spent += float(ev.payload["amount"])
        elif ev.type == "budget.cap_changed":
            self.policy.budget_cap_eur = float(ev.payload["cap"])

    @property
    def remaining(self) -> float:
        return max(0.0, self.policy.budget_cap_eur - self.spent)

    def check(self, estimated: float, what: str) -> None:
        if estimated <= 0:
            return
        if self.spent + estimated > self.policy.budget_cap_eur + 1e-12:
            raise BudgetBlocked(
                f"Dépense estimée {estimated:.4f} pour « {what} » refusée : "
                f"plafond {self.policy.budget_cap_eur:.2f}, déjà dépensé {self.spent:.4f}."
            )

    def charge(self, amount: float, what: str, actor: str, mission_id: str | None = None) -> None:
        if amount <= 0:
            return
        ev = self.ledger.append("budget.charged", actor, {"amount": float(amount), "what": what},
                                mission_id=mission_id)
        self._apply(ev)

    def set_cap(self, cap: float, actor: str, reason: str) -> None:
        ev = self.ledger.append("budget.cap_changed", actor, {"cap": float(cap), "reason": reason})
        self._apply(ev)


class Governance:
    def __init__(self, ledger: Ledger, board: Board, policy: Policy | None = None):
        self.ledger = ledger
        self.board = board
        self.policy = policy or Policy()
        self.budget = Budget(ledger, self.policy)
        self.paused = False
        for ev in ledger.events(types=["colony.paused", "colony.resumed"]):
            self.paused = ev.type == "colony.paused"

    # -- interrupteur d'arrêt -------------------------------------------------
    def pause(self, actor: str, reason: str = "") -> None:
        self.paused = True
        self.ledger.append("colony.paused", actor, {"reason": reason})

    def resume(self, actor: str, reason: str = "") -> None:
        self.paused = False
        self.ledger.append("colony.resumed", actor, {"reason": reason})

    def ensure_running(self) -> None:
        if self.paused:
            raise ColonyPaused("La ruche est en pause (interrupteur d'arrêt).")

    # -- capacités ------------------------------------------------------------
    @staticmethod
    def check_capability(agent_tools: set[str], tool: str) -> None:
        if tool not in agent_tools:
            raise CapabilityDenied(f"outil « {tool} » hors des capacités de l'agent")

    @staticmethod
    def attenuate(parent_tools: set[str], requested: set[str]) -> set[str]:
        """Capacités d'un sous-agent = intersection stricte avec celles du parent."""
        return set(parent_tools) & set(requested)

    # -- évaluation du risque d'une action ------------------------------------
    def assess(self, *, tool_risk: Risk, tool_name: str, external: bool, reversible: bool,
               spends_money: bool, tainted: bool, agent_has_untrusted_input: bool,
               allowed_by_config: bool = True) -> Assessment:
        risk = tool_risk
        reasons = [f"risque de base de l'outil {tool_name} : {tool_risk.label}"]
        if not allowed_by_config:
            return Assessment(Risk.CRITICAL, reasons + ["cible non autorisée par la configuration"],
                              requires_human=True, allowed=False)
        if tainted:
            risk = Risk(min(Risk.CRITICAL, risk + 1))
            reasons.append("arguments dérivés d'un contenu non fiable (données ≠ instructions)")
        if not reversible:
            risk = Risk(min(Risk.CRITICAL, risk + 1))
            reasons.append("action irréversible")
        if spends_money:
            risk = Risk.CRITICAL
            reasons.append("engage une dépense")
        requires_human = risk > self.policy.auto_execute_max
        if external and agent_has_untrusted_input:
            requires_human = True
            reasons.append("règle de deux : entrée non fiable + effet externe dans la même tâche")
        if spends_money:
            requires_human = True
        return Assessment(risk, reasons, requires_human=requires_human, allowed=True)

    # -- validations humaines -------------------------------------------------
    def request_approval(self, *, mission_id: str | None, author: str, title: str,
                         action: dict[str, Any], assessment: Assessment,
                         simulation: str, parent_id: str | None, refs: list[str]) -> Cell:
        return self.board.post(
            "approval", author, title, mission_id=mission_id, status="pending",
            parent_id=parent_id, refs=refs, pheromone=3.0,
            data={"action": action, "assessment": assessment.to_dict(), "simulation": simulation},
        )

    def resolve_approval(self, approval_id: str, approved: bool, by: str, comment: str = "") -> Cell:
        cell = self.board.get(approval_id)
        if cell is None or cell.kind != "approval":
            raise KeyError(approval_id)
        if cell.status != "pending":
            raise ValueError(f"validation déjà traitée ({cell.status})")
        return self.board.update(approval_id, by, reason="décision du Conseil",
                                 status="approved" if approved else "rejected",
                                 data={"decided_by": by, "comment": comment})

    def pending_approvals(self) -> list[Cell]:
        return self.board.query(kind="approval", status="pending")
