#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# install.sh — Installation initiale de Mnemo
#
# Ce script :
#   1. Vérifie les prérequis (Docker, Ollama)
#   2. Crée la config .env si absente
#   3. Crée le dossier data/ avec la structure attendue
#   4. Tire les modèles Ollama requis
#   5. Construit l'image Docker
#   6. Initialise la base SQLite
#
# Usage :
#   chmod +x install.sh && ./install.sh
#
# WSL2 : assure-toi que Docker Desktop a l'intégration WSL2 activée
#   pour ta distro (Docker Desktop → Settings → Resources → WSL Integration)
# ══════════════════════════════════════════════════════════════════

set -e

# ── Se place dans le répertoire du script + localise docker-compose.yml ───
cd "$(dirname "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(pwd)"
if [ -f "${SCRIPT_DIR}/docker-compose.yml" ]; then
    COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
elif [ -f "${SCRIPT_DIR}/docker/docker-compose.yml" ]; then
    COMPOSE_FILE="${SCRIPT_DIR}/docker/docker-compose.yml"
else
    echo "❌ docker-compose.yml introuvable dans ${SCRIPT_DIR}"
    exit 1
fi

# ── Couleurs ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${RESET}"; }
info() { echo -e "${BLUE}ℹ️  $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠️  $1${RESET}"; }
fail() { echo -e "${RED}❌ $1${RESET}"; exit 1; }
step() { echo -e "\n${BOLD}── $1${RESET}"; }

echo -e "\n${BOLD}🧠 Installation de Mnemo${RESET}"
echo "════════════════════════════════════════"

# ══════════════════════════════════════════════════════════════════
# Détection de l'environnement
# ══════════════════════════════════════════════════════════════════

# Détecte WSL2 pour adapter les messages d'erreur
IS_WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
    info "Environnement WSL2 détecté"
fi

# ══════════════════════════════════════════════════════════════════
# 1. Prérequis
# ══════════════════════════════════════════════════════════════════
step "Vérification des prérequis"

# ── Docker ────────────────────────────────────────────────────────
# Cherche docker dans le PATH standard + chemins WSL2/Docker Desktop
DOCKER_BIN=""
for candidate in docker \
    /usr/bin/docker \
    /usr/local/bin/docker \
    /mnt/c/Program\ Files/Docker/Docker/resources/bin/docker; do
    if command -v "$candidate" &>/dev/null 2>&1; then
        DOCKER_BIN="$candidate"
        break
    fi
done

if [ -z "$DOCKER_BIN" ]; then
    echo -e "${RED}❌ Docker non trouvé.${RESET}"
    if $IS_WSL; then
        echo ""
        echo "  Sur WSL2, Docker Desktop doit être installé sur Windows ET"
        echo "  l'intégration WSL2 doit être activée pour ta distro :"
        echo "    Docker Desktop → Settings → Resources → WSL Integration"
        echo "    → Active le toggle pour ta distro (Ubuntu, Debian...)"
        echo ""
        echo "  Ensuite relance un terminal WSL2 et retente ./install.sh"
    else
        echo "  Installe Docker Engine : https://docs.docker.com/engine/install/"
    fi
    exit 1
fi
ok "Docker : $($DOCKER_BIN --version 2>/dev/null | cut -d' ' -f3 | tr -d ',')"

# Vérifie que le daemon Docker répond (pas seulement que le binaire existe)
if ! $DOCKER_BIN info &>/dev/null 2>&1; then
    echo -e "${RED}❌ Le daemon Docker ne répond pas.${RESET}"
    if $IS_WSL; then
        echo ""
        echo "  Docker Desktop n'est probablement pas lancé."
        echo "  Lance Docker Desktop sur Windows, attends qu'il soit prêt"
        echo "  (icône dans la barre des tâches = vert), puis relance ce script."
    else
        echo "  Lance le daemon Docker : sudo systemctl start docker"
    fi
    exit 1
fi
ok "Daemon Docker : actif"

# ── Docker Compose ────────────────────────────────────────────────
# Teste d'abord le plugin v2 (docker compose), puis le binaire v1 (docker-compose)
COMPOSE_CMD=""
if $DOCKER_BIN compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="$DOCKER_BIN compose"
elif command -v docker-compose &>/dev/null 2>&1; then
    warn "Docker Compose v1 détecté (docker-compose). La v2 est recommandée."
    COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}❌ Docker Compose non trouvé.${RESET}"
    echo "  Installe le plugin Compose v2 :"
    echo "    sudo apt-get install docker-compose-plugin"
    echo "  Ou via Docker Desktop (inclus par défaut)."
    exit 1
fi
ok "Docker Compose : $($COMPOSE_CMD version --short 2>/dev/null || $COMPOSE_CMD version)"

# Substitue 'docker compose' par la commande détectée dans la suite du script
docker_compose() { $COMPOSE_CMD -f "${COMPOSE_FILE}" "$@"; }

# ── Ollama ────────────────────────────────────────────────────────
OLLAMA_BIN=""
for candidate in ollama /usr/local/bin/ollama /usr/bin/ollama; do
    if command -v "$candidate" &>/dev/null 2>&1; then
        OLLAMA_BIN="$candidate"
        break
    fi
done

if [ -z "$OLLAMA_BIN" ]; then
    warn "Ollama non trouvé dans le PATH."
    if $IS_WSL; then
        warn "Si Ollama tourne sur Windows (hors WSL2), c'est normal —"
        warn "l'agent le rejoindra via host.docker.internal."
    else
        warn "Installe Ollama depuis https://ollama.com puis relance ce script."
    fi
    read -p "  Continuer quand même ? (o/N) " CONTINUE
    [[ "${CONTINUE,,}" == "o" ]] || exit 0
else
    ok "Ollama : $($OLLAMA_BIN --version 2>/dev/null || echo 'installé')"
fi

# ══════════════════════════════════════════════════════════════════
# 2. Configuration .env
# ══════════════════════════════════════════════════════════════════
step "Configuration"

if [ ! -f ".env" ]; then
    cp .env.example .env
    ok ".env créé depuis .env.example"

    if [ -n "$OLLAMA_BIN" ]; then
        AVAILABLE=$($OLLAMA_BIN list 2>/dev/null | awk 'NR>1 {print $1}' | head -5)
        if [ -n "$AVAILABLE" ]; then
            info "Modèles Ollama disponibles :"
            echo "$AVAILABLE" | while read -r m; do echo "    - $m"; done
            echo ""
        fi
    fi

    info "Édite .env pour configurer :"
    echo "    MODEL=ollama/<ton_modèle>   (ex: ollama/mistral)"
    echo "    CALENDAR_SOURCE=           (optionnel)"
    echo ""
    read -p "  Ouvrir .env dans nano pour configurer maintenant ? (o/N) " EDIT
    [[ "${EDIT,,}" == "o" ]] && nano .env
else
    ok ".env déjà présent — non modifié"
fi

source .env
MODEL="${MODEL:-ollama/mistral}"
DATA_DIR="${DATA_DIR:-./data}"

# ══════════════════════════════════════════════════════════════════
# 3. Structure data/
# ══════════════════════════════════════════════════════════════════
step "Création de data/"

mkdir -p "${DATA_DIR}/sessions"
mkdir -p "${DATA_DIR}/docs"
touch "${DATA_DIR}/briefing.read" 2>/dev/null || true

if [ ! -f "${DATA_DIR}/memory.md" ]; then
    cat > "${DATA_DIR}/memory.md" << 'MEMORY_TEMPLATE'
# Memory — Agent
_Dernière mise à jour : à compléter_
> Penser à modifier la date d'une session à l'autre si besoin. (format : YYYY-MM-DD)
---
## 🧑 Identité Utilisateur
### Profil de base
- **Nom/Pseudo** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
- **Métier** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
- **Localisation** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
### Préférences & style
- **Style de communication** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
### Centres d'intérêt
- **Centres d'intérêt** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
## Identité Agent
### Rôle & personnalité définis
- **Nom de l'agent** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
### Ce que l'agent a appris sur lui-même
- Aucune note pour l'instant.
## Connaissances persistantes
### Projets en cours
Aucun projet enregistré pour l'instant.
### Décisions prises & leur raison
Aucune décision enregistrée pour l'instant.
## Historique des sessions
## Sources web
_Les faits issus du web sont potentiellement obsolètes. Vérifier la date d'acquisition._
## À ne jamais oublier
MEMORY_TEMPLATE
    ok "memory.md initialisé"
else
    ok "memory.md existant conservé"
fi

ok "Structure data/ prête : ${DATA_DIR}/"

# ══════════════════════════════════════════════════════════════════
# 4. Modèles Ollama
# ══════════════════════════════════════════════════════════════════
step "Modèles Ollama"

MODEL_NAME="${MODEL#ollama/}"

pull_if_needed() {
    local model="$1"
    if [ -z "$OLLAMA_BIN" ]; then
        warn "Ollama absent — tire le modèle manuellement : ollama pull ${model}"
        return
    fi
    if $OLLAMA_BIN list 2>/dev/null | grep -q "^${model}"; then
        ok "${model} déjà présent"
    else
        info "Téléchargement de ${model}..."
        $OLLAMA_BIN pull "${model}" || warn "Impossible de tirer ${model}"
    fi
}

pull_if_needed "${MODEL_NAME}"
pull_if_needed "nomic-embed-text"

# ══════════════════════════════════════════════════════════════════
# 5. Build Docker
# ══════════════════════════════════════════════════════════════════
step "Build des images Docker"
docker_compose build mnemo
ok "Image mnemo:latest construite"
docker_compose build mnemo-scheduler
ok "Image mnemo-scheduler:latest construite"

# ══════════════════════════════════════════════════════════════════
# 6. Initialisation de la base SQLite
# ══════════════════════════════════════════════════════════════════
step "Initialisation de la base SQLite"
docker_compose run --rm mnemo init_db
ok "Base SQLite initialisée dans ${DATA_DIR}/memory.db"

# ══════════════════════════════════════════════════════════════════
# 7. Scheduler (optionnel)
# ══════════════════════════════════════════════════════════════════
step "Scheduler — morning briefing"
echo "  Le scheduler génère chaque matin :"
echo "    - briefing.md  (heure : BRIEFING_TIME dans .env, défaut 07:30)"
echo "    - weekly.md    (chaque lundi matin, WEEKLY_TIME, défaut 08:00)"
echo "    - alertes deadlines J-1/J-3 injectées dans briefing.md"
echo ""
read -p "  Démarrer le scheduler maintenant en arrière-plan ? (o/N) " START_SCHED
if [[ "${START_SCHED,,}" == "o" ]]; then
    docker_compose up -d mnemo-scheduler
    ok "Scheduler démarré (docker compose up -d mnemo-scheduler)"
    info "Logs : docker compose logs -f mnemo-scheduler"
    info "Test immédiat : docker compose run --rm mnemo-scheduler --now briefing"
else
    info "Scheduler non démarré — lance-le manuellement quand tu veux :"
    echo -e "    ${BOLD}docker compose up -d mnemo-scheduler${RESET}"
fi

# ══════════════════════════════════════════════════════════════════
# Fin
# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}✅ Mnemo est prêt.${RESET}"
echo ""
echo "  ── Sessions ──────────────────────────────"
echo -e "  Démarrer une session    : ${BOLD}docker compose run --rm mnemo${RESET}"
echo -e "  Ingérer un document     : ${BOLD}docker compose run --rm mnemo ingest /data/docs/fichier.pdf${RESET}"
echo -e "  Questionnaire init      : ${BOLD}docker compose run --rm mnemo curiosity${RESET}"
echo ""
echo "  ── Scheduler ─────────────────────────────"
echo -e "  Démarrer (daemon)       : ${BOLD}docker compose up -d mnemo-scheduler${RESET}"
echo -e "  Arrêter                 : ${BOLD}docker compose stop mnemo-scheduler${RESET}"
echo -e "  Logs                    : ${BOLD}docker compose logs -f mnemo-scheduler${RESET}"
echo -e "  Test briefing immédiat  : ${BOLD}docker compose run --rm mnemo-scheduler --now briefing${RESET}"
echo -e "  Test weekly immédiat    : ${BOLD}docker compose run --rm mnemo-scheduler --now weekly${RESET}"
echo -e "  Test deadlines immédiat : ${BOLD}docker compose run --rm mnemo-scheduler --now deadline${RESET}"
echo ""
echo "  ── Fichiers générés dans data/ ───────────"
echo "    briefing.md  — mis à jour chaque matin"
echo "    weekly.md    — mis à jour chaque lundi"
echo "    tasks.md     — tâches planifiées (miroir DB)"
if $IS_WSL; then
    echo ""
    info "WSL2 : SearXNG (recherche web locale) :"
    echo -e "  ${BOLD}docker compose --profile search up -d searxng${RESET}"
fi
echo "════════════════════════════════════════"
echo ""