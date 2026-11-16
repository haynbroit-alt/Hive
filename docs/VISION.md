# HIVE — Vision

> **Pas une collection de chatbots : une colonie.** Des abeilles spécialisées qui ne se parlent
> qu'à travers un tableau commun, votent sans se copier, prouvent ce qu'elles affirment, et
> rendent des comptes à un Conseil humain qui garde la main sur tout ce qui compte.

*Rédigé le 2026-11-16. Les mentions **[prouvé]**, **[hypothèse]** et **[inconnu]** distinguent ce
qui est établi (par la littérature ou par nos tests) de ce qui reste à démontrer.*

---

## 1. L'ambition, reformulée

Vous voulez diriger une entreprise d'intelligences artificielles depuis votre téléphone. Ramenée à
ses fondamentaux, la demande contient cinq exigences :

| Exigence | Ce qu'elle veut dire concrètement | Ce qui la rend difficile |
|---|---|---|
| **Collectif** | Le groupe doit produire *mieux* qu'un agent seul | Sur les tâches séquentielles, le multi-agents fait souvent *pire* (−39 à −70 %) **[prouvé, Kim et al. 2025]** |
| **Collaboration** | Demander de l'aide, partager une découverte, mener une mission commune | Les agents qui bavardent entre eux amplifient leurs erreurs (jusqu'à ×17) **[prouvé, idem]** |
| **Multimodal** | Texte, voix, image, vidéo | À 0 €, tout doit tourner en local ou sur des offres gratuites limitées |
| **Pilotage mobile** | Observer, commander, valider depuis le smartphone | Exposer un agent sur Internet est dangereux (OpenClaw 2026 : 31 674 instances exposées, CVE critiques) **[prouvé]** |
| **Contrôle humain, 0 €, sécurité** | Aucune dépense ni action risquée sans vous | Un clic « approuver » n'est pas une politique ; l'injection de prompt n'a pas de solution parfaite |

**Conclusion de premier principe :** l'intelligence collective ne vient pas du *nombre* d'agents,
mais de quatre propriétés connues depuis Surowiecki et Woolley : **diversité**, **indépendance**,
**agrégation** et **vérification**, plus une cinquième pour une organisation qui dure : la
**mémoire qui s'améliore**. HIVE est conçu pour garantir ces propriétés *par construction*, et pour
n'utiliser plusieurs agents que là où c'est utile.

## 2. Le concept : une ruche dont vous êtes le Conseil

```
                 ┌──────────────── VOUS : le Conseil (smartphone) ────────────────┐
                 │ missions · directives · validations · veto · budget · retour    │
                 └───────────────┬───────────────────────────────▲─────────────────┘
                                 │ seul canal d'instructions      │ livrables, alertes,
                                 ▼ de confiance                   │ demandes de validation
 ┌──────────────────────────────── LE RAYON (tableau collectif) ─────────────────────────────┐
 │ cellules typées : observation · croyance (fait/hypothèse/inconnu) · contradiction ·       │
 │ hypothèse · prévision · option · proposition · décision · simulation · résultat · leçon…   │
 │ chaque cellule : auteur, preuves (refs), confiance, contamination, phéromone qui s'évapore │
 └──────▲──────────▲──────────▲──────────▲──────────▲──────────▲──────────▲──────────▲───────┘
        │          │          │          │          │          │          │          │
    Éclaireuse Cartographe Sceptique  Stratège   Pilote   Gardienne  Bâtisseuse Mesureuse/Mémoire
    (observer) (comprendre)(confronter)(anticiper)(synthèse)(risques)  (agir)    (mesurer/apprendre)
        └──────────── enchères : compétence × réputation × calibration ─────────────┘
 ┌──────────────── LES GARDIENNES (gouvernance déterministe) ─────────────────┐
 │ budget 0 € · capacités par agent · risque · contamination · règle de deux │
 │ bac à sable · validation humaine · disjoncteurs · interrupteur d'arrêt    │
 └──────────────── LE REGISTRE : chaque événement, chaîné SHA-256 ─────────────┘
```

Trois choix structurants, et pourquoi :

1. **Pas de canal privé entre agents. Tout passe par le Rayon.** Le Rayon est un *blackboard*
   (architecture des années 1970, Hearsay-II), redécouvert pour les LLM en 2025 : les systèmes
   blackboard surpassent les multi-agents statiques en consommant moins de jetons **[prouvé, modéré :
   arXiv 2507.01701, 2510.01285]**. Pour vous, c'est surtout ceci : **vous voyez tout**, la
   gouvernance intercepte tout, et tout est rejouable. C'est votre règle « ne contourne jamais les
   règles de communication de HIVE », transformée en propriété du système.

2. **Un moteur central qui applique la physique de la ruche, pas un chef qui pense à la place
   des abeilles.** La recherche montre que la coordination centralisée contient mieux les erreurs
   **[prouvé, Kim et al.]**. HIVE centralise donc le *protocole* (types autorisés, preuves, vote,
   sécurité) et décentralise le *travail* (les abeilles choisissent leurs tâches par enchères).
   La reine d'une vraie ruche ne commande pas non plus.

3. **Le nombre d'agents s'adapte au problème.** Un triage choisit un profil : *rapide* (3 étapes,
   pour une question simple), *analyse* (cycle cognitif complet), *action* (cycle complet + bac à
   sable + exécution sous contrôle). Ne pas lancer 14 étapes pour « quelle heure est-il à
   Lisbonne ? », c'est aussi de l'intelligence.

## 3. Votre boucle cognitive, implémentée

Votre schéma OBSERVER → … → DREAMING est le cœur du moteur (`hive/core/cycle.py`) :

| Votre étape | Dans HIVE | Garde-fou |
|---|---|---|
| OBSERVER | L'Éclaireuse relève les faits ; perception image/audio/vidéo (ffmpeg + modèle de vision) | Tout média est **contaminé** : une donnée, jamais une instruction |
| COMPRENDRE | La Cartographe construit des croyances `fact / hypothesis / unknown` | **EVIDENCE FIRST appliqué par le code** : un « fait » sans preuve est rétrogradé ; une référence inventée est supprimée |
| CONFRONTER | La Sceptique cherche contradictions et erreurs | Sortie `contradiction` ou `confirmation` justifiée |
| HYPOTHÈSES | Seulement s'il y a contradiction ; 2 abeilles **indépendantes** | Aucune ne voit la réponse de l'autre |
| ANTICIPER | Prévisions **probabilisées et pré-enregistrées** | Vous les résolvez plus tard → score de Brier → calibration |
| PRIORISER | Options chiffrées : impact, ROI, urgence, effort | — |
| DÉCIDER | 3 propositions indépendantes → **vote pondéré** (réputation × confiance) → **quorum** ; puis la Gardienne des risques contrôle et peut opposer un **veto** | Séparation des pouvoirs : la Gardienne ne vote pas ce qu'elle contrôle ; veto ou absence de quorum → vous arbitrez |
| SIMULER | Chaque action est jouée en **bac à sable** (`simulate()`) avant tout effet | Évaluation déterministe du risque |
| EXÉCUTER / HUMAN REVIEW | Risque faible : exécution ; au-delà : **votre validation**, depuis le téléphone | Dépense ⇒ toujours critique ; « règle de deux » |
| MESURER | La Mesureuse note le livrable vs critères + contrôles objectifs + **votre 👍/👎** | Votre retour pèse 4× plus que l'auto-évaluation |
| APPRENDRE | Leçons **proposées**, jamais actives sans votre accord | Défense contre l'empoisonnement de la mémoire (OWASP ASI06) |
| RÉVISER | Les croyances sont révisées avec traçabilité (`revises` / `revised_by`) | — |
| DREAMING | Audit hors-ligne : intégrité du registre, faits sans preuve, conflits, prévisions en attente, santé des abeilles | Le rêve **propose**, il ne modifie rien seul |

## 4. Ce qui est original (et ce qui ne l'est pas)

Les frameworks courants (LangGraph, CrewAI, Microsoft Agent Framework, OpenAI Agents SDK, Google ADK)
organisent des agents en *graphes*, en *équipes de rôles* ou en *passations*. HIVE emprunte à la
biologie et à la méthode scientifique des mécanismes peu exploités :

| Mécanisme | Inspiration | Statut |
|---|---|---|
| Tableau collectif unique, sans canal privé | Blackboard (Hearsay-II), bMAS 2025 | **[prouvé, modéré]** |
| Vote indépendant plutôt que débat | Les débats n'apportent pas plus que le vote majoritaire et coûtent plus (Choi et al. NeurIPS 2025 ; Smit et al. ICML 2024) ; le vote aide (+13 % en raisonnement, Kaesberg et al. 2025) | **[prouvé]** |
| **Quorum** façon essaimage d'abeilles (Seeley, *Honeybee Democracy*) | Biologie solide, **aucune étude sur LLM trouvée** | **[hypothèse à tester]** |
| **Phéromones** qui s'évaporent (priorité, attention) et **danse** de recrutement (une découverte crée sa propre tâche de vérification, d'intensité ∝ confiance) | Stigmergie ; une seule étude LLM, non relue par des pairs | **[hypothèse à tester]** |
| **Enchères** compétence × réputation × **calibration** | Contract-net ; réputation bayésienne Beta ; score de Brier | **[hypothèse à tester]**, la calibration de réputation d'agents LLM n'est pas documentée |
| Prévisions **pré-enregistrées** puis résolues | Méthode scientifique, *forecasting* | Mécanisme simple, effet **[inconnu]** |
| Contamination (*taint*) + bac à sable + règle de deux | CaMeL (Google DeepMind 2025), « Agents Rule of Two » (Meta) | **[prouvé]** comme réduction de l'impact des injections ; ne les empêche pas |
| Mémoire validée par l'humain | OWASP Agentic Top 10 (ASI06) | Bonne pratique **[prouvé]** |
| Registre chaîné (boîte noire rejouable) | Event sourcing | Technique éprouvée **[prouvé]** |
| Triage par profil | Kim et al. 2025 | **[prouvé]** que c'est nécessaire ; nos seuils sont **[hypothèse]** |

**Position honnête :** les éléments les plus originaux (quorum, phéromones, calibration) sont des
paris. HIVE est construit pour les **mesurer** (voir ROADMAP, banc d'essai du jour 5) et les abandonner
s'ils n'apportent rien.

## 5. Autocritique : les faiblesses de cette proposition

1. **Un mode hors-ligne n'est pas de l'intelligence.** Sans modèle branché, HIVE fait circuler le
   protocole et rédige une synthèse mécanique, honnêtement étiquetée `[hors-ligne]`. La qualité réelle
   dépendra du modèle local (Gemma 4 e4b, Ministral 3 8B…) et elle **n'a pas encore été mesurée**.
2. **Diversité réelle limitée.** Si les neuf abeilles utilisent le même petit modèle, leurs erreurs
   sont corrélées et le vote ressemble à de l'auto-cohérence. La vraie diversité exige plusieurs
   modèles (2 modèles locaux, ou local + Gemini gratuit).
3. **Lenteur.** Une mission « analyse » coûte environ 15 appels de modèle. Sur un CPU, cela peut
   prendre plusieurs minutes. À 0 €, le coût se paie en temps.
4. **L'auto-évaluation est biaisée.** La Mesureuse est un LLM qui note le travail de LLM. Votre
   retour humain est le seul vrai étalon ; sans lui, le score mesure surtout la forme.
5. **La contamination est conservatrice.** Dès qu'une abeille a vu une donnée externe, ses sorties
   sont marquées et ses actions demandent votre validation. C'est plus sûr, mais vous aurez des
   validations à donner.
6. **Sécurité mono-utilisateur.** Un seul jeton (le Conseil). Pas de chiffrement du registre au
   repos. Telegram n'est pas chiffré de bout en bout.
7. **Le moteur central est un point unique de défaillance**, assumé pour la sûreté et la lisibilité.

## 6. Métacognition : niveau de confiance

| Affirmation | Confiance | Sur quoi elle repose |
|---|---|---|
| Le protocole, la gouvernance et le registre fonctionnent comme décrit | Élevée | 42 tests automatisés + parcours réel dans un navigateur mobile |
| L'application mobile fonctionne sur iPhone/Android | Moyenne | Testée en émulation mobile (Chromium), pas sur un vrai appareil |
| Le canal Telegram fonctionne | Moyenne | Testé contre une imitation de l'API, pas contre le vrai Telegram |
| Les fournisseurs Ollama / API compatibles fonctionnent | Moyenne | Forme des requêtes testée hors réseau ; aucun appel réel fait dans cet environnement |
| Le fournisseur Claude fonctionne | Faible | Écrit d'après la documentation officielle, jamais appelé : il est payant et bloqué à 0 € |
| HIVE fait mieux qu'un agent seul | **Inconnue** | À mesurer au jour 5 |
| Le tag Ollama `gemma4:e4b` est exact | Moyenne | Vu dans des extraits de recherche, page non lue directement |

## 7. Ce dont j'ai besoin de vous

Voir la fin du README et la ROADMAP : votre matériel (pour le modèle local), votre canal préféré
(Telegram, l'application via Tailscale, ou les deux), votre accord ou non pour un secours cloud
gratuit (Gemini : vos données vont chez Google), et **une première vraie mission** pour mesurer la ruche
sur un objectif qui compte pour vous.
