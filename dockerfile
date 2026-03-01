# ══════════════════════════════════════════════════════════════════
# Mnemo — Agent mémoire personnel
# ══════════════════════════════════════════════════════════════════
#
# Ollama tourne sur la machine HÔTE (pas dans ce conteneur).
# Les données (memory.db, memory.md, sessions/) sont montées depuis l'hôte.
# Le conteneur est read-only sur src/ — les données vivent dans /data.
#
# Build :  docker compose build
# Run   :  docker compose run --rm mnemo
# ══════════════════════════════════════════════════════════════════

FROM python:3.12-slim

# ── Métadonnées ───────────────────────────────────────────────────
LABEL maintainer="Mnemo"
LABEL description="Agent mémoire personnel — CrewAI + SQLite + Ollama"
LABEL version="2.0"

# ── Dépendances système minimales ────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Répertoire de travail = données utilisateur ──────────────────
# /data est monté depuis l'hôte → memory.db, memory.md, sessions/
WORKDIR /data

# ── Dépendances Python ───────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ── Code source (lecture seule dans le conteneur) ────────────────
COPY src/ /app/src/

# ── Le code tourne depuis /data (chemins relatifs → /data/memory.db etc.)
# src/ est ajouté au PYTHONPATH pour que `import Mnemo` fonctionne
ENV PYTHONPATH="/app/src"
ENV PYTHONUNBUFFERED=1

# ── Point d'entrée ───────────────────────────────────────────────
# Lance python -m Mnemo.main run depuis /data
# Le flag -u désactive le buffering pour le mode interactif
ENTRYPOINT ["python", "-u", "-m", "Mnemo.main"]
CMD ["run"]