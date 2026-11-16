# ⬢ HIVE — une intelligence collective qui produit, sous votre contrôle

HIVE est une **colonie d'agents IA** (les « abeilles ») qui ne se parlent jamais directement :
elles déposent des **traces** sur un tableau commun, le **Rayon**, et chaque **ouvrière** démarre
d'elle-même dès que les traces qu'elle attend existent (stigmergie). La colonie analyse, décide,
**produit de vrais livrables** (par exemple une vidéo bilingue), se contrôle, se corrige et apprend.
**Rien n'est livré, dépensé ou exécuté de risqué sans votre accord**, depuis votre téléphone.
Vous êtes le **Conseil**.

- **0 € par défaut.** Le cœur n'a **aucune dépendance** (bibliothèque standard Python) ; ffmpeg et espeak-ng sont optionnels.
- **Docs :** [Vision](docs/VISION.md) · [Architecture](docs/ARCHITECTURE.md) · [Sécurité](docs/SECURITY.md) · [Recherche](docs/RESEARCH.md) · [Feuille de route](docs/ROADMAP.md)

## Essayer en 1 minute

```bash
git clone <ce dépôt> && cd Hive
python3 -m hive demo        # Python ≥ 3.11 + ffmpeg ; espeak-ng pour la voix (sinon vidéo muette, signalée)
python3 -m hive selftest    # tente réellement de franchir chaque garde-fou : 12/12 attendus
```

La démo confie à la ruche le brief **« Vidéo 30s fr/en : la pomme et la poire apprennent les couleurs »**, puis :

1. Les ouvrières se déclenchent d'elles-mêmes, dans cet ordre : cadrage → scénario → traduction,
   illustrations (une par scène), voix et sous-titres (une par langue) → montage → contrôle qualité.
2. Deux vraies vidéos MP4 de 30 s sont produites, en français et en anglais : images, voix,
   sous-titres incrustés et piste de sous-titres étiquetée. Le contrôle qualité (ffprobe, lisibilité,
   couleurs) donne 21/21 en ~8 s.
3. Le Conseil **refuse** (rejet critique simulé) : « Il manque le violet : ajoute le violet. »
4. La **révision 2** applique la consigne ; les 6 images inchangées sont **réutilisées depuis le cache** ;
   le contrôle donne 22/22.
5. Le Conseil accepte → **livraison**. Une **leçon** est proposée ; une fois que vous l'activez, les
   missions suivantes l'appliquent d'elles-mêmes.
6. Le registre (~290 événements chaînés SHA-256) est vérifié intact.

## Commander la ruche

| Commande | Effet |
|---|---|
| `hive new "brief"` / `hive run [--watch]` | Confier une mission (#n) / faire travailler la ruche |
| `hive status [N]` · `hive log N` · `hive graph` | État ; journal chronologique (traces, enchères, verrous) ; qui attend quoi |
| `hive approve N` · `hive reject N "motif"` · `hive revise N "consigne"` | Décider. Un refus de livraison devient la consigne de la révision suivante |
| `hive topup N 5` | Recharger l'enveloppe budgétaire d'une mission (le plafond global reste la limite) |
| `hive bot` | **Telegram seul, zéro dépendance** : missions par texte/vocal/photo/vidéo, vidéos à valider envoyées sur le téléphone |
| `hive serve` | Application mobile (PWA) + API + Telegram — `pip install ".[web]"` |
| `hive selftest` · `hive verify` · `hive schema` · `hive dream` | Garde-fous ; intégrité du registre ; schéma SQL ; audit (« rêve ») |

`hive` = `python -m hive` (ou `pip install -e .` pour la commande `hive`).

## Ce que sait faire la ruche (v0.2)

| Profil | Déclenché par | Ouvrières |
|---|---|---|
| **rapide** | une question simple | comprendre → synthèse → mesurer → bilan |
| **analyse** | « analyse », « décide », « risque »… | le cycle cognitif complet : observer → comprendre → confronter → hypothèses (s'il y a contradiction) → anticiper → prioriser → 3 votes indépendants → quorum → contrôle des risques → arbitrage → synthèse → mesurer → apprendre → réviser |
| **action** | « écris le fichier », « envoie »… | analyse + simulation (bac à sable) + exécution, ou votre validation |
| **studio** | « vidéo », « clip », « animation »… | cadrage → scénario → traduction × langue, illustration × scène, voix × langue, sous-titres × langue → montage × langue → contrôle qualité → correction automatique ou revue → **votre validation** → livraison + leçons |

## Comparé au pilote proposé

Le pilote collé (`hive.py`, tronqué) pose de bonnes bases : agents déclenchés par les traces, base
append-only, verrous d'idempotence, révisions, budget par job, `demo`, `selftest`. HIVE v0.2 reprend
ces principes et va plus loin sur chaque axe. La colonne « pilote » ne décrit que ce qui est
**visible dans l'extrait** (son générateur réel n'y figure pas).

| | Pilote (extrait) | HIVE v0.2 |
|---|---|---|
| Coordination | agents déclenchés par traces | + **enchères** entre abeilles (compétence × réputation × calibration), exécutions **parallèles par clé** (langue, scène, vote), ordre souple, **diagnostic** quand rien n'avance |
| Intégrité | déclencheurs SQL append-only | + **chaîne SHA-256** vérifiable : la falsification est détectée même si la base est contournée (testé) |
| Idempotence | table `runs` (job, agent, clé) | verrous **atomiques entre processus** (mission, ouvrière, clé, révision) + **cache par hachage des entrées** : une révision ne refait que ce qui a changé |
| Révisions | `rev`, `max_revisions` | + consignes **appliquées**, correction automatique avec garde-fou **« pas de progrès »**, leçons proposées puis appliquées aux missions suivantes |
| Budget | plafond par job, `topup` | plafond **global à 0 €** + enveloppe par mission + recharge ; un fournisseur payant est **sauté**, jamais appelé |
| Livrable | contenus texte ou chemins de fichier | **vraies vidéos MP4 bilingues**, mesurées par ffprobe ; contrôle qualité objectif (21 à 22 contrôles) |
| Contrôle humain | `approve` / `reject` en CLI | CLI + **téléphone** (vidéo envoyée sur Telegram, PWA) ; refus motivé → révision ; interrupteur d'arrêt |
| Sécurité | coupe-circuit `max_events` | + contamination des données externes, règle de deux, capacités atténuées, champs réservés, *evidence first*, authentification, CSRF, CSP |
| Vérification | `selftest` | `selftest` (12 garde-fous réellement attaqués) + **54 tests** + parcours réel dans un navigateur mobile |
| Ce n'est pas qu'un studio | — | le même moteur fait tourner l'**analyse/décision** collective (votes indépendants, quorum, veto) |

## Brancher un vrai cerveau (0 €, privé)

Sans modèle de langage, la ruche reste utile : le Studio produit de vraies vidéos à partir de ses
**gabarits** (couleurs, chiffres ; français, anglais, espagnol). Elle signale honnêtement ce
qu'elle ne sait pas faire, par exemple une langue sans gabarit ou une consigne qu'elle ne comprend pas.
Pour écrire librement, brancher un modèle local :

```bash
ollama pull gemma4:e4b                  # https://ollama.com — vérifiez le tag exact avec `ollama list`
cp hive.example.toml hive.toml          # le fournisseur « local » y est déclaré
```

Le scénario du modèle est **validé** (durée exacte, lisibilité). S'il est invalide, la ruche revient au
gabarit et journalise l'écart.

## Piloter depuis le smartphone

- **Telegram** (le plus simple, aucun port ouvert) :
  1. Créez un bot avec @BotFather.
  2. `export HIVE_TELEGRAM_TOKEN=…`
  3. Lancez `hive bot` et écrivez `/start` au bot : il vous donne votre identifiant.
  4. Ajoutez cet identifiant à `allowed_chat_ids` dans `hive.toml`, puis relancez.
  5. Envoyez « Vidéo 30s fr/en : … ». Les vidéos arrivent dans la discussion avec les boutons
     **✅ Livrer / ⛔ Refuser** ; un refus vous demande son motif.
- **Application (PWA)** : `hive serve` puis ouvrir via Tailscale (réseau privé gratuit) et « Ajouter à
  l'écran d'accueil ». Ne l'exposez **jamais** directement sur Internet ([SECURITY.md](docs/SECURITY.md)).

## État honnête

- **Vérifié :** 54 tests, autotest 12/12, démo de bout en bout, parcours mobile réel dans Chromium,
  deux processus concurrents sur le même registre.
- **Non vérifié :** appel à un vrai modèle (aucun dans cet environnement) ; Telegram contre la vraie
  API (testé contre une imitation) ; un vrai téléphone.
- La voix d'espeak-ng est robotique. Piper ou Kokoro peuvent être branchés via `studio.tts.command`.

Licence : à décider par le Conseil (MIT proposé).
