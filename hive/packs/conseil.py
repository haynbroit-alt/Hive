"""Pack « Conseil » : le cycle cognitif du Conseil, en ouvrières déclenchées par les traces.

OBSERVER → COMPRENDRE → CONFRONTER → (HYPOTHÈSES si contradiction) → ANTICIPER → PRIORISER →
DÉCIDER (3 votes indépendants) → QUORUM → CONTRÔLE DES RISQUES → ARBITRAGE (veto → Conseil) →
SIMULER → EXÉCUTER (ou validation humaine) → SYNTHÈSE → MESURER → APPRENDRE → RÉVISER → BILAN.

Aucune séquence n'est codée en dur : chaque ouvrière démarre quand ses traces existent.
Les étapes conditionnelles émergent d'elles-mêmes (sans contradiction, pas d'hypothèses).
"""
from __future__ import annotations

from typing import Any

from ..core.agent import Agent
from ..core.cycle import PHASES
from ..core.governance import Assessment, Risk
from ..core.workers import Ctx, Worker, WorkResult

CONSEIL = frozenset({"rapide", "analyse", "action"})
DEEP = frozenset({"analyse", "action"})
HELPERS = ("entraide", "verification")


class PhaseWorker(Worker):
    """Ouvrière « cognitive » : une abeille (LLM) traite une phase du cycle."""
    llm = True

    def __init__(self, key: str, *, profiles: frozenset[str], needs: tuple[str, ...] = (),
                 after: tuple[str, ...] = HELPERS, parallel: int = 1, independent: bool = False,
                 needs_by_profile: dict[str, tuple[str, ...]] | None = None, label: str | None = None):
        self.phase = PHASES[key]
        self.name = key
        self.label = label or self.phase.label.capitalize()
        self.skill = self.phase.skill
        self.profiles = profiles
        self.needs = needs
        self.after = tuple(a for a in after if a != key)
        self.produces = self.phase.outputs
        self.parallel = parallel
        self.independent = independent
        self.needs_by_profile = needs_by_profile or {}

    def needs_for(self, ctx: Ctx) -> tuple[str, ...]:
        return self.needs_by_profile.get(ctx.profile, self.needs)

    def focus(self, ctx: Ctx, key: str) -> list[str] | None:
        return None

    def inputs(self, ctx: Ctx, key: str) -> Any:
        """Contenu (et non identifiants) du contexte : une révision qui ne change rien réutilise le travail."""
        view = ctx.colony.context_for(ctx, self, self.focus(ctx, key))
        return {"phase": self.name, "mission_id": ctx.mission.id, "mission": ctx.mission.content,
                "lessons": ctx.colony.lessons(), "models": ctx.colony.agents[next(iter(ctx.colony.agents))].spec.models,
                "context": [(c["kind"], c["status"], c["content"], c["trusted"]) for c in view]}

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        colony = ctx.colony
        if bee is None:
            return WorkResult("failed", error="aucune abeille")
        if self.name == "observe":
            await colony.perceive_artifacts(ctx, bee.name)
        view = colony.context_for(ctx, self, self.focus(ctx, key))
        task = ctx.board.get(ctx.task_id) if ctx.task_id else ctx.mission
        out = await bee.perform(phase=self.phase, mission=ctx.mission, task=task, ctx=view,
                                lessons=colony.lessons(), tool_specs=colony.tools.specs(bee.spec.tools))
        tainted = any(c["taint"] for c in view)
        created = colony.apply_output(ctx, self, bee, out.parsed, tainted, ctx.task_id,
                                      allow_actions=self.name == "simulate")
        self.after_run(ctx, key, created)
        return WorkResult("done", created, provider=out.provider, summary=str(out.parsed.get("summary", ""))[:300])

    def after_run(self, ctx: Ctx, key: str, created: list[str]) -> None:
        pass


class HelpWorker(PhaseWorker):
    """Entraide : se déclenche dès qu'une abeille dépose une demande d'aide sur le Rayon."""

    def __init__(self) -> None:
        super().__init__("entraide", profiles=CONSEIL | {"studio"}, needs=("need",), after=())

    def _needs(self, ctx: Ctx) -> list:
        return [c for c in ctx.traces("need") if c.status == "open" and not c.data.get("for_human")
                and c.data.get("requested_by")]

    def keys(self, ctx: Ctx) -> list[str]:
        return [c.id for c in self._needs(ctx)]

    def skill_for(self, ctx: Ctx, key: str) -> str:
        c = ctx.board.get(key)
        return str((c.data.get("skill") if c else None) or "analysis")

    def exclude_bees(self, ctx: Ctx, key: str) -> list[str]:
        c = ctx.board.get(key)
        return [c.data.get("requested_by")] if c else []

    def focus(self, ctx: Ctx, key: str) -> list[str] | None:
        return [key]

    def after_run(self, ctx: Ctx, key: str, created: list[str]) -> None:
        ctx.board.update(key, "colonie", reason="aide apportée", status="done", data={"answers": created})


class VerifyWorker(PhaseWorker):
    """Vérification : une découverte annoncée (« danse ») recrute une vérificatrice."""

    def __init__(self) -> None:
        super().__init__("verification", profiles=CONSEIL, needs=("discovery",), after=())

    def keys(self, ctx: Ctx) -> list[str]:
        return [c.id for c in ctx.traces("discovery")]

    def exclude_bees(self, ctx: Ctx, key: str) -> list[str]:
        c = ctx.board.get(key)
        return [c.author] if c else []

    def focus(self, ctx: Ctx, key: str) -> list[str] | None:
        return [key]


class ObserveWorker(PhaseWorker):
    def __init__(self) -> None:
        super().__init__("observe", profiles=CONSEIL, needs=("mission",), after=())

    def applies(self, ctx: Ctx) -> bool:
        return ctx.profile in DEEP or (ctx.profile == "rapide" and bool(ctx.mission.data.get("attachments")))


class SimulateWorker(PhaseWorker):
    cacheable = False  # une action se re-planifie et se re-valide à chaque révision

    def __init__(self) -> None:
        super().__init__("simulate", profiles=frozenset({"action"}), needs=("clearance",))

    def ready(self, ctx: Ctx, key: str) -> bool:
        c = ctx.latest("clearance")
        return c is not None and c.status == "granted"


# ---------------------------------------------------------------------- ouvrières mécaniques
class QuorumWorker(Worker):
    """Agrège les propositions indépendantes : vote pondéré par la réputation, quorum."""
    name, label, skill = "quorum", "Quorum", "decision"
    profiles = DEEP
    needs = ("@decide",)
    produces = frozenset({"decision"})
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        colony, board = ctx.colony, ctx.board
        proposals = [c for c in ctx.traces("proposal") if c.data.get("option_key")]
        if not proposals:
            c = board.post("decision", "quorum", "Aucune proposition valide : pas de décision.",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="unknown", confidence=0.0, phase=self.name)
            return WorkResult("done", [c.id])
        tally: dict[str, float] = {}
        for p in proposals:
            w = colony.reputation.weight(p.author, "decision") * (p.confidence or 0.5)
            tally[str(p.data["option_key"])] = tally.get(str(p.data["option_key"]), 0.0) + w
        total = sum(tally.values()) or 1.0
        best = max(sorted(tally), key=lambda k: tally[k])
        share = tally[best] / total
        reached = len(proposals) >= 2 and share > colony.quorum  # un seul votant n'est pas un quorum
        option = next((o for o in ctx.traces("option") if o.data.get("option_key") == best), None)
        supporters = [p.author for p in proposals if p.data["option_key"] == best]
        conf = share * (sum(p.confidence or 0.5 for p in proposals) / len(proposals))
        d = board.post(
            "decision", "quorum",
            f"Option retenue : {option.content if option else best} — soutien {share:.0%} ({', '.join(supporters)})"
            + ("" if reached else " — QUORUM NON ATTEINT"),
            mission_id=ctx.mission.id, rev=ctx.rev, status="hypothesis", confidence=round(conf, 3), phase=self.name,
            refs=[p.id for p in proposals] + ([option.id] if option else []),
            taint=board.tainted(p.id for p in proposals),
            data={"option_key": best, "tally": {k: round(v, 3) for k, v in tally.items()}, "share": round(share, 3),
                  "quorum_reached": reached, "voters": len(proposals)})
        return WorkResult("done", [d.id], summary=f"{best} ({share:.0%})")


class ArbitrageWorker(Worker):
    """Après le contrôle des risques : veto ou absence de quorum (en profil action) → le Conseil tranche."""
    name, label, skill = "arbitrage", "Arbitrage", "risk"
    profiles = DEEP
    needs = ("@risk_review",)
    produces = frozenset({"clearance", "approval"})
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        colony = ctx.colony
        veto = next((c for c in ctx.traces("risk") if c.phase == "risk_review" and c.data.get("veto") is True), None)
        decision = ctx.latest("decision")
        no_quorum = decision is not None and not decision.data.get("quorum_reached") and ctx.profile == "action"
        if veto is not None:
            ap = colony.ask_human(ctx, author=veto.author, title=f"Veto de {veto.author} : passer outre et poursuivre ?",
                                  action={"type": "veto", "risk_id": veto.id},
                                  assessment=Assessment(Risk.HIGH, ["veto du contrôle des risques"], True),
                                  simulation=veto.content, parent_id=veto.id, refs=[veto.id])
            return WorkResult("blocked", [ap.id], summary="veto : arbitrage humain")
        if no_quorum:
            ap = colony.ask_human(ctx, author="quorum",
                                  title=f"Décision sans quorum ({decision.data.get('share', 0):.0%}) : "
                                        f"poursuivre avec « {decision.data.get('option_key')} » ?",
                                  action={"type": "no_quorum", "decision_id": decision.id},
                                  assessment=Assessment(Risk.HIGH, ["désaccord entre abeilles"], True),
                                  simulation=decision.content, parent_id=decision.id, refs=[decision.id])
            return WorkResult("blocked", [ap.id], summary="pas de quorum : arbitrage humain")
        c = ctx.board.post("clearance", "gardiennes", "Aucune objection bloquante : la suite est autorisée.",
                           mission_id=ctx.mission.id, rev=ctx.rev, status="granted", confidence=1.0, phase=self.name,
                           refs=[decision.id] if decision else [])
        return WorkResult("done", [c.id])


class ExecuteWorker(Worker):
    """Joue les actions simulées : exécution si risque faible, sinon validation humaine."""
    name, label, skill = "execute", "Exécution", "planning"
    profiles = frozenset({"action"})
    needs = ("@simulate",)
    produces = frozenset({"result", "approval"})
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        colony, board = ctx.colony, ctx.board
        outputs, waiting = [], False
        for sim in colony.planned_actions(ctx):
            if sim.data.get("exec_status") != "planned":
                continue
            a = sim.data.get("assessment") or {}
            if not a.get("allowed", True):
                board.update(sim.id, "gardiennes", reason="politique", data={"exec_status": "blocked"})
                r = board.post("result", "gardiennes", f"Action bloquée par la politique : {sim.content}",
                               mission_id=ctx.mission.id, rev=ctx.rev, refs=[sim.id], status="fact", confidence=1.0)
                outputs.append(r.id)
            elif a.get("requires_human"):
                ap = colony.ask_human(
                    ctx, author="gardiennes",
                    title=f"Autoriser : {sim.data['action']['tool']} ? (risque {a.get('risk_label')})",
                    action={"type": "tool", "simulation_id": sim.id, **sim.data["action"]},
                    assessment=Assessment(Risk(int(a.get("risk", 2))), list(a.get("reasons", [])), True,
                                          bool(a.get("allowed", True))),
                    simulation=sim.content, parent_id=sim.id, refs=[sim.id])
                board.update(sim.id, "gardiennes", reason="validation humaine requise",
                             data={"exec_status": "awaiting_approval", "approval_id": ap.id})
                outputs.append(ap.id)
                waiting = True
            else:
                board.update(sim.id, "colonie", reason="exécution automatique (risque faible)",
                             data={"exec_status": "executing"})
                await colony.execute_action(ctx.mission.id, sim.id, approved_by=None)
        return WorkResult("blocked" if waiting else "done", outputs)


class BilanWorker(Worker):
    """Clôt la mission : score (auto-évaluation + contrôles objectifs) et réputation."""
    name, label, skill = "bilan", "Bilan", "audit"
    profiles = CONSEIL
    needs = ("@measure",)
    after = ("learn", "revise", "entraide", "verification")
    produces = frozenset()
    terminal = True
    cacheable = False

    async def run(self, ctx: Ctx, key: str, bee: Agent | None) -> WorkResult:
        colony, board = ctx.colony, ctx.board
        auditor = [float(m.data["score"]) for m in ctx.traces("measurement")
                   if isinstance(m.data.get("score"), (int, float))]
        report = ctx.traces("report")
        violations = len(colony.ledger.events(mission_id=ctx.mission.id, types=["protocol.violation"]))
        objective = 0.4 * bool(report) + 0.3 * any(r.refs for r in report) + 0.3 * any(
            c.status == "unknown" for c in board.query(mission_id=ctx.mission.id, rev=ctx.rev))
        objective = max(0.0, objective - 0.05 * violations)
        score = round((sum(auditor) / len(auditor) + objective) / 2 if auditor else objective, 3)
        board.update(ctx.mission.id, "colonie", reason="mesure",
                     data={"score": score, "violations": violations, "score_parts": {
                         "auto_evaluation": round(sum(auditor) / len(auditor), 3) if auditor else None,
                         "controles_objectifs": round(objective, 3)}})
        for t in board.query(kind="task", mission_id=ctx.mission.id, status="done", rev=ctx.rev):
            w = colony.workers.get(t.phase or "")
            if t.claimed_by in colony.agents and t.skills and w is not None and w.llm:
                colony.reputation.update(t.claimed_by, t.skills[0], score, weight=0.5, reason="mesure de mission",
                                         mission_id=ctx.mission.id)
        return WorkResult("done", [], summary=f"score {score}")


def conseil_workers() -> list[Worker]:
    return [
        ObserveWorker(),
        PhaseWorker("understand", profiles=CONSEIL, needs=("@observe",)),
        PhaseWorker("confront", profiles=DEEP, needs=("@understand",)),
        PhaseWorker("hypothesize", profiles=DEEP, needs=("@confront", "contradiction"), parallel=2, independent=True),
        PhaseWorker("anticipate", profiles=DEEP, needs=("@confront",), after=("hypothesize", *HELPERS)),
        PhaseWorker("prioritize", profiles=DEEP, needs=("@anticipate",)),
        PhaseWorker("decide", profiles=DEEP, needs=("@prioritize",), parallel=3, independent=True),
        QuorumWorker(),
        PhaseWorker("risk_review", profiles=DEEP, needs=("decision",)),
        ArbitrageWorker(),
        SimulateWorker(),
        ExecuteWorker(),
        PhaseWorker("report", profiles=CONSEIL, needs=("@arbitrage",), needs_by_profile={"rapide": ("@understand",)},
                    after=("simulate", "execute", *HELPERS)),
        PhaseWorker("measure", profiles=CONSEIL, needs=("report",)),
        PhaseWorker("learn", profiles=DEEP, needs=("@measure",)),
        PhaseWorker("revise", profiles=DEEP, needs=("@learn",)),
        BilanWorker(),
        HelpWorker(),
        VerifyWorker(),
    ]
