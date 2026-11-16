# Architecture

```
hive/
├── core/
│   ├── ledger.py      Registre append-only SQLite, chaîné SHA-256 (vérifiable, rejouable)
│   ├── board.py       Le Rayon : cellules typées, phéromones (demi-vie), projection du registre
│   ├── cycle.py       Phases du cycle cognitif, profils (rapide / analyse / action), triage
│   ├── agent.py       Abeilles : enchères, prompt (protocole + leçons), sortie JSON
│   ├── colony.py      Moteur : enchères, protocole, vote/quorum, veto, bac à sable, exécution,
│   │                  validations, mesure, perception multimodale, disjoncteurs
│   ├── governance.py  Gardiennes : risque, budget, capacités, validations, interrupteur d'arrêt
│   ├── reputation.py  Réputation Beta par compétence + calibration (Brier)
│   ├── llm.py         Fournisseurs (offline, Ollama, compatible OpenAI, Claude) + routeur budgété
│   ├── offline.py     Cerveau déterministe et honnête (tests, secours)
│   ├── tools.py       Outils simulables : calc, workspace.read/write, web.fetch, webhook.post
│   ├── media.py       Médias adressés par contenu, ffmpeg (images clés, audio), transcription
│   └── dreaming.py    Rêve : audit, consolidation, propositions (sans auto-modification)
├── roster.py          Les 9 abeilles (aptitudes a priori, outils, préférences de modèles)
├── config.py          Configuration sûre par défaut + assemblage
├── api/server.py      API du Conseil, SSE, sécurité HTTP ; api/static : PWA (sans dépendance)
├── channels/telegram.py  Canal Telegram (long polling, boutons de validation)
└── __main__.py        CLI : serve, mission, status, verify, dream, token
```

## Flux d'une mission

1. **Soumission** (application, Telegram ou CLI). Le triage choisit un profil et le plan d'étapes.
   Les pièces jointes deviennent des cellules `artifact`, toutes contaminées.
2. **Pour chaque étape**, la colonie publie *n* tâches (n > 1 quand il faut de la diversité :
   hypothèses, décision).
3. **Enchères.** Chaque abeille offre `compétence = (1-w)·a priori + w·réputation`, multipliée par sa
   calibration (`w` croît avec les preuves accumulées). Pour une étape « indépendante », une abeille
   n'obtient qu'une tâche, et l'enchère choisit la meilleure offre disponible.
4. **Exécution.** L'abeille reçoit un contexte filtré (sans les sorties de ses pairs si l'étape est
   indépendante) et répond en JSON : `cells`, `needs` (demandes d'aide), `actions`.
5. **Validation du protocole** par la colonie :
   - types autorisés par phase ;
   - références réelles uniquement ;
   - un « fait » sans preuve devient une « hypothèse » ;
   - champs réservés supprimés ;
   - doublons rejetés ;
   - contamination propagée.
6. **Entraide.** Un `need` crée une tâche ENTRAIDE dont le demandeur est exclu. Une `discovery`
   crée une tâche VÉRIFICATION, avec une phéromone proportionnelle à la confiance (la « danse »).
7. **Fin d'étape.** Les crochets s'exécutent une seule fois : vote et quorum après DÉCIDER, veto
   après le contrôle des risques, score après MESURER.
8. **Actions.** Chaque action est simulée (SIMULER), puis évaluée par les Gardiennes. Elle est soit
   exécutée, soit soumise à votre validation, soit bloquée.
9. **Clôture.** La mission reçoit son livrable, sa mesure et ses leçons proposées. Vous recevez une
   notification (Telegram).

## Modèle de données

Tout état est une **cellule** (`Cell`) : `id, kind, author, content, mission_id, data, status,
confidence, phase, skills, parent_id, refs, pheromone, taint, claimed_by`. Le Rayon n'est qu'une
projection des événements `cell.posted`, `cell.updated` et `cell.reinforced` du registre :
`Board(ledger)` reconstruit exactement le même état (testé).

Statuts épistémiques : `fact` (exige des `refs`), `hypothesis`, `unknown`.

## Extension

- **Nouveau modèle** : une entrée `[providers.x]` dans `hive.toml` (Ollama, ou toute API compatible
  OpenAI). Ajoutez ensuite son nom dans `[models]` pour les abeilles qui doivent l'utiliser.
- **Nouvel outil** : sous-classe de `Tool` avec `risk`, `external`, `reversible`, `simulate()` et
  `execute()`. Ajoutez-la au registre, puis à la liste `tools` des abeilles autorisées.
- **Nouvelle abeille** : un `AgentSpec` dans `roster.py`.
- **Nouveau canal** : abonnez-vous à `colony.notifiers` et appelez les méthodes de la colonie
  (comme `channels/telegram.py`).
