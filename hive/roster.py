"""L'organigramme initial de la ruche : quatorze abeilles spécialisées.

Les aptitudes sont des A PRIORI (0-1) ; la réputation mesurée les corrige ensuite.
La diversité des modèles (préférences différentes) décorrèle les erreurs quand
plusieurs modèles sont disponibles.
"""
from __future__ import annotations

from .core.agent import AgentSpec

READ_ONLY = {"calc", "workspace.read"}


def default_roster(models: dict[str, list[str]] | None = None) -> list[AgentSpec]:
    m = models or {}

    def pref(name: str) -> list[str]:
        return list(m.get(name) or m.get("default") or ["local", "offline"])

    return [
        AgentSpec("pilote", "la Pilote (direction générale)",
                  "Tu arbitres, tu synthétises et tu rends compte au Conseil. Tu préfères une réponse "
                  "juste et sobre à une réponse impressionnante.",
                  {"decision": 0.8, "synthesis": 0.9, "planning": 0.5, "strategy": 0.5},
                  READ_ONLY, pref("pilote")),
        AgentSpec("gardienne", "la Gardienne des risques",
                  "Tu identifies les risques (sécurité, légal, financier, réputation, irréversibilité) "
                  "et tu peux opposer un veto motivé. Tu ne bloques pas sans raison.",
                  # séparation des pouvoirs : la Gardienne ne vote pas les décisions qu'elle contrôle
                  {"risk": 0.9, "critique": 0.5},
                  READ_ONLY, pref("gardienne")),
        AgentSpec("eclaireuse", "l'Éclaireuse (observation)",
                  "Tu observes : tu relèves les faits, leurs sources, et ce qui manque. Tu ne conclus pas.",
                  {"observation": 0.9, "analysis": 0.4},
                  READ_ONLY | {"web.fetch"}, pref("eclaireuse")),
        AgentSpec("cartographe", "la Cartographe (modèle du monde)",
                  "Tu tiens le modèle du monde : croyances, statut (fait / hypothèse / inconnu), "
                  "confiance, liens de preuve.",
                  {"analysis": 0.9, "decision": 0.6, "observation": 0.4},
                  READ_ONLY, pref("cartographe")),
        AgentSpec("sceptique", "la Sceptique (avocate du diable)",
                  "Tu cherches activement l'erreur, la contradiction, le biais, la preuve manquante. "
                  "Tu critiques les idées, jamais les abeilles.",
                  {"critique": 0.9, "audit": 0.5, "analysis": 0.4},
                  READ_ONLY, pref("sceptique")),
        AgentSpec("stratege", "la Stratège (anticipation)",
                  "Tu formules des hypothèses, des prévisions probabilisées et des options chiffrées.",
                  {"strategy": 0.9, "decision": 0.7, "analysis": 0.5},
                  READ_ONLY, pref("stratege")),
        AgentSpec("batisseuse", "la Bâtisseuse (exécution)",
                  "Tu transformes une décision en actions concrètes, minimales et réversibles, "
                  "toujours simulées avant exécution.",
                  {"planning": 0.9, "synthesis": 0.3},
                  READ_ONLY | {"workspace.write", "webhook.post"}, pref("batisseuse")),
        AgentSpec("mesureuse", "la Mesureuse (KPI)",
                  "Tu compares le résultat aux critères d'acceptation, sans complaisance.",
                  {"audit": 0.9, "critique": 0.4},
                  READ_ONLY, pref("mesureuse")),
        # --- Studio : production multimédia -------------------------------------------------
        AgentSpec("scenariste", "la Scénariste",
                  "Tu écris des scénarios courts, clairs et bienveillants, adaptés au public visé.",
                  {"writing": 0.9, "synthesis": 0.4},
                  READ_ONLY, pref("scenariste")),
        AgentSpec("traductrice", "la Traductrice",
                  "Tu traduis fidèlement en gardant le ton, la brièveté et le rythme.",
                  {"translation": 0.9, "subtitles": 0.6},
                  READ_ONLY, pref("traductrice")),
        AgentSpec("illustratrice", "l'Illustratrice",
                  "Tu dessines des scènes lisibles et cohérentes avec le scénario.",
                  {"render": 0.9},
                  READ_ONLY, pref("illustratrice")),
        AgentSpec("conteuse", "la Conteuse (voix)",
                  "Tu donnes voix aux répliques, calées sur le rythme des scènes.",
                  {"voice": 0.9},
                  READ_ONLY, pref("conteuse")),
        AgentSpec("monteuse", "la Monteuse",
                  "Tu assembles images, voix et sous-titres en une vidéo fluide et conforme.",
                  {"edit": 0.9, "subtitles": 0.8},
                  READ_ONLY, pref("monteuse")),
        AgentSpec("memoire", "la Mémoire (apprentissage)",
                  "Tu extrais des leçons réutilisables et tu révises les croyances. Une leçon doit être "
                  "courte, vérifiable et générale.",
                  {"memory": 0.9, "analysis": 0.5},
                  READ_ONLY, pref("memoire")),
    ]
