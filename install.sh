#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# install.sh — Installation initiale de Mnemo
#
# Usage :
#   chmod +x install.sh && ./install.sh
#
# Ce script :
#   1. Vérifie les prérequis (Docker, Ollama)
#   2. Crée .env depuis .env.template
#   3. Initialise le dossier data/ (memory.md, docs/, sessions/)
#   4. Tire les modèles Ollama nécessaires
#   5. Build les images Docker
#   6. Initialise la base SQLite
# ══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${BLUE}▶  $1${RESET}"; }
ok()    { echo -e "${GREEN}✅ $1${RESET}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${RESET}"; }
error() { echo -e "${RED}❌ $1${RESET}"; exit 1; }
step()  { echo -e "\n${BOLD}── $1 ──────────────────────────────────────${RESET}"; }


# ── 1. Vérification des prérequis ────────────────────────────────

step "Vérification des prérequis"

if ! command -v docker &>/dev/null; then
  error "Docker n'est pas installé. Installe-le depuis https://docs.docker.com/get-docker/"
fi
ok "Docker trouvé : $(docker --version | head -1)"

if ! docker compose version &>/dev/null; then
  error "Docker Compose v2 requis. Il est inclus dans Docker Desktop."
fi
ok "Docker Compose trouvé : $(docker compose version | head -1)"

if ! command -v ollama &>/dev/null; then
  warn "Ollama n'est pas trouvé dans le PATH."
  warn "Installe-le depuis https://ollama.com puis relance ce script."
  warn "Si Ollama tourne sur un autre serveur, ignore cet avertissement"
  warn "et modifie API_BASE dans .env manuellement."
  OLLAMA_AVAILABLE=false
else
  ok "Ollama trouvé : $(ollama --version 2>/dev/null || echo 'version inconnue')"
  OLLAMA_AVAILABLE=true
fi


# ── 2. Fichier .env ───────────────────────────────────────────────

step "Configuration .env"

if [ -f ".env" ]; then
  ok ".env déjà présent — conservé tel quel."
else
  cp .env.template .env
  ok ".env créé depuis .env.template"
  warn "Ouvre .env et ajuste les valeurs si besoin (MODEL, CALENDAR_SOURCE, etc.)"
fi


# ── 3. Dossier data/ ─────────────────────────────────────────────

step "Initialisation du dossier data/"

mkdir -p data/docs data/sessions
ok "data/docs/ et data/sessions/ créés."

if [ ! -f "data/memory.md" ]; then
  if [ -f "memory.md.template" ]; then
    cp memory.md.template data/memory.md
    ok "data/memory.md initialisé depuis le template."
  else
    warn "memory.md.template introuvable — data/memory.md sera créé au premier lancement."
  fi
else
  ok "data/memory.md déjà présent — conservé."
fi

# Copie le modèle ML de routing s'il n'est pas encore dans data/
if [ ! -f "data/router_model.joblib" ] && [ -f "router_model.joblib" ]; then
  cp router_model.joblib data/router_model.joblib
  ok "router_model.joblib copié dans data/"
fi


# ── 4. Modèles Ollama ─────────────────────────────────────────────

step "Téléchargement des modèles Ollama"

if [ "$OLLAMA_AVAILABLE" = true ]; then
  # Lit le modèle configuré dans .env (sans le préfixe ollama/)
  CONFIGURED_MODEL=$(grep "^MODEL=" .env | cut -d'=' -f2 | sed 's|ollama/||')
  LLM_MODEL="${CONFIGURED_MODEL:-mistral}"

  info "Modèle LLM : $LLM_MODEL"
  ollama pull "$LLM_MODEL"
  ok "Modèle $LLM_MODEL prêt."

  info "Modèle d'embedding : nomic-embed-text"
  ollama pull nomic-embed-text
  ok "nomic-embed-text prêt."
else
  warn "Ollama non disponible — skipping pull des modèles."
  warn "Lance manuellement : ollama pull mistral && ollama pull nomic-embed-text"
fi


# ── 5. Build Docker ───────────────────────────────────────────────

step "Build des images Docker"

info "Build mnemo:latest..."
docker compose build mnemo
ok "Image mnemo:latest construite."

info "Build mnemo-scheduler:latest..."
docker compose build mnemo-scheduler
ok "Image mnemo-scheduler:latest construite."


# ── 6. Initialisation de la base SQLite ──────────────────────────

step "Initialisation de la base SQLite"

docker compose run --rm mnemo init_db
ok "Base SQLite initialisée dans data/memory.db"


# ── Fin ───────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Mnemo est prêt !${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  Lance une session :       ${BOLD}./mnemo.sh${RESET}"
echo -e "  Démarre les services :    ${BOLD}./mnemo.sh services${RESET}"
echo -e "  Aide :                    ${BOLD}./mnemo.sh help${RESET}"
echo ""
if [ -f ".env" ]; then
  CALENDAR=$(grep "^CALENDAR_SOURCE=" .env | cut -d'=' -f2-)
  if [ -z "$CALENDAR" ]; then
    warn "Calendrier non configuré. Ajoute CALENDAR_SOURCE dans .env pour activer l'agenda."
  fi
fi
