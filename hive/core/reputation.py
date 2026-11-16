"""Réputation et calibration des agents (métacognition mesurable).

* Réputation : loi Beta(α, β) par agent et par compétence, mise à jour par des
  résultats VÉRIFIÉS (mesure de mission, retour humain), jamais par l'auto-évaluation.
* Calibration : score de Brier des prévisions probabilisées pré-enregistrées.
  Un agent qui annonce 90 % de confiance et se trompe souvent perd du poids
  dans les enchères et les votes. « Ne confonds jamais confiance et intelligence. »
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import Event, Ledger


@dataclass
class SkillScore:
    alpha: float = 1.0
    beta: float = 1.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def evidence(self) -> float:
        return self.alpha + self.beta - 2.0


@dataclass
class AgentRecord:
    skills: dict[str, SkillScore] = field(default_factory=dict)
    brier_sum: float = 0.0
    brier_n: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0

    @property
    def brier(self) -> float | None:
        return self.brier_sum / self.brier_n if self.brier_n else None

    def calibration_weight(self) -> float:
        """1.0 = parfaitement calibré ; tend vers 0.5 si l'agent fait pire que le hasard."""
        b = self.brier
        if b is None:
            return 1.0
        # Brier d'un agent qui répond toujours 50 % = 0.25. On pénalise au-delà.
        return max(0.5, min(1.0, 1.0 - (b - 0.1)))


class Reputation:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.records: dict[str, AgentRecord] = {}
        for ev in ledger.events(types=["reputation.updated", "calibration.scored", "task.finished"]):
            self._apply(ev)

    def record(self, agent: str) -> AgentRecord:
        return self.records.setdefault(agent, AgentRecord())

    def skill(self, agent: str, skill: str) -> float:
        return self.record(agent).skills.get(skill, SkillScore()).mean

    def weight(self, agent: str, skill: str) -> float:
        rec = self.record(agent)
        return rec.skills.get(skill, SkillScore()).mean * rec.calibration_weight()

    # -- mises à jour (événementielles) --------------------------------------
    def update(self, agent: str, skill: str, score: float, weight: float = 1.0,
               reason: str = "", mission_id: str | None = None) -> None:
        score = max(0.0, min(1.0, float(score)))
        ev = self.ledger.append("reputation.updated", "systeme",
                                {"agent": agent, "skill": skill, "score": score,
                                 "weight": float(weight), "reason": reason}, mission_id=mission_id)
        self._apply(ev)

    def score_forecast(self, agent: str, probability: float, outcome: bool,
                       forecast_id: str, mission_id: str | None = None) -> float:
        brier = (float(probability) - (1.0 if outcome else 0.0)) ** 2
        ev = self.ledger.append("calibration.scored", "systeme",
                                {"agent": agent, "forecast_id": forecast_id,
                                 "probability": float(probability), "outcome": bool(outcome),
                                 "brier": brier}, mission_id=mission_id)
        self._apply(ev)
        return brier

    def _apply(self, ev: Event) -> None:
        p = ev.payload
        if ev.type == "reputation.updated":
            s = self.record(p["agent"]).skills.setdefault(p["skill"], SkillScore())
            s.alpha += p["score"] * p["weight"]
            s.beta += (1.0 - p["score"]) * p["weight"]
        elif ev.type == "calibration.scored":
            rec = self.record(p["agent"])
            rec.brier_sum += p["brier"]
            rec.brier_n += 1
        elif ev.type == "task.finished":
            rec = self.record(p["agent"])
            if p.get("ok"):
                rec.tasks_done += 1
            else:
                rec.tasks_failed += 1

    def snapshot(self) -> dict[str, dict]:
        return {
            a: {
                "skills": {k: round(v.mean, 3) for k, v in r.skills.items()},
                "brier": None if r.brier is None else round(r.brier, 3),
                "calibration_weight": round(r.calibration_weight(), 3),
                "tasks_done": r.tasks_done,
                "tasks_failed": r.tasks_failed,
            }
            for a, r in sorted(self.records.items())
        }
