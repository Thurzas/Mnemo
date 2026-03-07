# 🛠️ Mnemo --- Troubleshooting Guide

This document lists issues encountered during the installation and use
of Mnemo, along with their causes and solutions.

------------------------------------------------------------------------

## Table of Contents

1.  [Docker does not start](#1-docker-does-not-start)
2.  [LiteLLM --- "Fallback to LiteLLM is not
    available"](#2-litellm--fallback-to-litellm-is-not-available)
3.  [LiteLLM --- "No module named fastapi"
    noise](#3-litellm--no-module-named-fastapi-noise)
4.  [Config file not found (KeyError in
    crew.py)](#4-config-file-not-found-keyerror-in-crewpy)
5.  [Misnamed config file](#5-misnamed-config-file)
6.  [unable to open database file](#6-unable-to-open-database-file)
7.  [Recurring events missing from
    calendar](#7-recurring-events-missing-from-calendar)
8.  [Maximum iterations reached on calendar
    questions](#8-maximum-iterations-reached-on-calendar-questions)
9.  [Read-only file system ---
    uncertain_cases.jsonl](#9-read-only-file-system--uncertain_casesjsonl)
10. [briefing.md created in data/data/ instead of
    data/](#10-briefingmd-created-in-datadata-instead-of-data)
11. [Scheduler cannot find
    Mnemo.scheduler](#11-scheduler-cannot-find-mnemoscheduler)
12. [Dockerfile.scheduler not found during
    build](#12-dockerfilescheduler-not-found-during-build)
13. [Template variable 'label' not
    found](#13-template-variable-label-not-found)
14. [Missing joblib --- silent ML
    router](#14-missing-joblib--silent-ml-router)
15. [When should you rebuild?](#15-when-should-you-rebuild)

------------------------------------------------------------------------

## 1. Docker does not start

**Symptom**

    docker: Cannot connect to the Docker daemon

or

    Error response from daemon: No such image

**Causes and solutions**

-   **Docker Desktop not running** (WSL2): launch Docker Desktop on
    Windows, wait for the green icon in the system tray, then retry.
-   **WSL2 integration not enabled**: Docker Desktop → Settings →
    Resources → WSL Integration → enable your distro.
-   **Image never built**: run `./install.sh` or `docker compose build`.

------------------------------------------------------------------------

## 2. LiteLLM --- "Fallback to LiteLLM is not available"

**Symptom**

    LiteLLM is not available, falling back to LiteLLM
    ImportError: Fallback to LiteLLM is not available

Mnemo crashes on the first message.

**Cause** `litellm[proxy]` in `requirements.txt` installs dependencies
that conflict with CrewAI. The `[proxy]` version must not be used.

**Solution** Ensure `requirements.txt` contains:

    litellm

and **not**:

    litellm[proxy]     ← DO NOT USE

Then rebuild:

``` bash
docker compose build --no-cache mnemo && ./mnemo.sh
```

------------------------------------------------------------------------

## 3. LiteLLM --- "No module named fastapi" noise

**Symptom**

    LiteLLM:ERROR: Error creating standard logging object - Missing dependency No module named 'fastapi'

The message appears several times but **Mnemo works normally**.

**Cause** LiteLLM attempts to initialize its internal proxy logging
module. This module requires `fastapi`, which is not installed (and
should not be --- see point 2). This is harmless noise.

**Solution** `patch_crewai_tracing.py` runs at build time and patches
this behavior. If the noise reappears after a `--no-cache`, verify that
`patch_crewai_tracing.py` is at the project root (next to `Dockerfile`)
and that the `Dockerfile` executes it:

``` dockerfile
COPY patch_crewai_tracing.py /tmp/patch_crewai_tracing.py
RUN python3 /tmp/patch_crewai_tracing.py
```

------------------------------------------------------------------------

## 4. Config file not found (KeyError in crew.py)

**Symptom**

    WARNING: File not found: /app/src/Mnemo/config/scheduler_tasks_config.yaml
    KeyError: 'schedule_task'

**Cause** The YAML configuration files for new crews
(`scheduler_agents.yaml`, `scheduler_tasks_config.yaml`,
`briefing_agents.yaml`, `briefing_tasks.yaml`) are not present in
`src/Mnemo/config/` on the host machine. Docker copies `src/` →
`/app/src/` at build time --- if files are missing on disk, they are
missing in the image.

**Solution** Check that the files exist:

``` bash
ls src/Mnemo/config/ | grep -E "scheduler|briefing"
```

Expected:

    briefing_agents.yaml
    briefing_tasks.yaml
    scheduler_agents.yaml
    scheduler_tasks_config.yaml

Add any missing files to `src/Mnemo/config/`, then rebuild:

``` bash
docker compose build mnemo && ./mnemo.sh
```

------------------------------------------------------------------------

## 5. Misnamed config file

**Symptom** Same error as point 4, despite the files being present in
`src/Mnemo/config/`.

**Cause** A file is incorrectly named. Example encountered:

    scheduler_task.yaml
    scheduler_task_config.yaml

while `crew.py` expects:

    scheduler_tasks_config.yaml

**Solution** Rename files to exactly match the paths declared in
`crew.py`:

``` bash
cd src/Mnemo/config/
mv scheduler_task.yaml scheduler_tasks.yaml
mv scheduler_task_config.yaml scheduler_tasks_config.yaml
```

Then rebuild.

------------------------------------------------------------------------

## 6. unable to open database file

**Symptom**

    Agent > Error while creating task: unable to open database file

**Possible causes**

**A --- `scheduled_tasks` table does not exist**\
Migration was not executed. Run:

``` bash
docker compose run --rm mnemo init_db
```

**B --- Incorrect DB path in `scheduler_tasks.py`**\
`scheduler_tasks.py` uses an absolute path (`/data/memory.db`) while
Docker WORKDIR is `/data`. If `DATA_PATH` is not defined, the file may
be searched in the wrong location.

Best practice --- same as `memory_tools.py`:

``` python
DB_PATH  = Path("memory.db")   # relative to WORKDIR /data
TASKS_MD = Path("tasks.md")
```

------------------------------------------------------------------------

## 7. Recurring events missing from calendar

**Symptom** Mnemo only displays 2--3 events instead of the full
schedule. Recurring events (weekly classes, fixed meetings) are missing.

**Cause** `get_upcoming_events()` was reading raw `DTSTART`. Events with
`RRULE:FREQ=WEEKLY` have a `DTSTART` in September (creation date) ---
never within the "today+14 days" window.

**Solution** Ensure you have the updated version of
`tools/calendar_tools.py` that includes `_expand_rrule()`. Check:

``` bash
grep -n "_expand_rrule" src/Mnemo/tools/calendar_tools.py
```

------------------------------------------------------------------------

## 8. Maximum iterations reached on calendar questions

**Symptom**

    Task failed: Maximum Iterations reached

Occurs on all calendar-related questions.

**Cause** `memory_retriever` (max_iter=8) was calling `GetCalendarTool`
internally → reformatted JSON → retried → exceeded iteration limit
before answering.

**Solution** The calendar must be pre-fetched in `main.py` before CrewAI
kickoff and injected into `base_inputs` as `calendar_context`. Verify in
`main.py`:

``` python
if eval_json.get("needs_calendar"):
    cal_events = get_upcoming_events(days=21)
    calendar_context = format_events_for_prompt(cal_events)
```

------------------------------------------------------------------------

## 9. Read-only file system --- uncertain_cases.jsonl

**Symptom**

    [DBG] log uncertain failed: [Errno 30] Read-only file system: '/app/src/Mnemo/uncertain_cases.jsonl'

**Cause** `_UNCERTAIN_LOG` pointed to `/app/src/Mnemo/` --- mounted
read-only in Docker. Same issue for `router_model.joblib` and
`training_data.jsonl`.

**Solution** These files must point to the WORKDIR `/data` (relative
path). Verify in `main.py`:

``` python
_UNCERTAIN_LOG = Path("uncertain_cases.jsonl")  # relative to WORKDIR=/data
```

And in router-related scripts:

``` python
_DATA = Path(os.getenv("DATA_PATH", ".")).resolve()
OUTPUT = _DATA / "router_model.joblib"
```

------------------------------------------------------------------------

## 10. briefing.md created in data/data/ instead of data/

**Symptom**

    waifuclawd/
    └── data/
        └── data/
            └── briefing.md

**Cause** `DATA_PATH=./data` in `.env` is resolved from WORKDIR `/data`
→ `/data/data`.

**Solution** Force `DATA_PATH=/data` in `docker-compose.yml` for the
`mnemo-scheduler` service:

``` yaml
environment:
  - HOME=/tmp
  - DATA_PATH=/data
```

------------------------------------------------------------------------

## 11. Scheduler cannot find Mnemo.scheduler

**Symptom**

    /usr/local/bin/python: No module named Mnemo.scheduler

**Cause** `scheduler.py` is not in `src/Mnemo/`. It must be there for
`python -m Mnemo.scheduler` to work.

**Solution**

``` bash
ls src/Mnemo/scheduler.py
mv scheduler.py src/Mnemo/scheduler.py
```

Then rebuild:

``` bash
docker compose build mnemo-scheduler
```

------------------------------------------------------------------------

## 12. Dockerfile.scheduler not found during build

**Symptom**

    failed to solve: failed to read dockerfile: open Dockerfile.scheduler: no such file or directory

**Cause** `Dockerfile.scheduler` must be at the project root (where
`docker-compose.yml` is located), not in a subfolder.

**Solution**

``` bash
ls | grep -E "Dockerfile|docker-compose"
```

Expected:

    Dockerfile
    Dockerfile.scheduler
    docker-compose.yml

Move the file if necessary, then rebuild.

------------------------------------------------------------------------

## 13. Template variable 'label' not found

**Symptom**

    ⚠️ Contextual analysis ignored: Missing required template variable
    'Template variable 'label' not found in inputs dictionary'

**Cause** CrewAI parses YAML task descriptions and attempts to inject
all `{variables}`. If `curiosity_tasks.yaml` contains `{{label}}`, it
becomes `{label}` after a first pass and the template engine crashes.

**Solution** In `curiosity_tasks.yaml`, replace any `{{label}}` or
`{{response}}` references with prose descriptions that CrewAI will not
interpolate. Check:

``` bash
grep -n "{{label}}\|{{response}}" src/Mnemo/config/curiosity_tasks.yaml
```

------------------------------------------------------------------------

## 14. Missing joblib --- silent ML router

**Symptom**

    [router ML] failure: No module named 'joblib'
    [DBG] ml=conversation (0.00)

The ML router does not work; everything falls back to the LLM.

**Cause** `joblib` and `scikit-learn` are not in `requirements.txt`.
They were likely previously installed as transitive dependencies --- a
`--no-cache` removed them.

**Solution** Ensure `requirements.txt` contains:

    scikit-learn>=1.4.0
    joblib>=1.3.0

Then rebuild:

``` bash
docker compose build mnemo
```

------------------------------------------------------------------------

## 15. When should you rebuild?

A rebuild (`docker compose build mnemo`) is **required** after
modifying:

  Modified file                            Rebuild required
  ---------------------------------------- -------------------------------
  `requirements.txt`                       ✅ yes --- Python packages
  `Dockerfile` or `Dockerfile.scheduler`   ✅ yes
  `patch_crewai_tracing.py`                ✅ yes --- executed at build
  `src/Mnemo/*.py`                         ✅ yes --- copied into `/app`
  `src/Mnemo/config/*.yaml`                ✅ yes --- copied into `/app`
  `data/memory.md`                         ❌ no --- mounted volume
  `data/memory.db`                         ❌ no --- mounted volume
  `.env`                                   ❌ no --- read at runtime

**Useful commands**

``` bash
docker compose build mnemo
docker compose build --no-cache mnemo
docker compose build
docker compose run --rm mnemo python -c "import joblib; print('joblib OK')"
docker compose run --rm mnemo ls /app/src/Mnemo/config/
```
