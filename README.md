# ⬢ HIVE — une intelligence collective que vous dirigez depuis votre téléphone

HIVE est une **colonie d'agents IA** (les « abeilles ») qui observent, comprennent, se contredisent,
votent, simulent, agissent et apprennent, **sous votre contrôle** : vous êtes le **Conseil**.
Elle travaille avec le texte, la voix, les images et les vidéos. Elle coûte **0 €** par défaut et ne
dépense jamais rien sans votre accord explicite.

- **Vision et autocritique** : [docs/VISION.md](docs/VISION.md)
- **Recherche sourcée** (frameworks, protocoles, preuves, pile à 0 €) : [docs/RESEARCH.md](docs/RESEARCH.md)
- **Architecture** : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · **Sécurité** : [docs/SECURITY.md](docs/SECURITY.md) · **Feuille de route** : [docs/ROADMAP.md](docs/ROADMAP.md)

## Ce qui fonctionne aujourd'hui (v0.1)

| | |
|---|---|
| 🧠 **Cycle cognitif complet** | OBSERVER → COMPRENDRE → CONFRONTER → HYPOTHÈSES → ANTICIPER → PRIORISER → DÉCIDER → SIMULER → EXÉCUTER / VALIDATION HUMAINE → MESURER → APPRENDRE → RÉVISER, plus le RÊVE |
| 🐝 **9 abeilles spécialisées** | Pilote, Gardienne des risques, Éclaireuse, Cartographe, Sceptique, Stratège, Bâtisseuse, Mesureuse, Mémoire, plus des sous-agents éphémères aux droits atténués |
| 🍯 **Le Rayon** | Tableau collectif unique, sans canal privé entre abeilles ; phéromones ; entraide ; « danse » de recrutement |
| 🗳 **Décision collective** | Propositions indépendantes → vote pondéré par la réputation → quorum → veto possible de la Gardienne → votre arbitrage |
| 🔎 **Evidence first** | `fact` / `hypothesis` / `unknown` ; un fait sans preuve est rétrogradé ; une référence inventée est supprimée |
| 🛡 **Gardiennes** | Budget 0 €, bac à sable avant toute action, risque déterministe, contamination des données externes, règle de deux, disjoncteurs, interrupteur d'arrêt |
| 📱 **Application mobile (PWA)** | Flux en direct, missions (texte, 📎 photo/vidéo/audio, 🎙️ vocal), validations, leçons, prévisions, abeilles, budget, pause |
| ✈️ **Telegram** | Missions par texte, vocal, photo ou vidéo ; boutons Autoriser/Refuser ; livrables envoyés ; aucun port ouvert |
| 🎞 **Multimodal** | Images (dimensions + modèle de vision), vidéo (images clés et bande son via ffmpeg), audio (transcription locale optionnelle) |
| 🔒 **Registre** | Chaque événement est chaîné SHA-256 : audit, rejeu, détection de falsification |

**Honnêteté :** sans modèle branché, la ruche tourne en mode `[hors-ligne]`. Elle fait circuler tout
le protocole mais **n'analyse rien** et le dit. Pour une vraie intelligence, branchez un modèle local
(étape 2 ci-dessous). Rien n'a encore été mesuré avec un vrai modèle : c'est l'objectif du jour 2.

## Démarrage en 5 minutes

```bash
git clone <ce dépôt> && cd Hive
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                     # 42 tests, sans réseau, sans dépense
python -m hive mission "Analyse s'il faut lancer une newsletter, mais sans audience."
python -m hive serve                    # puis ouvrez le lien affiché (il contient votre jeton)
```

### 2. Lui donner un cerveau local (0 €, privé)

```bash
# https://ollama.com : installez Ollama, puis
ollama pull gemma4:e4b                  # ~7-10 Go ; texte + image (vérifiez le tag exact avec `ollama list`)
cp hive.example.toml hive.toml          # le fournisseur « local » y est déjà déclaré
python -m hive serve
```

Avec 8 Go de RAM, choisissez un modèle de 3 à 4 milliards de paramètres. Avec 16 Go, 8 à 12
milliards (ex. `ministral-3:8b`). Deux modèles différents rendent les votes réellement indépendants.

### 3. Piloter depuis le smartphone

- **Telegram (le plus simple, aucun port ouvert)** :
  1. Créez un bot avec @BotFather.
  2. `export HIVE_TELEGRAM_TOKEN=…` (jamais dans un fichier versionné).
  3. Mettez `enabled = true` dans la section `[telegram]` de `hive.toml`.
  4. Écrivez `/start` au bot : il vous donne votre identifiant.
  5. Ajoutez cet identifiant à `allowed_chat_ids` et redémarrez.
- **Application (PWA)** : installez Tailscale (gratuit) sur l'ordinateur et le téléphone, puis ouvrez
  la ruche via votre réseau privé et « Ajouter à l'écran d'accueil ». Ne l'exposez **jamais**
  directement sur Internet (voir [SECURITY.md](docs/SECURITY.md)).

### Commandes

| Commande | Effet |
|---|---|
| `python -m hive serve` | Ruche + application mobile + Telegram (si activé) |
| `python -m hive mission "…"` | Mission depuis le terminal (`--profile rapide\|analyse\|action`) |
| `python -m hive status` / `verify` / `dream` / `token` | État ; intégrité du registre ; rêve ; lien de connexion |

## Pile technique (tout est gratuit)

Python 3.11+, FastAPI, httpx, SQLite (registre), ffmpeg (vidéo). Interface HTML/CSS/JS sans
dépendance ni script tiers. Modèles : Ollama (local), toute API compatible OpenAI (Gemini gratuit en
option, avec votre accord), Claude en option payante (bloqué à 0 €).

## Licence

À décider par le Conseil (MIT proposé). Aucun fichier de licence n'est ajouté tant que ce choix n'est pas fait.
