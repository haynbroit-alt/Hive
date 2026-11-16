"""Le Rêve : consolidation hors-ligne (auto-audit, mémoire, anticipation).

Inspiré du « sleep-time compute » (Lin et al. 2025) et de la réflexion des Generative
Agents, mais SOUS CONTRÔLE : le rêve ne modifie jamais la mémoire active de lui-même.
Il PROPOSE (leçons, alertes) et le Conseil valide — défense contre l'empoisonnement
de la mémoire (OWASP Agentic ASI06).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .board import Cell
from .colony import Colony


class Dreamer:
    def __init__(self, colony: Colony, stale_after_s: float = 30 * 24 * 3600):
        self.c = colony
        self.stale_after_s = stale_after_s

    def dream(self) -> Cell:
        c = self.c
        now = c.clock()
        findings: dict[str, Any] = {}

        ok, bad = c.ledger.verify()
        findings["integrite_registre"] = "intact" if ok else f"CORROMPUE à l'événement {bad}"

        beliefs = c.board.query(kind="belief")
        unsupported = [b.id for b in beliefs if b.status == "fact" and not b.refs]
        stale = [b.id for b in beliefs if b.status == "fact" and now - b.updated_at > self.stale_after_s]
        by_subject: dict[str, list[Cell]] = defaultdict(list)
        for b in beliefs:
            subj = str(b.data.get("subject") or "").strip().lower()
            if subj and not b.data.get("revised_by"):
                by_subject[subj].append(b)
        conflicts = {s: [b.id for b in bs] for s, bs in by_subject.items()
                     if len({" ".join(b.content.lower().split()) for b in bs}) > 1 and len(bs) > 1}
        findings["croyances"] = {"total": len(beliefs), "faits_sans_preuve": unsupported,
                                 "faits_anciens_a_reverifier": stale, "sujets_en_conflit": conflicts}

        open_forecasts = [f for f in c.board.query(kind="forecast") if not f.data.get("resolved")]
        findings["previsions_a_resoudre"] = [{"id": f.id, "p": f.data.get("probability"),
                                              "resolution": f.data.get("resolution")} for f in open_forecasts[:10]]

        violations = Counter(e.payload.get("agent") for e in c.ledger.events(types=["protocol.violation"]))
        failures = Counter(e.payload.get("agent") for e in c.ledger.events(types=["task.finished"])
                           if not e.payload.get("ok"))
        findings["sante_des_abeilles"] = {
            "violations_protocole": dict(violations), "taches_echouees": dict(failures),
            "reputation": c.reputation.snapshot()}

        missions = c.board.query(kind="mission")
        scored = [m for m in missions if isinstance(m.data.get("score"), (int, float))]
        by_profile: dict[str, list[float]] = defaultdict(list)
        for m in scored:
            by_profile[m.data.get("profile", "?")].append(float(m.data["score"]))
        findings["performance_par_profil"] = {k: round(sum(v) / len(v), 3) for k, v in by_profile.items()}
        findings["missions_arretees"] = [{"id": m.id, "raison": m.data.get("end_reason")}
                                         for m in missions if m.status == "stopped"]

        findings["budget"] = {"plafond": c.gov.policy.budget_cap_eur, "depense": round(c.gov.budget.spent, 4),
                              "appels_bloques": len(c.ledger.events(types=["budget.blocked"]))}

        # Propositions (jamais auto-activées)
        proposals: list[str] = []
        for agent, n in violations.items():
            if n >= 3:
                proposals.append(f"L'abeille {agent} viole souvent le protocole ({n} fois) : revoir son rôle "
                                 f"ou son modèle.")
        if unsupported:
            proposals.append(f"{len(unsupported)} « faits » sans preuve : les rétrograder en hypothèses.")
        if conflicts:
            proposals.append(f"{len(conflicts)} sujet(s) avec croyances contradictoires : lancer une mission "
                             f"de réconciliation.")
        if open_forecasts:
            proposals.append(f"{len(open_forecasts)} prévision(s) en attente : les résoudre améliore la "
                             f"calibration des abeilles.")
        for profile, avg in findings["performance_par_profil"].items():
            if avg < 0.5:
                proposals.append(f"Le profil « {profile} » obtient {avg:.0%} en moyenne : à améliorer.")
        findings["propositions"] = proposals

        lines = ["Rêve de la ruche — audit et consolidation", f"Registre : {findings['integrite_registre']}",
                 f"Croyances : {len(beliefs)} ; sans preuve : {len(unsupported)} ; conflits : {len(conflicts)}",
                 f"Prévisions à résoudre : {len(open_forecasts)}",
                 f"Budget : {findings['budget']['depense']} / {findings['budget']['plafond']} €"]
        lines += [f"→ {p}" for p in proposals] or ["→ Rien à signaler."]
        return c.board.post("dream", "memoire" if "memoire" in c.agents else "colonie", "\n".join(lines),
                            status="fact", confidence=1.0, data=findings, pheromone=0.5)
