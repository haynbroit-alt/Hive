"""Cerveau hors-ligne, déterministe et honnête.

Il ne simule PAS l'intelligence : il fait circuler le protocole (pour tests, démo et
secours quand aucun modèle n'est disponible) en n'affirmant que ce qui est vérifiable
sur le Rayon (contenu de la mission, pièces jointes, cellules existantes). Tout le reste
est déclaré « unknown » ou « hypothesis » avec une confiance basse, et chaque sortie
est préfixée [hors-ligne] pour que personne ne la confonde avec une analyse réelle.
"""
from __future__ import annotations

import json
from typing import Any

TAG = "[hors-ligne]"


def _first_sentence(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _ids(ctx: list[dict], kind: str | None = None, status: str | None = None) -> list[str]:
    return [c["id"] for c in ctx if (kind is None or c["kind"] == kind)
            and (status is None or c.get("status") == status)]


def offline_answer(req: Any) -> str:
    meta: dict[str, Any] = req.meta or {}
    phase = meta.get("phase", "")
    mission = meta.get("mission", "")
    mission_id = meta.get("mission_id", "")
    task = meta.get("task", "")
    ctx: list[dict] = meta.get("context", [])
    agent = meta.get("agent", "")
    out: dict[str, Any] = {"summary": f"{TAG} {phase} par {agent}", "cells": [], "needs": [], "actions": []}
    cells = out["cells"]
    m = _first_sentence(mission)

    if meta.get("purpose") == "describe_media":
        out = {"description": f"{TAG} Aucun modèle de vision disponible : contenu non décrit."}
        return json.dumps(out, ensure_ascii=False)

    if phase == "observe":
        artifacts = [c for c in ctx if c["kind"] == "artifact"]
        cells.append({"kind": "observation", "status": "fact", "confidence": 1.0, "refs": [mission_id],
                      "content": f"{TAG} Mission reçue du Conseil : « {m} »."})
        for a in artifacts:
            cells.append({"kind": "observation", "status": "fact", "confidence": 1.0, "refs": [a["id"]],
                          "content": f"{TAG} Pièce jointe disponible : {_first_sentence(a['content'], 120)}"})
        cells.append({"kind": "observation", "status": "unknown", "confidence": 0.3, "refs": [],
                      "content": f"{TAG} Aucune source externe consultée : le contexte réel reste à établir."})
    elif phase == "understand":
        cells.append({"kind": "belief", "status": "fact", "confidence": 0.95, "refs": [mission_id],
                      "data": {"subject": "objectif"},
                      "content": f"{TAG} L'objectif exprimé par le Conseil est : « {m} »."})
        cells.append({"kind": "belief", "status": "unknown", "confidence": 0.2, "refs": [],
                      "data": {"subject": "contexte"},
                      "content": f"{TAG} Sans modèle de langage, le sens profond et le contexte de la "
                                 f"demande ne peuvent pas être analysés."})
    elif phase == "confront":
        lowered = mission.lower()
        if any(w in lowered for w in (" mais ", "pourtant", "contradiction", "alors que", "cependant")):
            cells.append({"kind": "contradiction", "status": "hypothesis", "confidence": 0.4,
                          "refs": [mission_id],
                          "content": f"{TAG} La formulation de la mission contient une tension "
                                     f"(« mais / pourtant / alors que ») qui mérite examen."})
        else:
            cells.append({"kind": "confirmation", "status": "hypothesis", "confidence": 0.3,
                          "refs": _ids(ctx, "belief")[:2],
                          "content": f"{TAG} Aucune contradiction détectable sans analyse sémantique."})
    elif phase == "hypothesize":
        refs = _ids(ctx, "contradiction")[:1]
        cells.append({"kind": "hypothesis", "status": "hypothesis", "confidence": 0.3, "refs": refs,
                      "content": f"{TAG} Hypothèse ({agent}) : la tension vient d'un objectif et d'une "
                                 f"contrainte non encore arbitrés. Test : demander au Conseil la priorité."})
    elif phase == "anticipate":
        cells.append({"kind": "forecast", "status": "hypothesis", "confidence": 0.5, "refs": [mission_id],
                      "data": {"probability": 0.5,
                               "resolution": "Le Conseil juge le livrable utile (retour 👍) sous 7 jours."},
                      "content": f"{TAG} Prévision non informée : 50 % que le livrable soit jugé utile."})
        cells.append({"kind": "risk", "status": "hypothesis", "confidence": 0.6, "refs": [],
                      "data": {"level": "modéré"},
                      "content": f"{TAG} Risque principal : décider sans données externes ni modèle."})
    elif phase == "prioritize":
        cells.append({"kind": "option", "status": "hypothesis", "confidence": 0.5, "refs": [mission_id],
                      "data": {"option_key": "approfondir", "impact": 0.6, "roi": 0.6, "urgency": 0.4,
                               "effort": 0.4},
                      "content": f"{TAG} Option A — approfondir : collecter des données avant de trancher."})
        cells.append({"kind": "option", "status": "hypothesis", "confidence": 0.4, "refs": [mission_id],
                      "data": {"option_key": "agir", "impact": 0.7, "roi": 0.5, "urgency": 0.6,
                               "effort": 0.6},
                      "content": f"{TAG} Option B — agir maintenant avec l'information disponible."})
    elif phase == "decide":
        options = [c for c in ctx if c["kind"] == "option"]
        # Règle prudente : sans modèle, préférer l'option au meilleur rapport impact/effort.
        def score(c: dict) -> float:
            d = c.get("data") or {}
            return float(d.get("impact", 0.5)) * float(d.get("roi", 0.5)) / max(0.1, float(d.get("effort", 0.5)))
        best = max(options, key=score) if options else None
        key = (best.get("data") or {}).get("option_key", "approfondir") if best else "approfondir"
        cells.append({"kind": "proposal", "status": "hypothesis", "confidence": 0.5,
                      "refs": [best["id"]] if best else [], "data": {"option_key": key},
                      "content": f"{TAG} {agent} propose « {key} » (meilleur rapport impact×ROI/effort)."})
    elif phase == "risk_review":
        decision = next((c for c in ctx if c["kind"] == "decision"), None)
        veto = "veto" in mission.lower()  # crochet de test explicite, documenté
        cells.append({"kind": "risk", "status": "hypothesis", "confidence": 0.5,
                      "refs": [decision["id"]] if decision else [],
                      "data": {"level": "élevé" if veto else "faible", "veto": veto},
                      "content": f"{TAG} Contrôle des risques : "
                                 + ("opposition — validation humaine requise." if veto
                                    else "pas d'objection bloquante identifiée.")})
    elif phase == "simulate":
        cells.append({"kind": "simulation", "status": "hypothesis", "confidence": 0.6, "refs": [mission_id],
                      "content": f"{TAG} Plan : consigner la décision dans l'espace de travail."})
        out["actions"].append({"tool": "workspace.write",
                               "args": {"path": f"missions/{mission_id}.md",
                                        "content": f"# Mission {mission_id}\n\n{mission}\n\n"
                                                   f"(note rédigée {TAG})\n"},
                               "reason": "Garder une trace écrite de la décision."})
    elif phase == "report":
        facts = [c for c in ctx if c.get("status") == "fact"]
        hyps = [c for c in ctx if c.get("status") == "hypothesis" and c["kind"] != "proposal"]
        unknowns = [c for c in ctx if c.get("status") == "unknown"]
        decision = next((c for c in ctx if c["kind"] == "decision"), None)
        lines = [f"{TAG} Synthèse mécanique (aucun modèle de langage n'a analysé la mission).",
                 f"Mission : « {m} »"]
        if decision:
            lines.append(f"Décision de la colonie : {decision['content']}")
        lines.append(f"Faits ({len(facts)}) : " + "; ".join(_first_sentence(c['content'], 90) for c in facts[:4]))
        if hyps:
            lines.append(f"Hypothèses ({len(hyps)}) : " + "; ".join(_first_sentence(c['content'], 90) for c in hyps[:4]))
        lines.append(f"Inconnues ({len(unknowns)}) : " + "; ".join(_first_sentence(c['content'], 90) for c in unknowns[:4]))
        lines.append("Prochaine étape : brancher un modèle (Ollama local) pour une analyse réelle.")
        cells.append({"kind": "report", "status": "hypothesis", "confidence": 0.3,
                      "refs": [c["id"] for c in (facts + unknowns)[:6]],
                      "content": "\n".join(lines)})
    elif phase == "measure":
        report = next((c for c in ctx if c["kind"] == "report"), None)
        criteria = {
            "un livrable existe": report is not None,
            "les inconnues sont déclarées": any(c.get("status") == "unknown" for c in ctx),
            "une analyse sémantique réelle a eu lieu": False,
        }
        score = sum(criteria.values()) / len(criteria)
        cells.append({"kind": "measurement", "status": "fact", "confidence": 0.9,
                      "refs": [report["id"]] if report else [],
                      "data": {"score": round(score, 3),
                               "criteria": [{"criterion": k, "met": v} for k, v in criteria.items()]},
                      "content": f"{TAG} Score {score:.0%} : livrable présent, honnête, mais non analysé."})
    elif phase == "learn":
        cells.append({"kind": "lesson", "status": "hypothesis", "confidence": 0.5, "refs": [mission_id],
                      "data": {"type": "rule", "applies_to": "toutes missions"},
                      "content": f"{TAG} Règle candidate : sans modèle de langage disponible, limiter la "
                                 f"mission au profil « rapide » et le signaler au Conseil."})
    elif phase == "revise":
        unknown = next((c for c in ctx if c["kind"] == "belief" and c.get("status") == "unknown"), None)
        if unknown:
            cells.append({"kind": "belief", "status": "unknown", "confidence": 0.2, "refs": [unknown["id"]],
                          "data": {"revises": unknown["id"], "subject": (unknown.get("data") or {}).get("subject")},
                          "content": f"{TAG} Révision : aucune preuve nouvelle, la croyance reste inconnue."})
    elif phase == "help":
        cells.append({"kind": "observation", "status": "unknown", "confidence": 0.2, "refs": [],
                      "content": f"{TAG} Demande d'aide reçue (« {_first_sentence(task, 100)} ») : "
                                 f"impossible d'y répondre sans modèle."})
    else:
        cells.append({"kind": "observation", "status": "unknown", "confidence": 0.1, "refs": [],
                      "content": f"{TAG} Phase « {phase} » non prise en charge hors-ligne."})
    return json.dumps(out, ensure_ascii=False)

