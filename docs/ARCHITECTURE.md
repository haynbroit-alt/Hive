# Architecture (v0.2)

```
hive/
├── core/                 ← aucune dépendance externe
│   ├── ledger.py         Registre : append-only (déclencheurs SQL) + chaîne SHA-256, écritures inter-processus,
│   │                     verrous atomiques (table claims), projections rejouées et rattrapées (catch_up)
│   ├── board.py          Le Rayon : cellules typées (= traces), révision, phéromones ; projection du registre
│   ├── workers.py        Ouvrière : needs / after / produces / keys / inputs / run ; contexte de mission
│   ├── runs.py           Exécutions : verrou (mission, ouvrière, clé, révision) + cache par hachage des entrées
│   ├── colony.py         Moteur stigmergique : détection du travail, enchères, exécution parallèle, cache,
│   │                     protocole, révisions, validations humaines, disjoncteurs, diagnostic de blocage
│   ├── governance.py     Gardiennes : risque, budget global + par mission, capacités, interrupteur d'arrêt
│   ├── agent.py          Abeilles : compétence (a priori × réputation × calibration), prompt, sortie JSON
│   ├── reputation.py     Réputation Beta par compétence + calibration (Brier) — projection
│   ├── llm.py · http.py  Fournisseurs (hors-ligne, Ollama, compatible OpenAI, Claude) ; HTTP en stdlib
│   ├── tools.py · media.py · views.py · dreaming.py · cycle.py · offline.py
├── packs/conseil.py      Le cycle cognitif en ouvrières (analyse, décision collective, action)
├── studio/               Production multimédia réelle
│   ├── templates.py      Savoir-faire : cadrage du brief, gabarits multilingues, consignes de révision
│   ├── render.py         Illustrations en Python pur (formes lissées + PNG) et mesure de couleur
│   ├── av.py             Voix locale, sous-titres, montage et mesures ffmpeg/ffprobe
│   └── workers.py        Les 12 ouvrières du Studio
├── channels/telegram.py  Téléphone sans port ouvert (long polling), stdlib
├── api/                  API + PWA (option [web] : FastAPI)
├── roster.py · config.py · selftest.py · __main__.py
```

## Le principe : la stigmergie

Une ouvrière ne connaît **personne** : elle connaît des **traces**.

```python
class MontageWorker(Worker):
    needs = ("@illustration", "@voix", "@sous_titres")  # traces ou achèvements attendus (révision courante)
    produces = frozenset({"video"})                       # seules traces qu'elle a le droit de déposer
    def keys(self, ctx): return spec_of(ctx)["languages"]  # une exécution par langue, en parallèle
    def inputs(self, ctx, key): ...                       # ce qui détermine le résultat (haché → cache)
    async def run(self, ctx, key, bee): ...               # le travail ; dépose des cellules sur le Rayon
```

À chaque tour, la colonie déroule les étapes suivantes :

1. **Rattrapage.** Elle applique les événements écrits par d'autres processus, comme la CLI ou Telegram.
2. **Détection du travail.** Pour chaque mission active, elle repère les ouvrières dont les besoins sont
   satisfaits, qui ne sont pas retenues par un `after` encore actif, et dont une clé n'a pas encore
   d'exécution.
3. **Enchères.** Les abeilles qui possèdent la compétence enchérissent. Une abeille-LLM ne prend qu'un
   travail par tour ; les ouvrières-outils (rendu, montage) travaillent en parallèle.
4. **Verrouillage.** La prise du travail est un événement du registre assorti d'un verrou unique en
   base : deux processus ne peuvent pas prendre la même tâche.
5. **Exécution.** Si une exécution réussie aux **entrées identiques** existe déjà, ses traces sont
   recopiées (cache). Sinon l'ouvrière travaille.
6. **Clôture.** Si plus rien ne peut avancer, la mission se termine si une ouvrière *terminale* a fini.
   Sinon elle s'arrête **avec un diagnostic** (« Montage attend @voix… ») envoyé au Conseil.

**Ajouter une capacité, c'est ajouter une classe** à `conseil_workers()` / `studio_workers()` (ou à un
nouveau pack). Le moteur la découvre, l'insère dans le graphe des traces, et `hive graph` l'affiche.
Les étapes conditionnelles émergent d'elles-mêmes : sans trace `contradiction`, l'ouvrière
*Hypothèses* ne démarre jamais.

## Révisions : refaire seulement ce qui a changé

Une révision `n+1` s'ouvre dans trois cas : un **refus motivé** du Conseil, une **consigne**
(`hive revise`), ou un **contrôle qualité échoué mais corrigeable**. Voici ce qui se passe :

- La consigne devient une trace `message` à la nouvelle révision. Elle est *de confiance* si elle vient
  du Conseil, *non fiable* si elle est automatique.
- Les ouvrières se relancent d'elles-mêmes. Celles dont les **entrées n'ont pas changé** réutilisent
  leur résultat. Dans la démo, après « ajoute le violet », 6 illustrations sur 7 sont reprises du cache,
  et 2 clips vidéo sur 7 par langue (les scènes dont la durée n'a pas changé).
- Bornes : `max_revisions`, et un garde-fou **« pas de progrès »**. Si la même correction automatique
  échoue deux fois, la décision revient au Conseil (« Livrer MALGRÉ n défauts ? »). La ruche ne
  reboucle pas.

## Studio : ce que mesure le contrôle qualité

Pour chaque langue :
- texte disponible ;
- durée mesurée par ffprobe (±0,6 s) ;
- résolution ;
- piste audio ;
- piste de sous-titres étiquetée (ISO 639-2) ;
- voix synchronisée (aucune réplique plus longue que sa scène) ;
- lisibilité ≤ 17 caractères/s ;
- chaque couleur nommée.

Pour l'ensemble de la vidéo :
- durée du scénario conforme au brief ;
- couleur réelle de chaque illustration, mesurée dans l'image (écart < 45) ;
- consignes du Conseil effectivement appliquées.

Les défauts corrigeables (lisibilité, voix trop longue) déclenchent une correction automatique.
Les autres, comme une capacité manquante, remontent au Conseil.

## Modèle de données

Une **cellule** (`Cell`) porte les champs suivants : `id, kind, author, content, mission_id, rev, data,
status, confidence, phase, refs, pheromone, taint, claimed_by`. Le Rayon, les exécutions, la réputation,
le budget et l'état pause/reprise sont des **projections** du registre. `Board(ledger)` reconstruit
exactement le même état (testé), même entre processus.

Statuts épistémiques : `fact` (exige des `refs` réelles), `hypothesis`, `unknown`.

## Extension

- **Nouveau modèle** : `[providers.x]` dans `hive.toml` (Ollama ou API compatible OpenAI), puis `[models]`.
- **Nouvelle voix** : `[studio.tts] command = ["piper", "--model", "fr.onnx", "--output_file", "{out}"]`
  (le texte est envoyé sur l'entrée standard).
- **Nouvel outil** : sous-classe de `Tool` (`risk`, `external`, `reversible`, `simulate()`, `execute()`).
- **Nouvelle ouvrière / nouveau pack** : une classe `Worker` (voir ci-dessus).
- **Nouveau canal** : abonnez-vous à `colony.notifiers` (voir `channels/telegram.py`).
