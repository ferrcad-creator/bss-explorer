"""
api.py — API REST FastAPI pour BSS Explorer
============================================
Orchestrateur de collecte hydrogéologique BRGM.

Endpoints :
    GET  /health                    — Vérification de santé
    POST /search                    — Soumet une collecte BSS (retourne job_id)
    GET  /jobs/{job_id}/status      — Statut d'un job
    GET  /jobs/{job_id}/result      — JSON complet des forages
    GET  /jobs/{job_id}/zip         — ZIP (JSON + carte HTML + CSV)

Usage :
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload

Documentation interactive :
    http://localhost:8000/docs
"""

import json
import io
import os
import sys
import uuid
import zipfile
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# Ajouter le répertoire courant au path
sys.path.insert(0, os.path.dirname(__file__))

from utils.bss_collector import collect_site

# ─── Application FastAPI ──────────────────────────────────────────────────────
app = FastAPI(
    title="BSS Explorer API",
    description="""
## API REST — Collecte hydrogéologique BRGM

Orchestrateur de collecte automatisée depuis la Banque du Sous-Sol BRGM.
Soumettez des coordonnées GPS, récupérez les forages, piézomètres et données géotechniques.

### Workflow typique
1. `POST /search` → obtenir un `job_id`
2. `GET /jobs/{job_id}/status` → attendre `completed`
3. `GET /jobs/{job_id}/result` → récupérer les données JSON
4. `GET /jobs/{job_id}/zip` → télécharger le ZIP complet

### Intégration Python
```python
import requests, time

# Soumettre une recherche
r = requests.post("http://localhost:8000/search", json={
    "lat": 43.610769, "lon": 3.876716,
    "code_site": "FRA034001MPL", "emprise_m": 500
})
job_id = r.json()["job_id"]

# Attendre la fin
while True:
    status = requests.get(f"http://localhost:8000/jobs/{job_id}/status").json()
    if status["status"] in ("completed", "failed"):
        break
    time.sleep(2)

# Récupérer les résultats
result = requests.get(f"http://localhost:8000/jobs/{job_id}/result").json()
print(f"{result['nb_ouvrages']} ouvrages trouvés")
```
""",
    version="9.0.0",
    contact={"name": "FERRAPD", "url": "https://github.com/ferrcad-creator/bss-explorer"},
    license_info={"name": "Propriétaire — Usage interne FERRAPD"},
)

# CORS pour les intégrations frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Stockage en mémoire des jobs ─────────────────────────────────────────────
# En production, remplacer par Redis ou PostgreSQL pour la persistance
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# ─── Modèles Pydantic ─────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    lat: float = Field(..., description="Latitude WGS84 du centre de recherche", example=43.610769, ge=-90, le=90)
    lon: float = Field(..., description="Longitude WGS84 du centre de recherche", example=3.876716, ge=-180, le=180)
    code_site: Optional[str] = Field(None, description="Code BSS du site (optionnel, ex: FRA034001MPL)", example="FRA034001MPL")
    emprise_m: int = Field(500, description="Rayon de recherche en mètres (100–2000)", example=500, ge=100, le=2000)

    @validator("code_site", pre=True, always=True)
    def clean_code_site(cls, v):
        if v:
            return v.strip().upper()
        return v


class BatchSearchRequest(BaseModel):
    sites: list = Field(..., description="Liste de sites à collecter", min_items=1, max_items=50)


class JobStatus(BaseModel):
    job_id: str
    status: str = Field(..., description="pending | running | completed | failed")
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: float = Field(0.0, description="Progression de 0 à 1")
    nb_ouvrages: Optional[int] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    uptime_s: float


# ─── Temps de démarrage ───────────────────────────────────────────────────────
_start_time = time.time()


# ─── Fonction de collecte en arrière-plan ─────────────────────────────────────
def _run_collect(job_id: str, lat: float, lon: float, emprise_m: int, code_site: Optional[str]):
    """Exécute la collecte BSS en arrière-plan et met à jour le job."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = datetime.utcnow().isoformat() + "Z"
        _jobs[job_id]["progress"] = 0.1

    try:
        site_input = {"lat": lat, "lon": lon, "emprise_m": emprise_m, "code_site": code_site or ""}
        result = collect_site(site_input, verbose=False)

        with _jobs_lock:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["completed_at"] = datetime.utcnow().isoformat() + "Z"
            _jobs[job_id]["progress"] = 1.0
            _jobs[job_id]["result"] = result
            _jobs[job_id]["nb_ouvrages"] = result.get("nb_ouvrages", 0)

    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["completed_at"] = datetime.utcnow().isoformat() + "Z"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["progress"] = 0.0


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Vérification de santé",
    tags=["Système"],
)
def health():
    """Retourne le statut de l'API et le temps de fonctionnement."""
    return {
        "status": "ok",
        "version": "9.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "uptime_s": round(time.time() - _start_time, 1),
    }


@app.post(
    "/search",
    summary="Soumettre une recherche BSS",
    tags=["Collecte"],
    status_code=202,
    responses={
        202: {"description": "Job créé, collecte en cours"},
        422: {"description": "Paramètres invalides"},
    },
)
def search(req: SearchRequest, background_tasks: BackgroundTasks):
    """
    Soumet une collecte BSS pour un site donné.

    Retourne immédiatement un `job_id` à utiliser pour suivre la progression
    et récupérer les résultats.

    **Temps de collecte typique :** 15–60 secondes selon le nombre d'ouvrages.
    """
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "progress": 0.0,
            "nb_ouvrages": None,
            "error": None,
            "result": None,
            "params": {
                "lat": req.lat,
                "lon": req.lon,
                "code_site": req.code_site,
                "emprise_m": req.emprise_m,
            },
        }

    background_tasks.add_task(
        _run_collect,
        job_id=job_id,
        lat=req.lat,
        lon=req.lon,
        emprise_m=req.emprise_m,
        code_site=req.code_site,
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "pending",
            "message": "Collecte démarrée. Utilisez GET /jobs/{job_id}/status pour suivre la progression.",
            "status_url": f"/jobs/{job_id}/status",
            "result_url": f"/jobs/{job_id}/result",
            "zip_url": f"/jobs/{job_id}/zip",
        },
    )


@app.post(
    "/search/batch",
    summary="Soumettre une collecte en lot",
    tags=["Collecte"],
    status_code=202,
)
def search_batch(req: BatchSearchRequest, background_tasks: BackgroundTasks):
    """
    Soumet une collecte BSS pour plusieurs sites en parallèle.

    Chaque site génère un `job_id` indépendant.
    Maximum 50 sites par requête.
    """
    try:
        sites = req.sites
        if not isinstance(sites, list):
            raise ValueError("sites doit être une liste")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Format de sites invalide : {e}")

    jobs = []
    for site in sites:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        with _jobs_lock:
            _jobs[job_id] = {
                "job_id": job_id,
                "status": "pending",
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "progress": 0.0,
                "nb_ouvrages": None,
                "error": None,
                "result": None,
                "params": site,
            }
        background_tasks.add_task(
            _run_collect,
            job_id=job_id,
            lat=float(site["lat"]),
            lon=float(site["lon"]),
            emprise_m=int(site.get("emprise_m", 500)),
            code_site=site.get("code_site"),
        )
        jobs.append({
            "job_id": job_id,
            "code_site": site.get("code_site", ""),
            "status_url": f"/jobs/{job_id}/status",
        })

    return JSONResponse(
        status_code=202,
        content={
            "nb_jobs": len(jobs),
            "jobs": jobs,
            "message": f"{len(jobs)} collecte(s) démarrée(s) en parallèle.",
        },
    )


@app.get(
    "/jobs/{job_id}/status",
    response_model=JobStatus,
    summary="Statut d'un job",
    tags=["Jobs"],
)
def get_job_status(job_id: str):
    """
    Retourne le statut actuel d'un job de collecte.

    **Statuts possibles :**
    - `pending` — en attente de démarrage
    - `running` — collecte en cours
    - `completed` — terminé avec succès
    - `failed` — erreur lors de la collecte
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "progress": job.get("progress", 0.0),
        "nb_ouvrages": job.get("nb_ouvrages"),
        "error": job.get("error"),
    }


@app.get(
    "/jobs/{job_id}/result",
    summary="Résultat JSON d'un job",
    tags=["Jobs"],
)
def get_job_result(job_id: str):
    """
    Retourne le résultat complet d'une collecte BSS au format JSON.

    Le résultat inclut :
    - Les paramètres d'entrée (`input`)
    - La liste complète des ouvrages (`ouvrages`)
    - Les données Géorisques (`georisques`)
    - L'ouvrage le plus proche (`closest`)
    - Le nombre total d'ouvrages (`nb_ouvrages`)

    **Disponible uniquement si le job est `completed`.**
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable")
    if job["status"] == "pending":
        raise HTTPException(status_code=202, detail="Job en attente de démarrage")
    if job["status"] == "running":
        raise HTTPException(status_code=202, detail="Collecte en cours")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Collecte échouée : {job.get('error', 'Erreur inconnue')}")

    result = job.get("result", {})
    # Inclure les paramètres d'entrée dans la réponse
    return {
        "job_id": job_id,
        "input": job.get("params", {}),
        "success": True,
        "mode": result.get("mode", "WFS BRGM"),
        "nb_ouvrages": result.get("nb_ouvrages", 0),
        "ouvrages": result.get("ouvrages", []),
        "closest": result.get("closest"),
        "georisques": result.get("georisques"),
        "code_site": result.get("code_site", ""),
        "lat_centre": result.get("lat_centre"),
        "lon_centre": result.get("lon_centre"),
        "emprise_m": result.get("emprise_m"),
        "collected_at": job.get("completed_at"),
    }


@app.get(
    "/jobs/{job_id}/zip",
    summary="Télécharger le ZIP d'un job",
    tags=["Jobs"],
    response_class=StreamingResponse,
)
def get_job_zip(job_id: str):
    """
    Génère et retourne un fichier ZIP contenant :
    - `result.json` — données complètes des ouvrages
    - `ouvrages.csv` — tableau CSV (compatible Excel)
    - `carte.html` — carte Leaflet interactive
    - `README.txt` — description du contenu

    **Disponible uniquement si le job est `completed`.**
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job non terminé (statut : {job['status']}). Attendez que le statut soit 'completed'."
        )

    result = job.get("result", {})
    params = job.get("params", {})
    code_site = result.get("code_site") or params.get("code_site", "bss")
    ouvrages = result.get("ouvrages", [])
    collected_at = job.get("completed_at", datetime.utcnow().isoformat() + "Z")

    # ─── Construire le ZIP en mémoire ─────────────────────────────────────────
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

        # 1. result.json
        full_result = {
            "job_id": job_id,
            "input": params,
            "success": True,
            "mode": result.get("mode", "WFS BRGM"),
            "nb_ouvrages": result.get("nb_ouvrages", 0),
            "ouvrages": ouvrages,
            "closest": result.get("closest"),
            "georisques": result.get("georisques"),
            "code_site": code_site,
            "lat_centre": result.get("lat_centre"),
            "lon_centre": result.get("lon_centre"),
            "emprise_m": result.get("emprise_m"),
            "collected_at": collected_at,
        }
        zf.writestr("result.json", json.dumps(full_result, ensure_ascii=False, indent=2, default=str))

        # 2. ouvrages.csv (avec BOM UTF-8 pour Excel)
        def _fmt(v):
            """Formate une valeur pour CSV : None -> chaîne vide, float -> sans trailing zeros."""
            if v is None:
                return ""
            if isinstance(v, float):
                return str(int(v)) if v == int(v) else str(v)
            return str(v)

        csv_lines = [
            "Code BSS,Nature,Commune,Latitude,Longitude,"
            "Prof.totale(m),Prof.investigation(m),Niveau.eau(m),Niveau.eau.date,"
            "Altitude.NGF(m),Distance.centre(m),URL.InfoTerre,URL.ADES"
        ]
        for o in ouvrages:
            csv_lines.append(",".join([
                _fmt(o.get("code_bss")),
                _fmt(o.get("nature")),
                _fmt(o.get("commune")),
                _fmt(o.get("lat")),
                _fmt(o.get("lon")),
                _fmt(o.get("profondeur_totale")),
                _fmt(o.get("prof_investigation")),
                _fmt(o.get("niveau_eau")),
                _fmt(o.get("niveau_eau_date")),
                _fmt(o.get("altitude_ngf")),
                f"{o.get('distance_centre_m', 0):.1f}",
                _fmt(o.get("url_infoterre")),
                _fmt(o.get("url_ades")),
            ]))
        csv_content = "\ufeff" + "\n".join(csv_lines)
        zf.writestr("ouvrages.csv", csv_content.encode("utf-8"))

        # 3. carte.html
        map_html = result.get("map_html", "")
        if map_html:
            zf.writestr("carte.html", map_html.encode("utf-8"))

        # 4. README.txt
        geo = result.get("georisques") or {}
        readme = f"""BSS Explorer — Résultats de collecte
======================================
Site          : {code_site}
Coordonnées   : {params.get('lat', '')}, {params.get('lon', '')}
Emprise       : {params.get('emprise_m', 500)} m
Collecté le   : {collected_at}
Job ID        : {job_id}

Résultats
---------
Ouvrages trouvés  : {result.get('nb_ouvrages', 0)}
Mode de collecte  : {result.get('mode', 'WFS BRGM')}
Zone sismique     : {geo.get('zone_sismique', 'N/A')}
Aléa RGA          : {geo.get('alea_rga', 'N/A')}

Contenu du ZIP
--------------
result.json   — Données complètes au format JSON
ouvrages.csv  — Tableau CSV compatible Excel (BOM UTF-8)
carte.html    — Carte Leaflet interactive (ouvrir dans un navigateur)
README.txt    — Ce fichier

Sources
-------
BRGM WFS      : https://geoservices.brgm.fr/geologie
InfoTerre     : http://ficheinfoterre.brgm.fr
Géorisques    : https://www.georisques.gouv.fr
ADES          : https://ades.eaufrance.fr

© FERRAPD — BSS Explorer v9
"""
        zf.writestr("README.txt", readme.encode("utf-8"))

    zip_buffer.seek(0)
    filename = f"bss_{code_site}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get(
    "/jobs",
    summary="Lister tous les jobs",
    tags=["Jobs"],
)
def list_jobs():
    """Retourne la liste de tous les jobs (sans les résultats volumineux)."""
    with _jobs_lock:
        jobs = [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "code_site": j.get("params", {}).get("code_site", ""),
                "lat": j.get("params", {}).get("lat"),
                "lon": j.get("params", {}).get("lon"),
                "emprise_m": j.get("params", {}).get("emprise_m"),
                "nb_ouvrages": j.get("nb_ouvrages"),
                "created_at": j["created_at"],
                "completed_at": j.get("completed_at"),
                "error": j.get("error"),
            }
            for j in _jobs.values()
        ]
    return {"nb_jobs": len(jobs), "jobs": sorted(jobs, key=lambda x: x["created_at"], reverse=True)}


@app.delete(
    "/jobs/{job_id}",
    summary="Supprimer un job",
    tags=["Jobs"],
    status_code=204,
)
def delete_job(job_id: str):
    """Supprime un job et libère la mémoire associée."""
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable")
        del _jobs[job_id]


# ─── Point d'entrée ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("API_PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False, workers=4)
