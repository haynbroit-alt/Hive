"""Le cycle cognitif de la ruche (fidèle au schéma du Conseil).

OBSERVER → COMPRENDRE → CONFRONTER → (HYPOTHÈSES si contradiction) → ANTICIPER →
PRIORISER → DÉCIDER (propositions indépendantes + vote + Gardienne des risques) →
SIMULER → EXÉCUTER (risque faible) / VALIDATION HUMAINE (risque élevé) → SYNTHÈSE →
MESURER → APPRENDRE → RÉVISER ; le RÊVE (consolidation) tourne au niveau de la colonie.

Leçon tirée de la recherche (Kim et al. 2025, voir docs/RESEARCH.md) : le multi-agents
dégrade les tâches séquentielles simples et n'aide que sur le travail parallélisable.
D'où les PROFILS : une question simple ne traverse pas 13 phases.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    key: str
    label: str
    skill: str
    outputs: frozenset[str]
    instruction: str
    parallel: int = 1            # nombre de tâches indépendantes (diversité / vote)
    independent: bool = False    # les agents ne voient pas les sorties des pairs de la phase


PHASES: dict[str, Phase] = {p.key: p for p in [
    Phase("observe", "OBSERVER", "observation", frozenset({"observation", "need", "discovery"}),
          "Relève les faits disponibles (mission, pièces jointes, sources). Un fait doit citer sa "
          "source dans refs. Signale ce qui manque."),
    Phase("understand", "COMPRENDRE", "analysis", frozenset({"belief", "need", "discovery"}),
          "Construis le modèle du monde : croyances clés avec statut fact/hypothesis/unknown, "
          "confiance calibrée et références. Nomme explicitement les inconnues."),
    Phase("confront", "CONFRONTER", "critique", frozenset({"contradiction", "confirmation", "need"}),
          "Confronte les observations aux croyances. Cherche activement erreurs, contradictions, "
          "biais et faiblesses. Une confirmation doit être justifiée."),
    Phase("hypothesize", "HYPOTHÈSES", "strategy", frozenset({"hypothesis", "need"}),
          "Pour chaque contradiction, propose des explications candidates testables "
          "(« Pourquoi ? »), avec ce qui permettrait de les départager.", parallel=2, independent=True),
    Phase("anticipate", "ANTICIPER", "strategy", frozenset({"forecast", "risk", "opportunity", "need"}),
          "Formule des prévisions PROBABILISÉES et vérifiables (data.probability entre 0 et 1, "
          "data.resolution = comment savoir si c'est vrai), des risques et des opportunités."),
    Phase("prioritize", "PRIORISER", "strategy", frozenset({"option"}),
          "Propose 2 à 4 options d'action. Pour chacune : data.impact, data.roi, data.urgency, "
          "data.effort (0-1) et data.option_key court et unique."),
    Phase("decide", "DÉCIDER", "decision", frozenset({"proposal"}),
          "INDÉPENDAMMENT des autres, choisis UNE option (data.option_key) et justifie. "
          "Donne ta confiance. Ne cherche pas le consensus : ton avis propre compte.",
          parallel=3, independent=True),
    Phase("risk_review", "CONTRÔLE DES RISQUES", "risk", frozenset({"risk"}),
          "Évalue la décision retenue. data.level (faible/modéré/élevé/critique) et data.veto "
          "(true seulement si la décision est dangereuse, illégale, coûteuse ou irréversible "
          "sans justification suffisante)."),
    Phase("simulate", "SIMULER", "planning", frozenset({"simulation", "need"}),
          "Planifie les actions concrètes nécessaires (champ actions : outil + arguments). "
          "Elles seront jouées en bac à sable avant toute exécution réelle."),
    Phase("report", "SYNTHÈSE", "synthesis", frozenset({"report"}),
          "Rédige le livrable final pour le Conseil : réponse, décision, faits / hypothèses / "
          "inconnues séparés, risques, prochaines étapes, niveau de confiance global."),
    Phase("measure", "MESURER", "audit", frozenset({"measurement"}),
          "Compare le livrable aux critères d'acceptation. data.score (0-1), data.criteria "
          "(liste critère → atteint/non atteint). Sois sévère et factuel."),
    Phase("learn", "APPRENDRE", "memory", frozenset({"lesson"}),
          "Propose au plus 2 leçons réutilisables (data.type = skill ou rule, data.applies_to). "
          "Elles ne seront actives qu'après validation du Conseil."),
    Phase("entraide", "ENTRAIDE", "analysis",
          frozenset({"observation", "belief", "hypothesis", "discovery", "confirmation", "contradiction"}),
          "Réponds à la demande d'aide d'une autre abeille (cellule ciblée), avec preuves et confiance calibrée."),
    Phase("verification", "VÉRIFICATION", "critique", frozenset({"confirmation", "contradiction"}),
          "Vérifie la découverte annoncée (cellule ciblée) : confirme avec preuves ou réfute."),
    Phase("revise", "RÉVISER", "analysis", frozenset({"belief"}),
          "Révise le modèle du monde à la lumière de la mission : pour chaque croyance à corriger, "
          "dépose une croyance avec data.revises = id de l'ancienne, nouveau statut et confiance."),
]}

PROFILE_LABELS = {
    "rapide": "Question simple : un minimum d'abeilles, réponse directe.",
    "analyse": "Analyse / décision : cycle cognitif complet, sans action réelle.",
    "action": "Action : cycle complet + simulation + exécution sous contrôle humain.",
    "studio": "Production multimédia : script, images, voix, sous-titres, montage, contrôle qualité, livraison validée.",
}

STUDIO_WORDS = ("vidéo", "video", "clip", "dessin animé", "animation", "court-métrage", "film ")
ACTION_WORDS = ("envoie", "publie", "poste", "écris le fichier", "crée le fichier", "exécute",
                "déploie", "achète", "paie", "commande", "supprime", "planifie l'envoi")
ANALYSIS_WORDS = ("analyse", "compare", "stratégie", "décide", "décision", "risque", "évalue",
                  "faut-il", "dois-je", "plan", "étudie", "prévois", "anticipe", "pourquoi")


def triage(text: str, has_attachments: bool) -> str:
    """Triage déterministe (sans LLM) : simple par défaut, complexe si nécessaire."""
    t = text.lower()
    if any(w in t for w in STUDIO_WORDS) and not any(w in t for w in ("analyse cette", "résume cette", "regarde cette")):
        return "studio"
    if any(w in t for w in ACTION_WORDS):
        return "action"
    if any(w in t for w in ANALYSIS_WORDS) or len(t) > 280:
        return "analyse"
    return "rapide"
