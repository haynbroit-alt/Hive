# Sécurité et contrôle humain

Principe directeur : **les agents proposent, la politique dispose, le Conseil tranche.**
Aucune sécurité ne repose sur la « bonne volonté » d'un modèle de langage.

## Contrôles en place (v0.1) et tests qui les prouvent

| Menace (OWASP Agentic 2026) | Contrôle HIVE | Test |
|---|---|---|
| ASI01 Détournement d'objectif / injection de prompt | Tout contenu externe (web, fichiers, médias, transcriptions) est **contaminé** ; la contamination se propage à toute sortie dérivée et ne peut jamais être retirée ; les prompts l'étiquettent `[NON FIABLE]` ; seul le Conseil émet des cellules `trusted` | `test_taint_cannot_be_removed…`, `test_image_mission_observations_are_tainted…` |
| ASI02 Mauvais usage d'outils | Chaque agent a une liste d'outils ; chaque outil déclare risque, réversibilité, effet externe, dépense ; **simulation obligatoire** avant exécution ; listes blanches (domaines, webhooks) ; espace de travail confiné et versionné ; calcul sans `eval` | `test_workspace_confinement…`, `test_safe_eval`, `test_web_fetch_allowlist` |
| ASI03 Abus d'identité ou de privilèges | Capacités **atténuées** pour les sous-agents (intersection stricte avec le parent) ; champs réservés (`trusted`, `action`, `assessment`, `exec_status`…) que les agents ne peuvent pas écrire | `test_subagent_capabilities_are_attenuated`, `test_protocol_enforcement…` |
| ASI05 Exécution de code inattendue | Aucun outil n'exécute de code arbitraire en v0.1 ; ffmpeg est appelé sans shell, avec des délais limites | — |
| ASI06 Empoisonnement mémoire / contexte | Les leçons restent `proposed` jusqu'à votre validation ; le rêve **propose** sans rien modifier ; les références inventées sont supprimées ; un « fait » sans preuve est rétrogradé | `test_feedback_forecasts_and_lessons…`, `test_dreaming_audits_without_changing_memory` |
| ASI07 Communication inter-agents non sûre | Aucun canal privé : tout passe par le Rayon. Registre **append-only imposé par la base** (déclencheurs SQL) **et** chaîné SHA-256 (une retouche faite en contournant la base est détectée). Protocole validé par le moteur : types autorisés par ouvrière, anti-doublon | `test_ledger_chain_and_tamper_detection`, `test_board_is_a_replayable_projection`, `selftest` |
| ASI08 Pannes en cascade | Disjoncteurs par mission (étapes, appels modèle, **événements**, durée). L'entraide est limitée à un niveau. **Révisions bornées** + garde-fou « pas de progrès » (une correction automatique inefficace rend la main au Conseil). **Verrous atomiques** : un travail n'est jamais exécuté deux fois, même par deux processus | `test_circuit_breaker_stops_runaway_mission`, `test_failed_quality_check_self_corrects_then_hands_back_without_looping`, `test_ledger_is_safe_across_processes_and_claims_are_atomic` |
| Livraison non validée | Une ouvrière ne peut pas livrer : seule la décision du Conseil crée la trace `delivery`. La revue présente les vidéos et les défauts éventuels | `test_studio_produces_real_bilingual_videos_and_waits_for_the_council` |
| ASI09 Exploitation de la confiance humaine | Les validations affichent le risque, les raisons, la simulation et les arguments exacts ; double confirmation pour un risque élevé ; le contenu est rendu sans HTML (`textContent`) | revue de `app.js` |
| ASI10 Agents « voyous » | Interrupteur d'arrêt global (téléphone, Telegram, API) ; réputation et calibration réduisent l'influence d'un agent peu fiable ; le rêve signale les violations répétées | `test_kill_switch_pauses_everything` |
| Dépenses | Plafond **global 0 €** + **enveloppe par mission** (recharge = geste humain) ; estimation pessimiste avant chaque appel ; un fournisseur payant est **sauté**, jamais appelé ; relever le plafond global exige une validation humaine explicite | `test_budget_zero_blocks_paid_provider…`, `test_budget_raise_requires_explicit_human_approval` |
| Règle de deux (Meta) | Une tâche qui a vu des données non fiables **et** propose un effet externe exige toujours votre validation | `test_risk_assessment_rules` |

Tous ces garde-fous sont aussi **attaqués réellement** par `python -m hive selftest` (12 contrôles,
sans réseau ni dépense), à lancer après chaque mise à jour.

## Médias produits

- Les fichiers produits (images, voix, sous-titres, vidéos) sont adressés par leur contenu (SHA-256).
  Ils sont servis en ligne seulement pour les formats inertes (PNG/JPEG/GIF/WebP, MP4, audio) ; les
  autres sont téléchargés.
- ffmpeg et espeak-ng sont appelés **sans shell** (listes d'arguments), avec des délais limites. Les
  textes à incruster passent par des fichiers (`textfile=`) et non par la ligne de commande.

## Accès depuis le smartphone

- Le serveur écoute sur **127.0.0.1** par défaut. Ne l'exposez **jamais** directement sur Internet
  (rappel : en 2026, des dizaines de milliers d'instances d'OpenClaw ont été exposées).
- Accès recommandé : **Tailscale** (réseau privé, 0 €). `tailscale serve` peut fournir le HTTPS
  nécessaire au micro et à la caméra *(à vérifier sur votre installation)*.
- Jeton de 192 bits ; cookie `HttpOnly` et `SameSite=Strict` ; en-tête `X-Hive` exigé sur toute écriture
  (anti-CSRF) ; 5 essais de connexion par 5 minutes ; CSP stricte sans script tiers ; aucune API mise en
  cache par le service worker.
- Le lien de connexion transporte le jeton dans le **fragment** (`#token=`). Le fragment n'est jamais
  envoyé au serveur ni écrit dans les journaux, et la page l'efface aussitôt.
- **Telegram** (`hive bot`, zéro dépendance) : long polling (aucun port ouvert), liste blanche
  d'identifiants. Un refus de livraison demande toujours son **motif**, qui devient la consigne tracée
  de la révision suivante. N'autorisez que des
  discussions **privées** : dans un groupe, n'importe quel membre pourrait valider. Les discussions avec
  un bot ne sont **pas chiffrées de bout en bout** : n'y transmettez pas de secrets.

## Secrets

Clés d'API et jeton Telegram : **variables d'environnement uniquement**. `hive.toml` ne contient que
le *nom* de la variable. `data/` et `hive.toml` sont exclus de git.

## Limites connues (à traiter)

1. L'injection de prompt peut encore **fausser une analyse**. Elle ne peut plus déclencher d'action
   sans votre validation, mais elle peut tromper un raisonnement.
2. Le registre n'est pas chiffré au repos. Si votre machine est compromise, la ruche l'est aussi.
3. Mono-utilisateur : pas de rôles multiples (lecteur, approbateur…).
4. Le `web.fetch` ne suit pas les redirections. La liste blanche par domaine ne protège pas contre un
   domaine autorisé lui-même compromis.
5. Les dépendances Python (FastAPI, httpx…) font partie de la surface d'attaque. Mettez-les à jour.
