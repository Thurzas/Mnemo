# ══════════════════════════════════════════════════════════════════
# Mnemo — Agent mémoire personnel  (Phase 3 — durci)
# ══════════════════════════════════════════════════════════════════
#
# Ollama tourne sur la machine HÔTE (pas dans ce conteneur).
# Les données (memory.db, memory.md, sessions/) sont montées depuis l'hôte.
# Le conteneur est read-only sur src/ — les données vivent dans /data.
# L'agent tourne en tant qu'utilisateur non-root (uid 1000).
#
# Build :  docker compose build
# Run   :  docker compose run --rm mnemo
# ══════════════════════════════════════════════════════════════════


# ── Étape 1 : build du frontend React ────────────────────────────
# Une image Node temporaire compile le frontend.
# Le résultat (src/Mnemo/static/) est copié dans l'image finale.
# L'utilisateur n'a pas besoin de Node.js sur sa machine.
FROM node:22-slim AS frontend-builder

WORKDIR /build

# Installe les dépendances (layer mis en cache si package.json inchangé)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

# Copie les sources et compile
# outDir est configuré dans vite.config.ts → ../src/Mnemo/static
# (depuis /build, ça donne /src/Mnemo/static dans le container builder)
COPY frontend/ ./
RUN npm run build


# ── Étape 2 : image Python finale ────────────────────────────────
FROM python:3.12-slim

# ── Métadonnées ───────────────────────────────────────────────────
LABEL maintainer="Mnemo"
LABEL description="Agent mémoire personnel — CrewAI + SQLite + Ollama"
LABEL version="3.0"

# ── Dépendances système minimales ────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Utilisateur non-root ─────────────────────────────────────────
# Crée un utilisateur dédié (uid/gid 1000) sans shell de login.
# Le conteneur ne tourne JAMAIS en root — même en cas d'exploitation.
RUN groupadd --gid 1000 mnemo \
 && useradd  --uid 1000 --gid 1000 --create-home --shell /bin/false mnemo \
 && mkdir -p /home/mnemo/.local/share \
 && chown -R mnemo:mnemo /home/mnemo

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir litellm

# ── Patch CrewAI : désactive le prompt interactif de tracing ────
COPY docker/patch_crewai_tracing.py /tmp/patch_crewai_tracing.py
RUN python3 /tmp/patch_crewai_tracing.py

# ── Code source (lecture seule dans le conteneur) ────────────────
COPY src/ /app/src/

# ── Frontend compilé (depuis l'étape builder) ────────────────────
# Remplace le contenu de static/ par le build Vite frais.
COPY --from=frontend-builder /src/Mnemo/static/ /app/src/Mnemo/static/

RUN chown -R mnemo:mnemo /app

# ── Répertoire de travail = données utilisateur ──────────────────
# /data est monté depuis l'hôte → memory.db, memory.md, sessions/
# Le répertoire doit exister et appartenir à mnemo pour le volume mount
RUN mkdir -p /data && chown mnemo:mnemo /data
WORKDIR /data

# ── Passage en non-root ───────────────────────────────────────────
USER mnemo

# ── Variables d'environnement ────────────────────────────────────
# src/ est ajouté au PYTHONPATH pour que `import Mnemo` fonctionne
ENV PYTHONPATH="/app/src"
# Redirige HOME vers /tmp (tmpfs) — évite les erreurs de lecture seule
# CrewAI écrit son cache ChromaDB dans HOME/.local/share/data
ENV HOME=/tmp
ENV PYTHONUNBUFFERED=1
# Désactive la télémétrie CrewAI — aucun envoi vers app.crewai.com
ENV OTEL_SDK_DISABLED=true
ENV CREWAI_DISABLE_TELEMETRY=true
ENV CREWAI_DISABLE_EXECUTION_TRACE_VIEWER=true
ENV CREWAI_TRACING_ENABLED=false

# ── Point d'entrée ───────────────────────────────────────────────
ENTRYPOINT ["python", "-u", "-m", "Mnemo.main"]
CMD ["run"]
