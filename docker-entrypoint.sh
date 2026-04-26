#!/bin/bash
set -e

echo "[BSS] Démarrage de BSS Explorer v9.0.0"
echo "[BSS] Mode : ${BSS_MODE:-full}"

# Démarrer l'API FastAPI en arrière-plan
echo "[BSS] Démarrage de l'API FastAPI sur le port ${API_PORT:-8001}..."
python3 -m uvicorn api:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8001}" \
    --workers 2 \
    --log-level info &
API_PID=$!

# Attendre que l'API soit prête
echo "[BSS] Attente de l'API..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${API_PORT:-8001}/health" > /dev/null 2>&1; then
        echo "[BSS] API prête."
        break
    fi
    sleep 1
done

# Démarrer Streamlit
echo "[BSS] Démarrage de Streamlit sur le port ${APP_PORT:-8501}..."
exec python3 -m streamlit run app.py \
    --server.port "${APP_PORT:-8501}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection true \
    --browser.gatherUsageStats false
