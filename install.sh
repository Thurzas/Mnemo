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
# ══════════════════════════════════════════════════════════════════

set -e  # Stoppe si une commande échoue

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
# 1. Prérequis
# ══════════════════════════════════════════════════════════════════
step "Vérification des prérequis"

# Docker
if ! command -v docker &>/dev/null; then
    fail "Docker non trouvé. Installe Docker Desktop ou Docker Engine."
fi
ok "Docker : $(docker --version | cut -d' ' -f3 | tr -d ',')"

# Docker Compose
if ! docker compose version &>/dev/null; then
    fail "Docker Compose plugin non trouvé. Installe Docker Compose v2."
fi
ok "Docker Compose : $(docker compose version --short)"

# Ollama
if ! command -v ollama &>/dev/null; then
    warn "Ollama non trouvé dans le PATH."
    warn "Installe Ollama depuis https://ollama.com puis relance ce script."
    warn "Si Ollama tourne déjà en service (WSL2, serveur), tu peux ignorer."
    read -p "  Continuer quand même ? (o/N) " CONTINUE
    [[ "${CONTINUE,,}" == "o" ]] || exit 0
else
    ok "Ollama : $(ollama --version 2>/dev/null || echo 'installé')"
fi

# ══════════════════════════════════════════════════════════════════
# 2. Configuration .env
# ══════════════════════════════════════════════════════════════════
step "Configuration"

if [ ! -f ".env" ]; then
    cp .env.example .env
    ok ".env créé depuis .env.example"

    # Détection du modèle disponible
    if command -v ollama &>/dev/null; then
        AVAILABLE=$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' | head -5)
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

# Charge les variables pour la suite
source .env
MODEL="${MODEL:-ollama/mistral}"
DATA_DIR="${DATA_DIR:-./data}"

# ══════════════════════════════════════════════════════════════════
# 3. Structure data/
# ══════════════════════════════════════════════════════════════════
step "Création de data/"

mkdir -p "${DATA_DIR}/sessions"
mkdir -p "${DATA_DIR}/docs"

# Crée memory.md vierge si absent
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

MODEL_NAME="${MODEL#ollama/}"  # Retire le préfixe "ollama/"

pull_if_needed() {
    local model="$1"
    if ollama list 2>/dev/null | grep -q "^${model}"; then
        ok "${model} déjà présent"
    else
        info "Téléchargement de ${model}..."
        ollama pull "${model}" || warn "Impossible de tirer ${model} — vérifie qu'Ollama tourne"
    fi
}

if command -v ollama &>/dev/null; then
    pull_if_needed "${MODEL_NAME}"
    pull_if_needed "nomic-embed-text"
else
    warn "Ollama absent — assure-toi que ces modèles sont disponibles sur ton serveur Ollama :"
    echo "    ollama pull ${MODEL_NAME}"
    echo "    ollama pull nomic-embed-text"
fi

# ══════════════════════════════════════════════════════════════════
# 5. Build Docker
# ══════════════════════════════════════════════════════════════════
step "Build de l'image Docker"
docker compose build
ok "Image mnemo:latest construite"

# ══════════════════════════════════════════════════════════════════
# 6. Initialisation de la base SQLite
# ══════════════════════════════════════════════════════════════════
step "Initialisation de la base SQLite"

docker compose run --rm mnemo init_db
ok "Base SQLite initialisée dans ${DATA_DIR}/memory.db"

# ══════════════════════════════════════════════════════════════════
# Fin
# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}✅ Mnemo est prêt.${RESET}"
echo ""
echo "  Pour démarrer une session :"
echo -e "  ${BOLD}docker compose run --rm mnemo${RESET}"
echo ""
echo "  Pour ingérer un document :"
echo -e "  ${BOLD}docker compose run --rm mnemo ingest /data/docs/ton_fichier.pdf${RESET}"
echo ""
echo "  Pour relancer le questionnaire d'initialisation :"
echo -e "  ${BOLD}docker compose run --rm mnemo curiosity${RESET}"
echo "════════════════════════════════════════"
echo ""