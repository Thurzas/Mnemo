# Scheduler — Documentation

## Vue d'ensemble

Le système de scheduling de Mnemo est composé de **deux couches distinctes** :

1. **`SchedulerCrew`** — interactif, s'execute dans la session utilisateur, *cree* des taches
2. **`scheduler.py`** — service Docker background, tourne en boucle, *execute* les taches

Ces deux couches communiquent via la table SQLite `scheduled_tasks` et le module `tools/scheduler_tasks.py`.

---

## Couche 1 : `SchedulerCrew` (interactif)

Intervient quand le router detecte une intention `scheduler` (via keywords, ML ou LLM).

### Pipeline

```
User: "rappelle-moi demain a 9h de relire mes notes avant la réunion."
  -> router detecte intent scheduler (ML/keywords)
  -> SchedulerCrew.run() :
      1. Recupere les taches pending existantes (contexte pour l'agent)
      2. kickoff() -> LLM transforme en JSON structure :
         {
           "action": "create",
           "task_type": "one_shot",
           "task_action": "reminder",
           "trigger_at": "2026-03-07T09:00:00",
           "payload": { "message": "relire mes notes" },
           "confirmation_message": "..."
         }
      3. Parse le JSON, cree la tache en DB via create_task()
      4. Retourne le confirmation_message a l'utilisateur
```

### Actions supportees

| `action` JSON | Comportement |
|---------------|-------------|
| `"create"` | Cree une tache (one_shot ou recurring) |
| `"cancel"` | Annule une tache par `task_id_to_cancel` |

### Gestion des erreurs

- JSON malformed -> message d'erreur en langage naturel, pas d'exception
- `cancel` sur un ID inexistant -> message explicite
- Fences markdown dans le output LLM -> strippees avant parse

---

## Couche 2 : `scheduler.py` (service background)

Service Docker separe (`mnemo-scheduler`). Boucle toutes les 60 secondes.

### Demarrage

```bash
docker compose up -d mnemo-scheduler               # Service en arriere-plan
docker compose run --rm mnemo-scheduler --now briefing   # Declenchement immediat
docker compose run --rm mnemo-scheduler --now weekly
docker compose run --rm mnemo-scheduler --now deadline
docker compose run --rm mnemo-scheduler --now all
```

### Bootstrap au demarrage — 3 taches systeme fixes

| ID | Cron | Action | Output |
|----|------|--------|--------|
| `sys_briefing` | `daily HH:MM` (env `BRIEFING_TIME`, defaut `07:30`) | `action_briefing()` | `briefing.md` |
| `sys_weekly` | `weekly lundi HH:MM` (env `WEEKLY_TIME`, defaut `08:00`) | `action_weekly()` | `weekly.md` |
| `sys_deadline_scan` | `daily 07:00` | `action_deadline_alert()` | Injecte dans `briefing.md` |

Bootstrap idempotent : `INSERT OR REPLACE`, un double demarrage ne cree pas de doublons.

### Boucle principale (tick 60s)

```
get_due_tasks()  -> taches pending dont next_run <= now
  -> dispatch(task) -> action correspondante
  -> one_shot    : mark_done()
  -> recurring/system : reschedule() -> recalcule next_run
  -> erreur      : mark_error(task_id, message)
```

### Les 4 actions concretes

#### `action_briefing()`
- Recupere les evenements calendrier du jour
- Recupere le resume de la derniere session
- Recupere les highlights memoire (sections cibles : Projets en cours, Decisions prises, A ne jamais oublier, Profil de base)
- Appelle `BriefingCrew.kickoff()` avec tout le contexte
- Ecrit le resultat dans `briefing.md`
- En cas d'echec : ecrit un fichier de fallback minimaliste

#### `action_weekly()`
- Meme crew (`BriefingCrew`), contexte different
- Collecte les evenements de la semaine passee (lundi -> dimanche)
- Liste les sessions de la semaine passee
- Ecrit dans `weekly.md`

#### `action_deadline_alert()`
- Lit le calendrier sur 4 jours (`get_upcoming_events(days=4)`)
- Filtre les evenements a J-1 et J-3
- Injecte un bloc `## Alertes deadlines` dans `briefing.md` existant
- Si `briefing.md` n'existe pas encore : le cree avec les alertes seules

#### `action_reminder(payload)`
- Injecte un bloc `## Rappel` dans `briefing.md`
- Si `briefing.md` n'existe pas : le cree
- `payload.message` contient le texte du rappel (fourni par l'utilisateur via `SchedulerCrew`)

---

## Couche 3 : Boucle d'autonomie GOAP

Le scheduler intègre une troisième couche qui s'exécute en parallèle de la boucle principale (tick 60s) : `_goap_autonomy_tick()`. Elle pilote l'avancement automatique des projets sandbox `in_progress`.

### `_goap_autonomy_tick()`

Appelé à chaque tick. Scanne tous les projets avec `status=in_progress` dans `users/*/projects/*/project.json`.

Pour chaque projet trouvé :
1. Charge `plan.md` et liste les étapes non cochées (`- [ ] ...`)
2. Appelle `_advance_project(project, step)` sur la première étape non cochée
3. Si toutes les étapes sont cochées → marque le projet `done`

### `_advance_project(project, step_text)`

Évalue les préconditions GOAP pour l'étape en cours :
1. Interroge le HP-KG (`goap/planner.py`) pour déterminer l'action GOAP correspondant à l'étape
2. Consulte `world_state.json` pour vérifier si les préconditions sont satisfaites
3. Dispatch selon le type d'action :

| Type d'action | Comportement |
|---------------|-------------|
| `sandbox_read`, `web_search`, `memory_read` | Exécution automatique sans confirmation |
| `sandbox_shell`, `npm`, `pip`, `python`, `file_write` | Ajout dans `pending_confirmations` |
| Inconnu / préconditions manquantes | Loggé dans `logs/commands.log`, étape ignorée ce tick |

### `pending_confirmations` — format et déduplication

Les actions risquées sont écrites dans `users/<username>/world_state.json` sous la clé `pending_confirmations` :

```json
{
  "pending_confirmations": [
    {
      "id": "proj_waifuclawd_step_2_1711234567",
      "project_slug": "waifuclawd",
      "step": "Installer les dépendances npm",
      "action": "sandbox_shell",
      "command": "npm install",
      "created_at": "2026-03-20T10:30:00"
    }
  ]
}
```

**Déduplication :** avant d'ajouter une entrée, le scheduler vérifie que l'`id` (construit depuis `<slug>_step_<n>_<timestamp>`) n'est pas déjà présent. Une confirmation en attente pour la même étape ne génère pas de doublon.

**Consommation :** l'API REST (`GET /api/confirmations`, `POST /api/confirmations/{id}`) expose ces entrées au dashboard. L'approbation déclenche l'exécution réelle de la commande dans le projet ; le rejet supprime l'entrée sans exécution.

Voir [sandbox.md](sandbox.md) pour la documentation complète des confirmations.

---

## Couche transverse : `tools/scheduler_tasks.py` (CRUD + mirror)

### Format cron simplifie

| Format | Exemple | Usage |
|--------|---------|-------|
| `"daily HH:MM"` | `"daily 07:30"` | Taches systeme quotidiennes |
| `"weekly WEEKDAY HH:MM"` | `"weekly lundi 08:00"` | Taches hebdomadaires |
| `None` (one_shot) | — | `trigger_at` ISO datetime utilise a la place |

### `compute_next_run(task_type, cron_expr, trigger_at, from_dt=None)`

Calcule le prochain `datetime` d'execution :
- `one_shot` : retourne `trigger_at` converti en datetime
- `daily HH:MM` : aujourd'hui a l'heure cible si pas encore passee, sinon demain
- `weekly WEEKDAY HH:MM` : prochain jour de semaine cible a l'heure cible

### Cycle de vie d'une tache

```
pending -> (execution) -> done         (one_shot)
        -> (execution) -> pending      (recurring/system, next_run recalcule)
        -> (erreur)    -> error
        -> (annulation) -> cancelled
```

### API CRUD

| Fonction | Description |
|----------|-------------|
| `create_task(id, type, action, payload, trigger_at, cron_expr)` | Cree ou remplace (INSERT OR REPLACE) |
| `get_due_tasks(now=None)` | Taches pending dont `next_run <= now` |
| `mark_done(task_id)` | Passe en `done`, met `last_run` |
| `mark_error(task_id, error_msg)` | Passe en `error`, stocke le message (500 chars max) |
| `reschedule(task_id, cron_expr)` | Recalcule `next_run`, repasse en `pending` |
| `cancel_task(task_id)` | Passe en `cancelled`, retourne `bool` (False si deja termine) |
| `list_tasks(status=None)` | Liste toutes les taches, ou filtrees par status |

### Mirror `tasks.md`

Regenere apres chaque operation CRUD. Structure :

```markdown
# Taches planifiees

## Systeme
- [recycler] 2026-03-07 07:30 — Morning briefing quotidien

## Recurrentes
- [recycler] 2026-03-09 08:00 — Resume hebdomadaire

## One-shot
- [ ] 2026-03-07 09:00 — Faire ma declaration
```

Icones : `[ ]` pending, `[x]` done, `[~]` cancelled, `[!]` error, `[recycler]` recurring/system.

---

## Schema SQLite (`scheduled_tasks`)

```sql
id          TEXT PRIMARY KEY
type        TEXT    -- one_shot | recurring | system
action      TEXT    -- briefing | weekly | deadline_alert | reminder
payload     TEXT    -- JSON
trigger_at  TEXT    -- ISO datetime (one_shot)
cron_expr   TEXT    -- "daily HH:MM" ou "weekly lundi HH:MM"
status      TEXT    -- pending | done | error | cancelled
created_at  TEXT    -- ISO datetime
next_run    TEXT    -- ISO datetime (null si one_shot sans trigger_at)
last_run    TEXT    -- ISO datetime
error_msg   TEXT    -- message d'erreur (500 chars max)
```

---

## Variables d'environnement

| Variable | Defaut | Description |
|----------|--------|-------------|
| `DATA_PATH` | `/data` | Repertoire des donnees (DB, MD, sessions) |
| `BRIEFING_TIME` | `07:30` | Heure du briefing quotidien |
| `WEEKLY_TIME` | `08:00` | Heure du resume hebdomadaire (lundi) |
| `MODEL` | — | Modele Ollama utilise par les crews |
| `API_BASE` | — | URL de l'API Ollama |

---

## Fichiers produits

| Fichier | Producteur | Contenu |
|---------|-----------|---------|
| `/data/briefing.md` | `action_briefing()` + `action_deadline_alert()` + `action_reminder()` | Briefing du jour, alertes, rappels |
| `/data/weekly.md` | `action_weekly()` | Resume de la semaine passee |
| `/data/tasks.md` | `_sync_tasks_md()` (apres chaque CRUD) | Miroir humain des taches planifiees |


---
## Pipeline:

```
User: "tous les lundis à 8h, briefing + rappelle-moi de faire mon point projets"
  │
  ├─ router (keywords / ML / LLM) ──────────────────────► route = scheduler
  │
  └─ SchedulerCrew.run()
       │
       ├─ 1. list_tasks(status="pending")  ──────────────► contexte tâches existantes
       │
       ├─ 2. kickoff(user_message, temporal_context,
       │             existing_tasks)
       │       └─ LLM décompose en tâches primitives
       │           └─ retourne JSON :
       │               {
       │                 "tasks": [
       │                   { "action": "create",
       │                     "task_type": "recurring",
       │                     "task_action": "briefing",
       │                     "cron_expr": "weekly lundi 08:00",
       │                     "payload": {} },
       │                   { "action": "create",
       │                     "task_type": "recurring",
       │                     "task_action": "reminder",
       │                     "cron_expr": "weekly lundi 08:00",
       │                     "payload": {"message": "Point projets"} }
       │                 ],
       │                 "confirmation_message": "Planifié chaque lundi à 8h."
       │               }
       │
       ├─ 3. parse JSON  (strip fences, json.loads)
       │
       ├─ 4. pour chaque tâche dans tasks[] :
       │       ├─ action = "create"  ──► create_task()  ──► INSERT scheduled_tasks
       │       │                                         └─► _sync_tasks_md()
       │       └─ action = "cancel"  ──► cancel_task()  ──► UPDATE status=cancelled
       │                                                 └─► _sync_tasks_md()
       │
       └─ 5. retourne confirmation_message  ─────────────► affiché à l'utilisateur


─── plus tard, scheduler.py (boucle 60s) ──────────────────────────────────────

  get_due_tasks()  ──► tâches pending dont next_run ≤ now
    │
    ├─ action = briefing       ──► BriefingCrew  ──► briefing.md
    ├─ action = weekly         ──► BriefingCrew  ──► weekly.md
    ├─ action = deadline_alert ──► calendrier    ──► injecte dans briefing.md
    └─ action = reminder       ──► payload.msg   ──► injecte dans briefing.md
         │
         ├─ one_shot   ──► mark_done()
         └─ recurring  ──► reschedule()  ──► next_run recalculé
```
---

## Couverture de tests (etat au 06 mars 2026)

Aucun fichier de test dedie. Tests a ecrire :

### Niveau 1 — unitaires purs (sans LLM, sans DB)
- `compute_next_run()` : daily/weekly/one_shot, heure passee/future, jour courant
- `_fmt_task_line()` : formatage par status et type
- `_strip_fences()` du scheduler.py

### Niveau 2 — integration DB (SQLite temporaire)
- `create_task()` : insertion + `next_run` calcule
- `get_due_tasks()` : filtre correct sur `next_run <= now`
- `mark_done()`, `mark_error()`, `reschedule()` : transitions de status
- `cancel_task()` : retour `True`/`False`
- `bootstrap_system_tasks()` : idempotence (double appel = pas de doublon)
- `_sync_tasks_md()` : contenu et structure du fichier genere

### Niveau 3 — integration crew (LLM mocke au niveau kickoff)
- `SchedulerCrew.run()` creation one_shot -> verifier appel `create_task()`
- `SchedulerCrew.run()` avec `action: "cancel"` -> verifier appel `cancel_task()`
- `SchedulerCrew.run()` avec JSON malformed -> message d'erreur propre
- `dispatch()` : mock des actions -> verifier branchement par action type
