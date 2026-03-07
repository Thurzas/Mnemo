# 🛠️ Mnemo — Guide de dépannage

Ce document recense les problèmes rencontrés lors de l'installation et de l'utilisation de Mnemo, avec leurs causes et solutions.

---

## Sommaire

1. [Docker ne démarre pas](#1-docker-ne-démarre-pas)
2. [LiteLLM — "Fallback to LiteLLM is not available"](#2-litellm--fallback-to-litellm-is-not-available)
3. [LiteLLM — bruit "No module named fastapi"](#3-litellm--bruit-no-module-named-fastapi)
4. [Fichier de config introuvable (KeyError dans crew.py)](#4-fichier-de-config-introuvable-keyerror-dans-crewpy)
5. [Fichier de config mal nommé](#5-fichier-de-config-mal-nommé)
6. [unable to open database file](#6-unable-to-open-database-file)
7. [Événements récurrents absents du calendrier](#7-événements-récurrents-absents-du-calendrier)
8. [Maximum iterations reached sur les questions calendrier](#8-maximum-iterations-reached-sur-les-questions-calendrier)
9. [Read-only file system — uncertain_cases.jsonl](#9-read-only-file-system--uncertain_casesjsonl)
10. [briefing.md créé dans data/data/ au lieu de data/](#10-briefingmd-créé-dans-datadata-au-lieu-de-data)
11. [Le scheduler ne trouve pas Mnemo.scheduler](#11-le-scheduler-ne-trouve-pas-mnemoscheduler)
12. [Dockerfile.scheduler introuvable au build](#12-dockerfilescheduler-introuvable-au-build)
13. [Template variable 'label' not found](#13-template-variable-label-not-found)
14. [joblib manquant — router ML silencieux](#14-joblib-manquant--router-ml-silencieux)
15. [Quand rebuilder ?](#15-quand-rebuilder-)

---

## 1. Docker ne démarre pas

**Symptôme**
```
docker: Cannot connect to the Docker daemon
```
ou
```
Error response from daemon: No such image
```

**Causes et solutions**

- **Docker Desktop non lancé** (WSL2) : lance Docker Desktop sur Windows, attends l'icône verte dans la barre des tâches, puis relance.
- **Intégration WSL2 non activée** : Docker Desktop → Settings → Resources → WSL Integration → active ta distro.
- **Image jamais buildée** : lance `./install.sh` ou `docker compose build`.

---

## 2. LiteLLM — "Fallback to LiteLLM is not available"

**Symptôme**
```
LiteLLM is not available, falling back to LiteLLM
ImportError: Fallback to LiteLLM is not available
```
Mnemo crashe dès le premier message.

**Cause**
`litellm[proxy]` dans `requirements.txt` installe des dépendances qui entrent en conflit avec CrewAI. La version `[proxy]` ne doit pas être utilisée.

**Solution**
Vérifier que `requirements.txt` contient bien `litellm` (sans suffixe) :
```
litellm
```
et **non** :
```
litellm[proxy]     ← NE PAS UTILISER
```
Puis rebuilder :
```bash
docker compose build --no-cache mnemo && ./mnemo.sh
```

---

## 3. LiteLLM — bruit "No module named fastapi"

**Symptôme**
```
LiteLLM:ERROR: Error creating standard logging object - Missing dependency No module named 'fastapi'
```
Le message apparaît plusieurs fois mais **Mnemo fonctionne normalement**.

**Cause**
LiteLLM tente d'initialiser son module proxy interne pour les logs. Ce module requiert `fastapi`, qui n'est pas installé (et ne doit pas l'être — voir point 2). C'est du bruit sans conséquence fonctionnelle.

**Solution**
`patch_crewai_tracing.py` est exécuté au build et patche ce comportement. Si le bruit réapparaît après un `--no-cache`, vérifier que `patch_crewai_tracing.py` est bien à la racine du projet (à côté de `Dockerfile`) et que le `Dockerfile` l'exécute bien :
```dockerfile
COPY patch_crewai_tracing.py /tmp/patch_crewai_tracing.py
RUN python3 /tmp/patch_crewai_tracing.py
```

---

## 4. Fichier de config introuvable (KeyError dans crew.py)

**Symptôme**
```
WARNING: File not found: /app/src/Mnemo/config/scheduler_tasks_config.yaml
KeyError: 'schedule_task'
```

**Cause**
Les fichiers YAML de configuration des nouveaux crews (`scheduler_agents.yaml`, `scheduler_tasks_config.yaml`, `briefing_agents.yaml`, `briefing_tasks.yaml`) ne sont pas dans `src/Mnemo/config/` sur le disque hôte. Docker copie `src/` → `/app/src/` au build — si les fichiers sont absents sur le disque, ils sont absents dans l'image.

**Solution**
Vérifier la présence des fichiers :
```bash
ls src/Mnemo/config/ | grep -E "scheduler|briefing"
# attendu :
# briefing_agents.yaml
# briefing_tasks.yaml
# scheduler_agents.yaml
# scheduler_tasks_config.yaml
```
Placer les fichiers manquants dans `src/Mnemo/config/`, puis rebuilder :
```bash
docker compose build mnemo && ./mnemo.sh
```

---

## 5. Fichier de config mal nommé

**Symptôme**
Même erreur que le point 4, malgré la présence des fichiers dans `src/Mnemo/config/`.

**Cause**
Un fichier est mal nommé. Exemple rencontré :
```
scheduler_task.yaml          ← manque le "s"
scheduler_task_config.yaml   ← manque le "s"
```
alors que `crew.py` attend :
```
scheduler_tasks_config.yaml  ← avec "s"
```

**Solution**
Renommer les fichiers pour qu'ils correspondent exactement aux chemins déclarés dans `crew.py` :
```bash
cd src/Mnemo/config/
mv scheduler_task.yaml scheduler_tasks.yaml
mv scheduler_task_config.yaml scheduler_tasks_config.yaml
```
Puis rebuilder.

---

## 6. unable to open database file

**Symptôme**
```
Agent > Erreur lors de la création de la tâche : unable to open database file
```

**Causes possibles**

**A — Table `scheduled_tasks` inexistante**
La migration n'a pas été jouée. Lancer :
```bash
docker compose run --rm mnemo init_db
```

**B — Chemin de la DB incorrect dans `scheduler_tasks.py`**
`scheduler_tasks.py` utilise un chemin absolu (`/data/memory.db`) alors que le WORKDIR Docker est `/data`. Si `DATA_PATH` n'est pas défini, le fichier est cherché au mauvais endroit.

La bonne pratique — identique à `memory_tools.py` :
```python
DB_PATH  = Path("memory.db")   # relatif au WORKDIR /data
TASKS_MD = Path("tasks.md")
```

---

## 7. Événements récurrents absents du calendrier

**Symptôme**
Mnemo n'affiche que 2-3 événements au lieu du planning complet. Les événements récurrents (cours hebdomadaires, réunions fixes) sont absents.

**Cause**
`get_upcoming_events()` lisait `DTSTART` brut. Les événements avec `RRULE:FREQ=WEEKLY` ont un `DTSTART` en septembre (date de création) — jamais dans la fenêtre "aujourd'hui+14j".

**Solution**
S'assurer d'avoir la version à jour de `tools/calendar_tools.py` qui contient `_expand_rrule()`. Vérifier :
```bash
grep -n "_expand_rrule" src/Mnemo/tools/calendar_tools.py
# doit retourner une ligne
```

---

## 8. Maximum iterations reached sur les questions calendrier

**Symptôme**
```
Task failed: Maximum Iterations reached
```
Sur toutes les questions portant sur le calendrier ("qu'est-ce que j'ai demain", "prochaine session X").

**Cause**
`memory_retriever` (max_iter=8) appelait `GetCalendarTool` en interne → reformatait le JSON → retentait → explosait avant de répondre.

**Solution**
Le calendrier doit être pré-fetché dans `main.py` avant le kickoff CrewAI et injecté dans `base_inputs` comme `calendar_context`. Vérifier dans `main.py` :
```python
if eval_json.get("needs_calendar"):
    cal_events = get_upcoming_events(days=21)
    calendar_context = format_events_for_prompt(cal_events)
```

---

## 9. Read-only file system — uncertain_cases.jsonl

**Symptôme**
```
[DBG] log uncertain echoue : [Errno 30] Read-only file system: '/app/src/Mnemo/uncertain_cases.jsonl'
```

**Cause**
`_UNCERTAIN_LOG` pointait vers `/app/src/Mnemo/` — monté read-only dans Docker. Même problème pour `router_model.joblib` et `training_data.jsonl`.

**Solution**
Ces fichiers doivent pointer vers le WORKDIR `/data` (relatif). Vérifier dans `main.py` :
```python
_UNCERTAIN_LOG = Path("uncertain_cases.jsonl")  # relatif à WORKDIR=/data
```
Et dans `train_router.py` / `retrain_router.py` / `generate_training_data.py` :
```python
_DATA = Path(os.getenv("DATA_PATH", ".")).resolve()
OUTPUT = _DATA / "router_model.joblib"
```

---

## 10. briefing.md créé dans data/data/ au lieu de data/

**Symptôme**
```
waifuclawd/
└── data/
    └── data/
        └── briefing.md   ← mauvais emplacement
```

**Cause**
`DATA_PATH=./data` dans `.env` est résolu depuis le WORKDIR `/data` → `/data/data`.

**Solution**
Forcer `DATA_PATH=/data` dans `docker-compose.yml` pour le service `mnemo-scheduler` :
```yaml
environment:
  - HOME=/tmp
  - DATA_PATH=/data
```

---

## 11. Le scheduler ne trouve pas Mnemo.scheduler

**Symptôme**
```
/usr/local/bin/python: No module named Mnemo.scheduler
```

**Cause**
`scheduler.py` n'est pas dans `src/Mnemo/` — il doit s'y trouver pour que `python -m Mnemo.scheduler` fonctionne.

**Solution**
```bash
# Vérifier
ls src/Mnemo/scheduler.py

# Si absent, placer le fichier au bon endroit
mv scheduler.py src/Mnemo/scheduler.py
```
Puis rebuilder le scheduler :
```bash
docker compose build mnemo-scheduler
```

---

## 12. Dockerfile.scheduler introuvable au build

**Symptôme**
```
failed to solve: failed to read dockerfile: open Dockerfile.scheduler: no such file or directory
```

**Cause**
`Dockerfile.scheduler` doit être à la racine du projet (là où se trouve `docker-compose.yml`), pas dans un sous-dossier.

**Solution**
```bash
# Structure attendue à la racine :
ls | grep -E "Dockerfile|docker-compose"
# Dockerfile
# Dockerfile.scheduler
# docker-compose.yml
```
Déplacer le fichier si nécessaire, puis rebuilder.

---

## 13. Template variable 'label' not found

**Symptôme**
```
⚠️ Analyse contextuelle ignorée : Missing required template variable
'Template variable 'label' not found in inputs dictionary'
```

**Cause**
CrewAI parse les descriptions de tâches YAML et tente d'injecter toutes les `{variables}`. Si `curiosity_tasks.yaml` contient `{{label}}` qui devient `{label}` après une première passe, le moteur de template crashe.

**Solution**
Dans `curiosity_tasks.yaml`, remplacer toute référence à `{{label}}` ou `{{réponse}}` par une description en prose que CrewAI n'interpolera pas. Vérifier :
```bash
grep -n "{{label}}\|{{réponse}}" src/Mnemo/config/curiosity_tasks.yaml
# ne doit rien retourner
```

---

## 14. joblib manquant — router ML silencieux

**Symptôme**
```
[router ML] echec : No module named 'joblib'
[DBG] ml=conversation (0.00)
```
Le router ML ne fonctionne pas, tout passe par le LLM.

**Cause**
`joblib` et `scikit-learn` ne sont pas dans `requirements.txt`. Ils étaient probablement installés comme dépendances transitives d'une ancienne version — un `--no-cache` les a supprimés.

**Solution**
Vérifier que `requirements.txt` contient :
```
scikit-learn>=1.4.0
joblib>=1.3.0
```
Puis rebuilder :
```bash
docker compose build mnemo
```

---

## 15. Quand rebuilder ?

Un rebuild (`docker compose build mnemo`) est **obligatoire** après toute modification de :

| Fichier modifié | Rebuild nécessaire |
|---|---|
| `requirements.txt` | ✅ oui — packages Python |
| `Dockerfile` ou `Dockerfile.scheduler` | ✅ oui |
| `patch_crewai_tracing.py` | ✅ oui — exécuté au build |
| `src/Mnemo/*.py` | ✅ oui — copié dans `/app` |
| `src/Mnemo/config/*.yaml` | ✅ oui — copié dans `/app` |
| `data/memory.md` | ❌ non — volume monté |
| `data/memory.db` | ❌ non — volume monté |
| `.env` | ❌ non — lu au runtime |

**Commandes utiles**
```bash
# Rebuild standard
docker compose build mnemo

# Rebuild sans cache (résout la plupart des problèmes persistants)
docker compose build --no-cache mnemo

# Rebuild des deux images
docker compose build

# Vérifier ce qui est dans l'image
docker compose run --rm mnemo python -c "import joblib; print('joblib OK')"
docker compose run --rm mnemo ls /app/src/Mnemo/config/
```