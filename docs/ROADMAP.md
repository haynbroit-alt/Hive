# Feuille de route — semaine 1

Règle : chaque jour se termine par un **résultat vérifiable** et des **indicateurs mesurés**.
Ce qui n'est pas mesuré n'est pas considéré comme acquis.

| Jour | Objectif | Livrable vérifiable | Statut |
|---|---|---|---|
| **J1** | Comprendre, chercher, poser le noyau | Vision, recherche sourcée, noyau (Rayon, registre, gouvernance, cycle complet, vote/quorum, veto, bac à sable), application mobile, canal Telegram, 42 tests | ✅ fait |
| **J1 bis** | **Produire, pas seulement analyser** (v0.2) | Moteur stigmergique (ouvrières déclenchées par les traces, verrous inter-processus, cache, révisions bornées) ; **Studio** : vraie vidéo bilingue de 30 s à 0 €, contrôle qualité objectif, correction automatique, validation humaine, leçons ; cœur sans dépendance ; CLI complète, `demo`, `selftest` 12/12, 54 tests | ✅ fait |
| **J2** | **Brancher un vrai cerveau** (sur votre machine) | Ollama + Gemma 4 e4b ou Ministral 3 8B ; mesure du taux de JSON valide par phase, de la latence par mission, des violations de protocole ; prompts ajustés | ⏳ demande votre machine |
| **J3** | Outils et notifications | Client **MCP** (outils standard) ; recherche web contrôlée ; notifications push (ntfy et/ou Web Push) | à faire |
| **J4** | Voix de bout en bout | faster-whisper (local) testé en français ; voix de synthèse de qualité (Piper/Kokoro via `studio.tts.command`) à la place d'espeak-ng ; « mission vocale » depuis Telegram mesurée (latence, taux d'erreur) | à faire |
| **J5** | **Banc d'essai : la ruche est-elle plus intelligente ?** | 20 missions de référence notées à l'aveugle : agent seul, colonie sans vote, colonie complète. On garde ou on abandonne le quorum, les phéromones et la calibration selon les résultats | à faire |
| **J6** | Interopérabilité | Ruche exposée comme agent **A2A** (Agent Card) ; leçons exportées en **SKILL.md** ; sous-agents recrutés automatiquement quand une compétence manque | à faire |
| **J7** | Durcissement et bilan | Tests d'injection (documents piégés), revue de sécurité, rapport de semaine (objectifs vs résultats), décisions pour la semaine 2 | à faire |

## Indicateurs suivis (tableau de bord)

- Missions terminées / arrêtées / annulées ; durée et nombre d'appels modèle par mission.
- **Score humain** moyen (👍/👎). C'est l'étalon : l'auto-évaluation seule ne compte pas.
- Part des « faits » réellement prouvés ; violations de protocole par mission ; références inventées.
- **Calibration** (score de Brier moyen par abeille) ; réputation par compétence.
- Validations : demandées, accordées, refusées ; délai de réponse du Conseil.
- **Dépense : 0 €** (et appels payants bloqués).

## Studio : prochaines capacités

- Scénarios libres par modèle local (déjà branché et validé ; à mesurer avec un vrai modèle au J2).
- Nouveaux gabarits : formes, animaux, alphabet ; personnages au-delà des fruits ; mouvements simples.
- Musique de fond générée localement ; normalisation sonore (`loudnorm`) ; formats verticaux (9:16).
- Publication externe (YouTube, réseaux) : uniquement comme **action à haut risque**, toujours
  soumise à la validation du Conseil.

## Expériences à mener (hypothèses de la vision)

| Hypothèse | Expérience | Critère d'abandon |
|---|---|---|
| H1 — Le vote indépendant à quorum bat un décideur unique | 20 décisions, même modèle, comparaison à l'aveugle | Aucun gain mesurable ou latence > ×2 |
| H2 — La diversité de modèles améliore le vote | 2 modèles locaux contre 1 | Aucun gain |
| H3 — La pondération par calibration améliore les décisions | Comparer vote pondéré et non pondéré après 50 prévisions résolues | Aucun gain |
| H4 — Le triage évite le surcoût multi-agents | Questions simples : profil rapide contre analyse | Qualité inférieure en profil rapide |
| H5 — Les phéromones (priorité, danse) améliorent l'allocation | Comparer avec une file FIFO | Aucun gain mesurable |
