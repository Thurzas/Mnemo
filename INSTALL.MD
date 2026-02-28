# Installation de Mnemo

> Agent mémoire personnel — 100% local, 100% privé.

---

## Prérequis

| Outil | Version | Installation |
|---|---|---|
| Docker | ≥ 24 | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | ≥ 2 | inclus dans Docker Desktop |
| Ollama | ≥ 0.4 | [ollama.com](https://ollama.com) |

> **WSL2** : Docker Desktop avec intégration WSL2 activée est recommandé.

---

## Installation en 3 étapes

### 1. Clone & configure

```bash
git clone https://github.com/ton-user/mnemo.git
cd mnemo
chmod +x install.sh
```

### 2. Lance l'installeur

```bash
./install.sh
```

L'installeur :
- Crée `.env` depuis `.env.example`
- Crée `data/` avec `memory.md`, `sessions/`
- Tire les modèles Ollama (`mistral` + `nomic-embed-text`)
- Build l'image Docker `mnemo:latest`
- Initialise la base SQLite

### 3. Démarre Mnemo

```bash
docker compose run --rm mnemo
```

---

## Configuration

Toute la configuration est dans `.env` :

```bash
# Modèle LLM (ollama/<nom>)
MODEL=ollama/mistral

# URL Ollama — pointe vers ta machine hôte
API_BASE=http://host.docker.internal:11434

# Dossier des données (memory.db, memory.md, sessions/)
DATA_DIR=./data

# Calendrier ICS (optionnel)
CALENDAR_SOURCE=/data/agenda.ics
```

---

## Commandes disponibles

```bash
# Session de conversation
docker compose run --rm mnemo

# Ingérer un document
docker compose run --rm mnemo ingest /data/docs/rapport.pdf

# Lister les documents ingérés
docker compose run --rm mnemo docs

# Forcer le questionnaire d'initialisation
docker compose run --rm mnemo curiosity

# Initialiser / migrer la base SQLite
docker compose run --rm mnemo init_db
```

---

## Structure des données

```
data/                   ← monté dans le conteneur comme /data
├── memory.md           ← mémoire long terme (éditable à la main)
├── memory.db           ← index SQLite (FTS5 + vecteurs)
├── sessions/           ← sessions JSON en attente de consolidation
│   └── session_*.json
└── docs/               ← documents à ingérer (PDF, DOCX, TXT...)
    └── rapport.pdf
```

> `memory.md` et `memory.db` ne vivent **jamais** dans l'image Docker.
> Tes données restent sur ta machine.

---

## Calendrier ICS

Mnemo peut lire ton agenda pour te rappeler les événements proches.

**Fichier local** : place ton `.ics` dans `data/` et configure :
```
CALENDAR_SOURCE=/data/agenda.ics
```

**Google Calendar** : dans Google Agenda → Paramètres → *Lien secret au format iCal*
```
CALENDAR_SOURCE=https://calendar.google.com/calendar/ical/xxx/basic.ics
```

**Nextcloud / Radicale** :
```
CALENDAR_SOURCE=https://nextcloud.local/remote.php/dav/calendars/user/default/
```

---

## Mise à jour

```bash
git pull
docker compose build   # rebuild l'image avec le nouveau code
docker compose run --rm mnemo init_db   # applique les migrations DB si besoin
```

---

## Troubleshooting

**`Connection refused` sur Ollama**
Vérifie qu'Ollama tourne : `ollama list`
Sur Linux, `host.docker.internal` nécessite Docker ≥ 20.10.

**`ModuleNotFoundError`**
Rebuild l'image : `docker compose build --no-cache`

**Mémoire réinitialisée à chaque session**
`DATA_DIR` dans `.env` doit pointer vers un dossier persistant sur ta machine, pas vers un chemin dans le conteneur.