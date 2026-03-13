#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# mnemo.sh — Raccourci de lancement Mnemo
#
# Usage :
#   ./mnemo.sh setup        # installation initiale (première fois)
#   ./mnemo.sh              # démarre une session interactive
#   ./mnemo.sh services     # démarre scheduler + API en arrière-plan
#   ./mnemo.sh scheduler    # démarre le scheduler seul
#   ./mnemo.sh api          # démarre l'API seule
#   ./mnemo.sh stop         # arrête scheduler + API
#   ./mnemo.sh briefing     # génère le briefing maintenant
#   ./mnemo.sh weekly       # génère le weekly maintenant
#   ./mnemo.sh logs         # logs du scheduler en temps réel
#   ./mnemo.sh logs-api     # logs de l'API en temps réel
#   ./mnemo.sh ingest <f>   # ingère un fichier dans la mémoire
#   ./mnemo.sh adduser <n>  # crée un utilisateur et affiche son token
#   ./mnemo.sh fix-perms    # chmod 600/700 sur /data (migration données existantes)
#   ./mnemo.sh status       # état des containers
#
# Tip WSL2 : ajoute un alias dans ~/.bashrc pour lancer depuis n'importe où :
#   alias mnemo='/mnt/f/prod/crew/waifuclawd/mnemo.sh'
# ══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

# ── Couleurs ──────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; RESET='\033[0m'

info() { echo -e "${BLUE}▶  $1${RESET}"; }
ok()   { echo -e "${GREEN}✅ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠️  $1${RESET}"; }

CMD="${1:-run}"

case "$CMD" in

  setup|install)
    info "Lancement de l'installeur Mnemo..."
    bash install.sh
    ;;

  run|"")
    info "Démarrage de la session Mnemo..."
    docker compose run --rm mnemo
    ;;

  services)
    info "Démarrage du scheduler + API en arrière-plan..."
    docker compose up -d mnemo-scheduler mnemo-api
    ok "Scheduler et API démarrés."
    echo -e "  Logs scheduler : ${BOLD}./mnemo.sh logs${RESET}"
    echo -e "  Logs API       : ${BOLD}./mnemo.sh logs-api${RESET}"
    ;;

  scheduler)
    info "Démarrage du scheduler en arrière-plan..."
    docker compose up -d mnemo-scheduler
    ok "Scheduler démarré."
    echo -e "  Logs : ${BOLD}./mnemo.sh logs${RESET}"
    ;;

  api)
    info "Démarrage de l'API en arrière-plan..."
    docker compose up -d mnemo-api
    ok "API démarrée."
    echo -e "  Logs : ${BOLD}./mnemo.sh logs-api${RESET}"
    ;;

  stop)
    info "Arrêt et suppression des containers..."
    docker compose down --remove-orphans 2>/dev/null || true
    docker container prune -f >/dev/null 2>&1 || true
    ok "Services arrêtés."
    ;;

  rebuild)
    info "Arrêt et suppression des containers..."
    docker compose down --remove-orphans 2>/dev/null || true
    docker container prune -f >/dev/null 2>&1 || true
    info "Rebuild des images..."
    docker compose build mnemo mnemo-scheduler
    info "Redémarrage du scheduler et de l'API..."
    docker compose up -d mnemo-scheduler mnemo-api
    # Si mnemo-rvc était actif (profile voice), rebuild + redémarre
    if docker compose --profile voice ps -q mnemo-rvc 2>/dev/null | grep -q .; then
      info "Rebuild du service RVC..."
      docker compose --profile voice build mnemo-rvc
      docker compose --profile voice up -d mnemo-rvc
      ok "Rebuild terminé. Scheduler, API et RVC redémarrés."
    else
      ok "Rebuild terminé. Scheduler et API redémarrés."
      echo -e "  (RVC non actif — lance ${BOLD}./mnemo.sh rvc${RESET} pour l'activer)"
    fi
    ;;

  rvc)
    info "Build + démarrage du service RVC (voix custom)..."
    docker compose --profile voice build mnemo-rvc
    docker compose --profile voice up -d mnemo-rvc
    ok "Service RVC démarré."
    echo -e "  Ajoute ${BOLD}RVC_SERVICE_URL=http://mnemo-rvc:7865${RESET} dans .env puis relance l'API :"
    echo -e "  ${BOLD}./mnemo.sh api${RESET}"
    echo -e "  Logs : ${BOLD}./mnemo.sh logs-rvc${RESET}"
    ;;

  briefing)
    info "Génération du briefing..."
    docker compose run --rm mnemo-scheduler --now briefing
    ok "briefing.md généré dans data/"
    ;;

  weekly)
    info "Génération du résumé hebdomadaire..."
    docker compose run --rm mnemo-scheduler --now weekly
    ok "weekly.md généré dans data/"
    ;;

  deadline)
    info "Scan des deadlines J-1/J-3..."
    docker compose run --rm mnemo-scheduler --now deadline
    ok "Scan terminé."
    ;;

  logs)
    info "Logs du scheduler (Ctrl+C pour quitter)..."
    docker compose logs -f mnemo-scheduler
    ;;

  logs-api)
    info "Logs de l'API (Ctrl+C pour quitter)..."
    docker compose logs -f mnemo-api
    ;;

  logs-rvc)
    info "Logs du service RVC (Ctrl+C pour quitter)..."
    docker compose --profile voice logs -f mnemo-rvc
    ;;

  ingest)
    FILE="${2:-}"
    if [ -z "$FILE" ]; then
      warn "Usage : ./mnemo.sh ingest <chemin_fichier>"
      warn "Exemple : ./mnemo.sh ingest data/docs/rapport.pdf"
      exit 1
    fi
    # Convertit le chemin hôte en chemin container si besoin
    if [[ "$FILE" != /data/* ]]; then
      BASENAME=$(basename "$FILE")
      info "Ingestion de $BASENAME..."
      docker compose run --rm mnemo ingest "/data/docs/$BASENAME"
    else
      docker compose run --rm mnemo ingest "$FILE"
    fi
    ;;

  fix-perms)
    DATA_DIR="${DATA_PATH:-./data}"
    info "Correction des permissions sur $DATA_DIR..."
    # Données globales
    [ -f "$DATA_DIR/users.json" ] && chmod 600 "$DATA_DIR/users.json"
    [ -d "$DATA_DIR/users" ]     && chmod 700 "$DATA_DIR/users"
    [ -d "$DATA_DIR/sessions" ]  && chmod 700 "$DATA_DIR/sessions"
    # Fichiers globaux
    for f in "$DATA_DIR"/briefing.md "$DATA_DIR"/weekly.md \
             "$DATA_DIR"/memory.md "$DATA_DIR"/tasks.md; do
      [ -f "$f" ] && chmod 600 "$f"
    done
    # Répertoires et fichiers par utilisateur
    if [ -d "$DATA_DIR/users" ]; then
      find "$DATA_DIR/users" -type d -exec chmod 700 {} +
      find "$DATA_DIR/users" -type f -exec chmod 600 {} +
    fi
    ok "Permissions corrigées."
    ;;

  adduser)
    USERNAME="${2:-}"
    if [ -z "$USERNAME" ]; then
      warn "Usage : ./mnemo.sh adduser <nom_utilisateur>"
      exit 1
    fi
    # Charger MNEMO_ADMIN_TOKEN depuis .env si pas déjà dans le shell
    if [ -z "${MNEMO_ADMIN_TOKEN:-}" ] && [ -f ".env" ]; then
      MNEMO_ADMIN_TOKEN=$(grep -E '^MNEMO_ADMIN_TOKEN\s*=' .env | cut -d'=' -f2- | tr -d '\r"' | xargs)
    fi
    ADMIN_TOKEN="${MNEMO_ADMIN_TOKEN:-}"
    if [ -z "$ADMIN_TOKEN" ]; then
      warn "MNEMO_ADMIN_TOKEN n'est pas défini."
      warn "Définissez-le dans .env ou exportez-le : export MNEMO_ADMIN_TOKEN=votre_secret"
      exit 1
    fi
    # Appel à l'API si elle tourne, sinon appel Python direct
    if docker compose ps --status running mnemo-api 2>/dev/null | grep -q mnemo-api; then
      info "Création de l'utilisateur '$USERNAME' via l'API..."
      RESPONSE=$(curl -s -X POST http://localhost:8000/api/users \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"$USERNAME\"}")
      TOKEN=$(echo "$RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
      if [ -z "$TOKEN" ]; then
        warn "Erreur : $RESPONSE"
        exit 1
      fi
    else
      info "API non démarrée — création directe via Python..."
      DATA_DIR="${DATA_PATH:-./data}"
      USERS_FILE="$DATA_DIR/users.json"
      TOKEN=$(python3 -c "
import json, hashlib, secrets, os, sys
from pathlib import Path
from datetime import datetime

username = sys.argv[1]
users_file = Path('$USERS_FILE')
admin_dir = Path('$DATA_DIR/users') / username

users = {}
if users_file.exists():
    users = json.loads(users_file.read_text())
if username in users:
    print('ERROR:already_exists', file=sys.stderr)
    sys.exit(1)

token = f'mnemo_{secrets.token_hex(32)}'
users[username] = {
    'token_hash': hashlib.sha256(token.encode()).hexdigest(),
    'calendar_source': '',
    'created_at': datetime.now().isoformat(),
}
users_file.parent.mkdir(parents=True, exist_ok=True)
users_file.write_text(json.dumps(users, ensure_ascii=False, indent=2))
admin_dir.mkdir(parents=True, exist_ok=True)
(admin_dir / 'sessions').mkdir(exist_ok=True)
print(token)
" "$USERNAME" 2>&1)
      if echo "$TOKEN" | grep -q "ERROR:already_exists"; then
        warn "Utilisateur '$USERNAME' déjà existant."
        exit 1
      fi
    fi
    ok "Utilisateur '$USERNAME' créé."
    echo ""
    echo -e "  ${BOLD}Token (à conserver — non récupérable) :${RESET}"
    echo -e "  ${GREEN}$TOKEN${RESET}"
    echo ""
    warn "Ce token ne sera plus affiché. Conservez-le en lieu sûr."
    ;;

  status)
    echo -e "\n${BOLD}── Containers Mnemo ──────────────────────────${RESET}"
    docker compose ps
    echo ""
    ;;

  help|--help|-h)
    echo -e "\n${BOLD}mnemo.sh — Raccourci Mnemo${RESET}"
    echo ""
    echo -e "  ${BOLD}./mnemo.sh setup${RESET}         Installation initiale (première fois)"
    echo -e "  ${BOLD}./mnemo.sh${RESET}               Session interactive"
    echo -e "  ${BOLD}./mnemo.sh services${RESET}      Démarre scheduler + API (daemon)"
    echo -e "  ${BOLD}./mnemo.sh scheduler${RESET}     Démarre le scheduler seul (daemon)"
    echo -e "  ${BOLD}./mnemo.sh api${RESET}           Démarre l'API seule (daemon)"
    echo -e "  ${BOLD}./mnemo.sh stop${RESET}          Arrête scheduler + API"
    echo -e "  ${BOLD}./mnemo.sh rebuild${RESET}       Stop → build → redémarre scheduler + API (+ RVC si actif)"
    echo -e "  ${BOLD}./mnemo.sh briefing${RESET}      Génère briefing.md maintenant"
    echo -e "  ${BOLD}./mnemo.sh weekly${RESET}        Génère weekly.md maintenant"
    echo -e "  ${BOLD}./mnemo.sh deadline${RESET}      Scanne les deadlines J-1/J-3"
    echo -e "  ${BOLD}./mnemo.sh logs${RESET}          Logs scheduler en temps réel"
    echo -e "  ${BOLD}./mnemo.sh logs-api${RESET}      Logs API en temps réel"
    echo -e "  ${BOLD}./mnemo.sh rvc${RESET}           Build + démarre le service RVC (voix custom)"
    echo -e "  ${BOLD}./mnemo.sh logs-rvc${RESET}      Logs RVC en temps réel"
    echo -e "  ${BOLD}./mnemo.sh ingest <f>${RESET}    Ingère un fichier en mémoire"
    echo -e "  ${BOLD}./mnemo.sh adduser <n>${RESET}   Crée un utilisateur, affiche son token"
    echo -e "  ${BOLD}./mnemo.sh fix-perms${RESET}     Corrige chmod 600/700 sur /data (migration)"
    echo -e "  ${BOLD}./mnemo.sh status${RESET}        État des containers"
    echo ""
    ;;

  *)
    warn "Commande inconnue : $CMD"
    echo "  Lance ${BOLD}./mnemo.sh help${RESET} pour la liste des commandes."
    exit 1
    ;;

esac