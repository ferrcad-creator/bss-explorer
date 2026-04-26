# ─────────────────────────────────────────────────────────────────────────────
# BSS Explorer — Dockerfile production (Python 3.11-slim, non-root, healthcheck)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Métadonnées
LABEL maintainer="FERRAPD" \
      description="BSS Explorer — API FastAPI + Streamlit" \
      version="9.0.0"

# Variables d'environnement
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    APP_USER=bss \
    APP_PORT=8501 \
    API_PORT=8001

# ── Dépendances système ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Utilisateur non-root ───────────────────────────────────────────────────────
RUN groupadd --gid 1001 ${APP_USER} \
    && useradd --uid 1001 --gid ${APP_USER} --shell /bin/bash --create-home ${APP_USER}

# ── Répertoire de travail ──────────────────────────────────────────────────────
WORKDIR ${APP_HOME}

# ── Dépendances Python (couche cachée séparément) ─────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Code source ───────────────────────────────────────────────────────────────
COPY --chown=${APP_USER}:${APP_USER} . .

# ── Permissions ───────────────────────────────────────────────────────────────
RUN mkdir -p /app/data && chown -R ${APP_USER}:${APP_USER} /app

# ── Utilisateur non-root ───────────────────────────────────────────────────────
USER ${APP_USER}

# ── Ports exposés ─────────────────────────────────────────────────────────────
EXPOSE ${APP_PORT} ${API_PORT}

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${API_PORT}/health || exit 1

# ── Script de démarrage ───────────────────────────────────────────────────────
COPY --chown=${APP_USER}:${APP_USER} docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
