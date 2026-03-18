# Crews — Documentation

## Vue d'ensemble

Mnemo est composé de **8 crews spécialisés**, chacun avec un rôle précis. Un "crew" CrewAI est un ensemble d'agents et de tâches qui collaborent séquentiellement.

Tous les crews partagent la même configuration LLM (modèle Ollama, URL API) mais avec des températures différentes selon le besoin de créativité vs déterminisme.

---

## Récapitulatif

| Crew | Déclenché par | Température | Usage |
|------|---------------|-------------|-------|
| `EvaluationCrew` | Chaque message (si ML insuffisant) | 0.0 | Routing sémantique |
| `ConversationCrew` | route = `conversation` | 0.0 / 0.5 | Réponse avec mémoire |
| `ConsolidationCrew` | Fin de session | 0.0 | Mémorisation des faits |
| `CuriosityCrew` | Post-consolidation | 0.0 | Détection des lacunes |
| `ShellCrew` | route = `shell` | 0.0 | Exécution de commandes |
| `BriefingCrew` | Scheduler (cron) | 0.3 | Briefing matinal / hebdo |
| `SchedulerCrew` | route = `scheduler` | 0.0 | Planification en langage naturel |
| `CalendarWriteCrew` | route = `calendar` | 0.0 | CRUD calendrier |
| `NoteWriterCrew` | route = `note` | 0.0 | Écriture directe en mémoire |

---

## EvaluationCrew

**Rôle :** analyser l'intent du message et produire un JSON de routing.

**Agent :** `evaluator` (1 agent, max 2 itérations)

**Prompt :** reçoit le message, l'historique de session, la date actuelle.

**Output :**
```json
{
  "route": "conversation",
  "needs_memory": true,
  "needs_calendar": false,
  "needs_web": false,
  "memory_query": "projet phoenix deadline",
  "web_query": null,
  "needs_clarification": false,
  "clarification_question": null
}
```

**Quand est-il skippé ?** Si le classifieur ML est suffisamment confiant (≥ 0.95, ou ≥ 0.80 avec confirmation keyword).

---

## ConversationCrew

**Rôle :** répondre au message avec le contexte mémoire, calendrier, web.

**Agents :** 2 agents en séquence

### Agent 1 — `memory_retriever`

Collecte tout le contexte nécessaire :
- `get_session_memory` : charge la session courante
- `retrieve_memory` : cherche dans `memory.md` + `doc_chunks` (si `needs_memory`)
- Calendrier pré-chargé (si `needs_calendar`) : injecté directement, pas d'appel supplémentaire
- `web_search` (si `needs_web`)

Output : objet JSON structuré avec `long_term_context`, `calendar_context`, `web_context`, `session_context`.

### Agent 2 — `main_agent`

Génère la réponse finale en langage naturel.

**Règles temporelles (incluses dans le prompt) :**
- Pour les questions **présent/futur** : source primaire = section "Agenda - 7 prochains jours" du contexte temporel. Elle prime sur tout ce qui est dans `memory_context`.
- Pour les questions **passé** : chercher dans `long_term_context` les résumés de sessions de la date concernée.
- `memory_context` ne contient PAS les événements calendrier — ne jamais y chercher le programme d'un jour.

---

## ConsolidationCrew

**Rôle :** extraire les faits importants de la session et les écrire dans `memory.md`.

**Agents :** 2 agents en séquence

1. `session_consolidator` : analyse la transcription de la session, identifie les informations à mémoriser
2. `memory_writer` : met à jour les sections appropriées de `memory.md`, appelle `sync_markdown_to_db()`

**Déclenchement :** automatiquement à la fin de chaque session (après "au revoir" ou Ctrl+C).

**Crash recovery :** si la session n'a pas de marqueur `.done`, elle est re-consolidée au prochain démarrage.

---

## CuriosityCrew

**Rôle :** détecter ce que Mnemo ne sait pas encore et poser des questions ciblées.

**Agents :** 2 agents

**Pipeline en deux phases :**

**Phase 1a — structurelle (Python pur, sans LLM) :** compare `memory.md` au schéma attendu (`MEMORY_SCHEMA`). Si "Nom/Pseudo" est vide → question générée directement.

**Phase 1b — contextuelle (LLM) :** analyse la session pour détecter des informations mentionnées mais non mémorisées. Si tu as parlé d'un nouveau projet → question générée.

**Limites :**
- Max 5 questions par session
- Questions déjà ignorées (table `curiosity_skipped`) ne reviennent pas

---

## ShellCrew

**Rôle :** exécuter des commandes système dans un environnement sandboxé.

**Agent :** `shell_executor` (1 agent, max 8 itérations)

**Outils disponibles :**
- `ShellExecuteTool` : execute une commande whitelistée
- `ReadPdfTool` : lit un PDF par plage de pages
- `FileWriterTool` : écrit un fichier texte sous `/data`

**Sécurité :**
- **Whitelist** : `ls`, `cat`, `find`, `grep`, `head`, `tail`, `wc`, `du`, `stat`, `file`, `diff`, `sort`, `uniq`, `mkdir`, `touch`, `mv`, `cp`, `rm`, `rmdir`, `python`
- **Interdits explicites** : shells (`bash`, `sh`), réseau (`curl`, `wget`, `ssh`), escalade (`sudo`, `chmod`), dangeureux (`rm -rf`, `kill`, `dd`)
- **Pipe autorisé** : `cmd_lecture | cmd_lecture` uniquement
- **Métacaractères interdits** : `&&`, `||`, `;`, backtick, `$(`, `>`, `<`
- **Chemin restreint** : toutes les opérations fichier sous `/data` uniquement
- **Timeout** : 30 secondes par commande
- **Output** : tronqué à 50 KB

**Confirmation obligatoire** : avant tout kickoff, `main.py` affiche la commande et demande une confirmation explicite.

---

## BriefingCrew

**Rôle :** générer `briefing.md` (quotidien) et `weekly.md` (hebdomadaire).

**Agent :** `briefing_agent` (1 agent, température 0.3)

**Déclenché par le scheduler** (pas par le chat) — voir [scheduler.md](scheduler.md).

**Contexte injecté (briefing quotidien) :**
- Événements calendrier du jour
- Résumé de la dernière session
- Highlights mémoire : Projets en cours, Décisions prises, À ne jamais oublier, Profil de base

**Contexte injecté (weekly) :**
- Événements de la semaine passée (lundi → dimanche)
- Liste des sessions de la semaine passée

---

## SchedulerCrew

**Rôle :** transformer une demande en langage naturel en tâches planifiées.

**Agent :** `scheduler_agent` (1 agent)

**Format de sortie (multi-tâches) :**
```json
{
  "tasks": [
    {
      "action": "create",
      "task_type": "recurring",
      "task_action": "briefing",
      "cron_expr": "weekly lundi 08:00",
      "payload": {}
    },
    {
      "action": "create",
      "task_type": "recurring",
      "task_action": "reminder",
      "cron_expr": "weekly lundi 08:00",
      "payload": {"message": "Point projets"}
    }
  ],
  "confirmation_message": "Planifié chaque lundi à 8h."
}
```

Une demande complexe est décomposée en **primitives** (ex : "tous les lundis : briefing + rappel" = 2 tâches distinctes).

Voir [scheduler.md](scheduler.md) pour le détail complet.

---

## CalendarWriteCrew

**Rôle :** créer, modifier ou supprimer des événements dans le fichier `.ics`.

**Agent :** `calendar_writer_agent` (1 agent, température 0.0)

**Limite :** lecture/écriture fichier ICS local uniquement. Les URLs Google Calendar / Nextcloud sont en lecture seule.

Voir [calendar.md](calendar.md) pour le détail complet.

---

## NoteWriterCrew

**Rôle :** écrire immédiatement un fait dans `memory.md`, sans attendre la fin de session.

**Agent :** `note_writer` (1 agent)

**Cas d'usage :** "Mémorise que je préfère les réponses courtes", "Note que le projet Phoenix est en pause".

**Différence avec ConsolidationCrew :** la consolidation attend la fin de session et analyse l'ensemble des échanges. `NoteWriterCrew` écrit immédiatement ce que l'utilisateur lui demande explicitement.

---

## Configuration commune

Tous les crews lisent les mêmes variables d'environnement :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MODEL` | `ollama/mistral` | Modèle Ollama (ex: `ollama/qwen2.5`, `ollama/llama3.2`) |
| `API_BASE` | `http://host.docker.internal:11434` | URL de l'API Ollama |
| `OTEL_SDK_DISABLED` | `true` | Désactive la télémétrie CrewAI |
| `CREWAI_DISABLE_TELEMETRY` | `true` | Désactive les traces CrewAI |
