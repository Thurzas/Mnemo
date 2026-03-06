#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# mnemo.sh — Raccourci de lancement Mnemo
#
# Usage :
#   ./mnemo.sh              # démarre une session interactive
#   ./mnemo.sh scheduler    # démarre le scheduler en arrière-plan
#   ./mnemo.sh stop         # arrête le scheduler
#   ./mnemo.sh briefing     # génère le briefing maintenant
#   ./mnemo.sh weekly       # génère le weekly maintenant
#   ./mnemo.sh logs         # logs du scheduler en temps réel
#   ./mnemo.sh ingest <f>   # ingère un fichier dans la mémoire
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

  run|"")
    info "Démarrage de la session Mnemo..."
    docker compose run --rm mnemo 
    ;;

  scheduler)
    info "Démarrage du scheduler en arrière-plan..."
    docker compose up -d mnemo-scheduler
    ok "Scheduler démarré."
    echo -e "  Logs : ${BOLD}./mnemo.sh logs${RESET}"
    ;;

  stop)
    info "Arrêt du scheduler..."
    docker compose stop mnemo-scheduler
    ok "Scheduler arrêté."
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

  status)
    echo -e "\n${BOLD}── Containers Mnemo ──────────────────────────${RESET}"
    docker compose ps
    echo ""
    ;;

  help|--help|-h)
    echo -e "\n${BOLD}mnemo.sh — Raccourci Mnemo${RESET}"
    echo ""
    echo -e "  ${BOLD}./mnemo.sh${RESET}               Session interactive"
    echo -e "  ${BOLD}./mnemo.sh scheduler${RESET}     Démarre le scheduler (daemon)"
    echo -e "  ${BOLD}./mnemo.sh stop${RESET}          Arrête le scheduler"
    echo -e "  ${BOLD}./mnemo.sh briefing${RESET}      Génère briefing.md maintenant"
    echo -e "  ${BOLD}./mnemo.sh weekly${RESET}        Génère weekly.md maintenant"
    echo -e "  ${BOLD}./mnemo.sh deadline${RESET}      Scanne les deadlines J-1/J-3"
    echo -e "  ${BOLD}./mnemo.sh logs${RESET}          Logs scheduler en temps réel"
    echo -e "  ${BOLD}./mnemo.sh ingest <f>${RESET}    Ingère un fichier en mémoire"
    echo -e "  ${BOLD}./mnemo.sh status${RESET}        État des containers"
    echo ""
    ;;

  *)
    warn "Commande inconnue : $CMD"
    echo "  Lance ${BOLD}./mnemo.sh help${RESET} pour la liste des commandes."
    exit 1
    ;;

esac