# Recherche : état de l'art (vérifié le 2026-11-16)

**Méthode et limites.** Deux recherches web ont été menées le 2026-11-16. Le réseau de
l'environnement bloquait la lecture directe de plusieurs pages primaires (spécifications MCP/A2A,
documentation Telegram et Google). Les faits ci-dessous viennent donc souvent des **extraits de
moteur de recherche** des pages citées, pas d'une lecture complète.
**V** = vu aujourd'hui sur la source citée ; **C** = sources contradictoires ; **N** = non vérifié.
Les offres gratuites changent souvent : revérifiez avant de vous y fier.

## 1. Frameworks multi-agents open source

| Framework | Licence | Coordination | Dernière version vue | Lecture pour HIVE |
|---|---|---|---|---|
| LangGraph | MIT | Graphe d'états + points de reprise | 1.2.14 (2026-10-06) [V](https://releases.sh/langchain/langgraph-releases.md) | Excellent `interrupt()` pour l'humain dans la boucle ; bas niveau |
| CrewAI | MIT (+ offre entreprise) | Équipes de rôles + Flows | 1.15.35 (2026-11-03) [V](https://docs.crewai.com/v1.15.35/en/changelog.md) | Rapide à prototyper ; peu de contrôle sur la communication |
| Microsoft Agent Framework (ex-AutoGen) | MIT (N) | Agents + workflows en graphe | 1.0 GA avril 2026 [V](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx) ; AutoGen en maintenance | Solide, gravité Azure |
| OpenAI Agents SDK | MIT | Passations, agents-outils, garde-fous | 0.24.0 (2026-10-15) [V](https://www.developersdigest.tech/blog/openai-agents-sdk-whats-new-2026) | `needs_approval` ; pas encore 1.0 |
| Google ADK | Apache-2.0 | Workflow Runtime en graphe (2.0) | GA Python 2026-05-19 [V](https://adk.dev/2.0/) | Natif A2A ; orienté Gemini |
| AgentScope | Apache-2.0 (N) | **MsgHub (diffusion) + pipelines** | 2.0.7 (2026-08-24) [V](https://docs.agentscope.io/versions/2.0.7/en/release-notes.md) | Le plus proche d'un blackboard parmi les grands |
| Agno | Apache-2.0 depuis fév. 2026 [V](https://www.agno.com/blog/community-roundup-february-2026) | Équipes + workflows + AgentOS | 3.x | Rapide ; poussée plateforme |
| Pydantic AI | MIT (N) | Agent unique d'abord ; graphe | 2.0.0 (2026-06-23) [V](https://pydantic.dev/docs/ai/project/changelog/) | Typage fort ; multi-agents mince |
| smolagents | Apache-2.0 (N) | Agent « code comme action » | 1.26.0 [V](https://www.developersdigest.tech/agent-frameworks/smolagents) | Minimal ; risque d'exécution de code |
| Letta (MemGPT) | Apache-2.0 | Agents à état, mémoire partagée, agents « sleep-time » | versions contradictoires (C) | Meilleur modèle de mémoire ; orientation incertaine |
| MetaGPT / ChatDev / CAMEL | MIT / Apache-2.0 | SOP / chaînes de chat / jeux de rôle | activité 2026 faible ou incertaine | Idées utiles (SOP), maintenance incertaine |
| Mastra (TypeScript) | Apache-2.0 + `ee/` | Agents + workflows | 1.0 (janv. 2026) [V](https://mastra.ai/blog/announcing-mastra-1) | Si un jour HIVE passe en TypeScript |

**Pourquoi ne pas avoir pris un de ces frameworks :** aucun n'offre nativement le trio *tableau
unique + gouvernance déterministe + registre rejouable*, et tous ajoutent une surface de dépendances.
Le noyau HIVE fait moins de 3 000 lignes de Python, lisibles et auditables, avec seulement 4 dépendances. Un adaptateur vers LangGraph ou
ADK reste possible plus tard.

**Avertissements 2026 directement pertinents :**
- **OpenClaw**, un agent personnel auto-hébergé piloté depuis des messageries, c'est-à-dire exactement
  le modèle « pilotage par téléphone ». Il a connu un détournement WebSocket (CVE-2026-25253, CVSS 8.8),
  de l'injection indirecte (CVE-2026-22708) et des compétences malveillantes sur sa place de marché ;
  les instances exposées sont passées de 679 à 31 674 en 12 jours
  [V](https://www.techtarget.com/cybersecurity/tip/The-OpenClaw-security-risks-every-CISO-needs-to-know?amp=1).
  → HIVE écoute en local, n'ouvre aucun port pour Telegram et n'a **pas** de place de marché de compétences.
- **Moltbook**, un réseau social réservé aux agents. On y a observé de l'injection de prompt d'un bot à
  l'autre [V](https://securityweek.com/security-analysis-of-moltbook-agent-network-bot-to-bot-prompt-injection-and-data-leaks/).
  → Sur le Rayon, une sortie dérivée d'une donnée contaminée reste contaminée.

## 2. Protocoles d'interopérabilité

- **Agentic AI Foundation (Linux Foundation)**, lancée le 2025-12-09 avec MCP, goose et AGENTS.md ;
  A2A l'a rejointe le 2026-08-17 [V](https://aaif.io/blog/a2a-joins-aaif).
- **MCP** : spécification courante **2026-07-28**, sans état (plus de poignée de main `initialize`,
  nouveau `server/discover`) [V](https://workos.com/blog/mcp-stateless-spec-2026-07-28).
  → Prévu pour HIVE : un client MCP pour brancher des outils standard (jour 3).
- **A2A v1.0** : Agent Cards signées (JWS + JCS), en-tête `A2A-Version`
  [V](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year).
  L'ACP d'IBM a fusionné dans A2A (août 2025). → Prévu : exposer la ruche comme un agent A2A (jour 6).
- **Agent Skills (SKILL.md)**, standard ouvert depuis le 2025-12-18
  [V](https://codex.danielvaughan.com/2026/05/05/agent-skills-open-standard-portable-skills-codex-cli-cross-agent/).
  → Les leçons validées de HIVE pourront être exportées dans ce format.
- **AG-UI** (flux d'événements agent → interface) : à suivre pour l'application mobile.

## 3. Ce qui marche vraiment : preuves contre effet de mode

| Approche | Preuve | Source |
|---|---|---|
| Multi-agents en général | Toutes les variantes multi-agents **perdent 39 à 70 %** sur les tâches séquentielles et gagnent **+81 %** sur le parallélisable ; les agents indépendants amplifient les erreurs jusqu'à **×17,2** ; le centralisé contient mieux | [Kim et al. (Google/MIT)](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/) |
| Coût | Environ **15× les jetons** d'un chat ; +90 % sur l'évaluation interne d'Anthropic | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Blackboard | +4,3 % vs CoT, +5 % vs multi-agents statique, moins de jetons ; le volontariat sur blackboard bat l'attribution maître/esclave | [2507.01701](https://arxiv.org/abs/2507.01701), [2510.01285](https://arxiv.org/abs/2510.01285v2) |
| Débat | Ne bat pas l'auto-cohérence ; le vote majoritaire explique l'essentiel des gains | [Smit et al.](https://proceedings.mlr.press/v235/smit24a.html), [Choi et al.](https://arxiv.org/abs/2508.17536v1) |
| Vote | +13,2 % en raisonnement ; **plus de tours de discussion avant le vote dégradent le résultat** | [Kaesberg et al.](https://aclanthology.org/2025.findings-acl.606) |
| Stigmergie LLM | Une seule étude, non relue par des pairs, aux résultats qui varient selon les versions | [2601.08129](https://arxiv.org/abs/2601.08129) |
| Marché / enchères | Petites expériences seulement | [2506.01900](https://arxiv.org/pdf/2506.01900) |
| Quorum (abeilles) | Biologie solide ([List et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC2689716)) ; **aucune étude LLM trouvée** | — |
| Calcul « en sommeil » | ~5× moins de calcul à précision égale, si les requêtes sont prévisibles | [2504.13171](https://arxiv.org/abs/2504.13171) |
| Bibliothèque de compétences (Voyager) | Sans elle, l'agent plafonne (Minecraft, succès vérifiable) | [2305.16291](https://arxiv.org/pdf/2305.16291) |
| Pannes multi-agents (MAST) | 14 modes de panne : spécification, désalignement entre agents, vérification | [2503.13657](https://arxiv.org/html/2503.13657v2) |

**Conséquences directes dans HIVE :** triage par profil (Kim) ; propositions **indépendantes** puis vote,
sans débat (Choi, Kaesberg) ; blackboard (bMAS) ; critères d'acceptation explicites et étape MESURER (MAST) ;
le quorum, les phéromones et la calibration sont traités comme des **hypothèses à mesurer**.

## 4. Sécurité des agents

- **CaMeL** : séparer le flux de contrôle du flux de données, avec des capacités. Sécurité prouvée sur
  77 % des tâches AgentDojo (84 % sans défense) [V](https://arxiv.org/abs/2503.18813).
- **Six patrons de conception** contre l'injection : planifier puis exécuter, map-reduce, double LLM…
  [V](https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/).
- **« Agents Rule of Two » (Meta)** : jamais plus de deux parmi *entrée non fiable*, *données sensibles*,
  *capacité d'agir ou de communiquer à l'extérieur* [V](https://ai.meta.com/blog/practical-ai-agent-security/).
- **OWASP Top 10 for Agentic Applications 2026** (ASI01–ASI10) ; ASI06 *empoisonnement de mémoire*,
  ASI07 *communication inter-agents non sûre* et ASI08 *pannes en cascade* visent directement une architecture
  à tableau partagé [V](https://www.speakeasy.com/blog/owasp-agentic-top-10-explained). Voir [SECURITY.md](SECURITY.md).
- « Un clic d'approbation n'est pas une politique » : il faut des règles déterministes par outil,
  argument et identité [V](https://arcjet.com/learn/needsapproval-and-interrupt-are-not-a-policy).

## 5. Une pile à 0 € : les faits

**Modèles locaux (Ollama / llama.cpp)**
- **Gemma 4** (Apache 2.0) : texte et image sur toutes les tailles, **audio sur E2B/E4B** ; téléchargements
  d'environ 7 à 10 Go pour e4b [V](https://ollama.com/library/gemma4).
- **Ministral 3** (3B/8B/14B, Apache 2.0, éditeur français) : vision sur toutes les tailles [V](https://mistral.ai/news/mistral-3/).
- **Qwen3-VL** : vision, outils, 256K de contexte [V](https://ollama.com/library/qwen3-vl).
- Règle approximative (N) : ~0,6 Go par milliard de paramètres en 4 bits. 8 Go de RAM suffisent pour
  3-4B, 16 Go pour 8-12B.

**API gratuites (à n'utiliser qu'avec votre accord)**
- **Gemini** : modèles Flash gratuits ; quotas visibles seulement dans AI Studio et parfois réduits (C).
  Pour les utilisateurs de l'EEE, les conditions des services payants (pas d'entraînement sur vos données)
  s'appliquent aussi au gratuit [V](https://ai.google.dev/gemini-api/terms). Point d'accès compatible OpenAI
  (`/v1beta/openai/`). → **Meilleur secours cloud** pour un usage personnel depuis la France.
- **Groq** : sans carte (C), pas de rétention par défaut [V](https://console.groq.com/docs/your-data),
  compatible OpenAI, transcription Whisper.
- **OpenRouter `:free`** : 50 requêtes par jour ; beaucoup de modèles gratuits **journalisent vos
  requêtes pour l'entraînement** [V](https://openrouter.ai/docs/guides/privacy/provider-logging). À éviter
  pour tout contenu sensible.
- **À éviter** : GitHub Models (**retiré le 2026-07-30**) [V](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/) ;
  Cerebras (moyen de paiement exigé, C) ; Mistral La Plateforme (documentation contradictoire, C).

**Voix**
- Transcription : faster-whisper (MIT, CPU int8) [V](https://github.com/SYSTRAN/faster-whisper) ;
  Parakeet TDT v3 (25 langues européennes) ; Voxtral (Apache 2.0).
- Synthèse vocale : Kokoro-82M (Apache 2.0, voix française `ff_siwis`) ; Piper (désormais GPL-3.0) ;
  ou la synthèse intégrée du téléphone (utilisée par HIVE, 0 €, aucune donnée envoyée).
- La dictée du navigateur (Web Speech API) envoie l'audio aux serveurs du navigateur sous Chrome
  [V](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API).

**Vidéo** : images clés par ffmpeg (`select='gt(scene,0.4)'`) + modèle de vision + transcription de la
bande son. C'est l'approche implémentée.

**Téléphone**
- **Telegram** : bots gratuits, téléchargement jusqu'à 20 Mo, envoi jusqu'à 50 Mo, boutons en ligne
  (`callback_data` ≤ 64 octets) [V](https://core.telegram.org/bots/api). Le long polling n'ouvre aucun
  port. **Pas de chiffrement de bout en bout.**
- **PWA** : notifications Web Push sur iOS seulement pour une application ajoutée à l'écran d'accueil
  (iOS 16.4+) ; micro et caméra exigent HTTPS ou localhost [V](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
- **Tailscale Personal** : 0 €, sans carte, usage non commercial [V](https://tailscale.com/pricing).
  **Cloudflare Quick Tunnel** : sans compte, mais **ne transmet pas les Server-Sent Events** ; HIVE
  bascule alors automatiquement en interrogation régulière [V](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
- **ntfy.sh** : 250 messages par jour en gratuit [V](https://docs.ntfy.sh/config/).

**Hébergement permanent**
- Le plus sûr à 0 € : **votre propre machine**.
- Oracle Always Free exige une carte, et son allocation Arm a été divisée par deux en 2026
  [V](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
  Cela demande votre autorisation explicite.
- Un vieux téléphone Android sous Termux est possible, mais le « phantom process killer » d'Android 12+
  tue les processus en arrière-plan (N).
