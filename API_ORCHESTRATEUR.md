# API Orchestrateur BSS Explorer — Documentation

**Version :** 9.0.0  
**Base URL :** `http://votre-serveur/api` (ou `http://localhost:8001` en local)  
**Format :** JSON (UTF-8)  
**Authentification :** Aucune (réseau interne) — à sécuriser avec un reverse proxy si exposé publiquement

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Endpoints](#2-endpoints)
3. [Modèles de données](#3-modèles-de-données)
4. [Exemples Python — Site unique](#4-exemples-python--site-unique)
5. [Exemples Python — Batch (liste de sites)](#5-exemples-python--batch-liste-de-sites)
6. [Intégration dans une brique métier](#6-intégration-dans-une-brique-métier)
7. [Codes d'erreur](#7-codes-derreur)

---

## 1. Vue d'ensemble

L'API orchestrateur expose une interface REST asynchrone pour soumettre des recherches BSS (Banque du Sous-Sol BRGM), suivre leur progression et récupérer les résultats. Le traitement est asynchrone : vous soumettez une recherche, obtenez un `job_id`, puis interrogez le statut jusqu'à complétion.

**Flux typique :**

```
POST /search → job_id → GET /jobs/{job_id}/status (polling) → GET /jobs/{job_id}/result
```

**Temps de traitement typique :**

| Nombre d'ouvrages | Durée estimée |
|---|---|
| 0–5 ouvrages | 10–30 secondes |
| 5–20 ouvrages | 30–90 secondes |
| 20–50 ouvrages | 90–300 secondes |

---

## 2. Endpoints

### GET /health

Vérifie que l'API est opérationnelle.

**Réponse :**
```json
{
  "status": "ok",
  "version": "9.0.0",
  "timestamp": "2026-04-26T10:00:00Z",
  "uptime_s": 3600.5
}
```

---

### POST /search

Soumet une recherche BSS pour un site donné. Retourne immédiatement un `job_id`.

**Corps de la requête :**

```json
{
  "lat": 43.836699,
  "lon": 4.360054,
  "code_site": "FRA030001NIM",
  "emprise_m": 500
}
```

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `lat` | float | Oui | Latitude WGS84 (ex: 43.836699) |
| `lon` | float | Oui | Longitude WGS84 (ex: 4.360054) |
| `code_site` | string | Non | Code site BSS (ex: "FRA030001NIM") |
| `emprise_m` | integer | Non | Rayon de recherche en mètres (défaut: 500, max: 5000) |

**Réponse (202 Accepted) :**
```json
{
  "job_id": "8896314b-98ae-40af-b6bf-e61874129abd",
  "status": "pending",
  "message": "Collecte démarrée.",
  "status_url": "/jobs/8896314b-98ae-40af-b6bf-e61874129abd/status",
  "result_url": "/jobs/8896314b-98ae-40af-b6bf-e61874129abd/result",
  "zip_url": "/jobs/8896314b-98ae-40af-b6bf-e61874129abd/zip"
}
```

---

### POST /search/batch

Soumet une liste de sites à traiter en séquence. Maximum 50 sites par appel.

**Corps de la requête :**

```json
[
  {"lat": 43.836699, "lon": 4.360054, "code_site": "FRA030001NIM", "emprise_m": 500},
  {"lat": 43.610769, "lon": 3.876716, "code_site": "FRA034001MPL", "emprise_m": 300}
]
```

**Réponse (202 Accepted) :**
```json
{
  "job_id": "abc123...",
  "status": "pending",
  "nb_sites": 2,
  "message": "Batch de 2 sites démarré.",
  "status_url": "/jobs/abc123.../status"
}
```

---

### GET /jobs/{job_id}/status

Interroge le statut d'un job en cours.

**Réponse :**
```json
{
  "job_id": "8896314b-98ae-40af-b6bf-e61874129abd",
  "status": "running",
  "progress": "Scraping InfoTerre : 09655X0141/P (3/18)",
  "nb_ouvrages": null,
  "created_at": "2026-04-26T10:00:00Z",
  "elapsed_s": 45.2
}
```

**Valeurs possibles de `status` :**

| Valeur | Description |
|---|---|
| `pending` | En attente de démarrage |
| `running` | Collecte en cours |
| `completed` | Terminé avec succès |
| `failed` | Erreur lors de la collecte |

---

### GET /jobs/{job_id}/result

Récupère le résultat complet d'un job terminé (JSON).

**Réponse (200 OK) :**
```json
{
  "job_id": "8896314b-...",
  "input": {"lat": 43.836699, "lon": 4.360054, "code_site": "FRA030001NIM", "emprise_m": 500},
  "success": true,
  "nb_ouvrages": 18,
  "ouvrages": [
    {
      "code_bss": "09655X0141/P",
      "nature": "Puits",
      "commune": "Nîmes",
      "lat": 43.838,
      "lon": 4.362,
      "profondeur_totale": 12.0,
      "prof_investigation": 10.7,
      "niveau_eau": 8.5,
      "niveau_eau_date": "2021-01-05",
      "altitude_ngf": 67.0,
      "distance_centre_m": 245.3,
      "url_infoterre": "https://infoterre.brgm.fr/...",
      "url_ades": "https://ades.eaufrance.fr/..."
    }
  ],
  "georisques": {
    "zone_sismique": "2 - FAIBLE",
    "alea_rga": "Exposition moyenne"
  },
  "collected_at": "2026-04-26T10:01:30Z"
}
```

---

### GET /jobs/{job_id}/zip

Télécharge un fichier ZIP contenant :
- `result.json` — données complètes au format JSON
- `ouvrages.csv` — tableau CSV (compatible Excel, BOM UTF-8)
- `README.txt` — description du contenu

**Réponse :** Fichier ZIP binaire (`Content-Type: application/zip`)

---

## 3. Modèles de données

### Ouvrage BSS

```json
{
  "code_bss": "09655X0141/P",
  "nature": "Puits",
  "commune": "Nîmes",
  "departement": "Gard",
  "lat": 43.838,
  "lon": 4.362,
  "profondeur_totale": 12.0,
  "prof_investigation": 10.7,
  "niveau_eau": 8.5,
  "niveau_eau_date": "2021-01-05",
  "altitude_ngf": 67.0,
  "altitude_precision": "NGF",
  "distance_centre_m": 245.3,
  "url_infoterre": "https://infoterre.brgm.fr/...",
  "url_ades": "https://ades.eaufrance.fr/...",
  "log_geologique": [
    {"prof_de": 0.0, "prof_a": 2.5, "lithologie": "Remblais"},
    {"prof_de": 2.5, "prof_a": 10.7, "lithologie": "Calcaire"}
  ],
  "documents": [
    {"nom": "Coupe géologique", "url": "https://..."}
  ]
}
```

### Géorisques

```json
{
  "zone_sismique": "2 - FAIBLE",
  "alea_rga": "Exposition moyenne",
  "lat": 43.836699,
  "lon": 4.360054
}
```

---

## 4. Exemples Python — Site unique

### Installation

```bash
pip install requests
```

### Collecte simple avec polling

```python
import requests
import time

BASE_URL = "http://localhost:8001"  # ou http://votre-serveur/api

def collect_site(lat: float, lon: float, code_site: str = None, emprise_m: int = 500) -> dict:
    """Soumet une collecte BSS et attend le résultat."""
    
    # 1. Soumettre la recherche
    payload = {"lat": lat, "lon": lon, "emprise_m": emprise_m}
    if code_site:
        payload["code_site"] = code_site
    
    response = requests.post(f"{BASE_URL}/search", json=payload)
    response.raise_for_status()
    job_id = response.json()["job_id"]
    print(f"Job démarré : {job_id}")
    
    # 2. Polling du statut
    while True:
        status_resp = requests.get(f"{BASE_URL}/jobs/{job_id}/status")
        status_data = status_resp.json()
        status = status_data["status"]
        progress = status_data.get("progress", "")
        
        if progress:
            print(f"  [{status}] {progress}")
        
        if status == "completed":
            print(f"  Terminé : {status_data['nb_ouvrages']} ouvrage(s)")
            break
        elif status == "failed":
            raise RuntimeError(f"Collecte échouée : {status_data.get('error')}")
        
        time.sleep(3)
    
    # 3. Récupérer le résultat
    result_resp = requests.get(f"{BASE_URL}/jobs/{job_id}/result")
    result_resp.raise_for_status()
    return result_resp.json()


# Exemple d'utilisation
if __name__ == "__main__":
    result = collect_site(
        lat=43.836699,
        lon=4.360054,
        code_site="FRA030001NIM",
        emprise_m=500
    )
    
    print(f"\nSite : {result['code_site']}")
    print(f"Ouvrages : {result['nb_ouvrages']}")
    print(f"Zone sismique : {result['georisques']['zone_sismique']}")
    
    for ouv in result["ouvrages"][:3]:
        print(f"  - {ouv['code_bss']} | {ouv['nature']} | prof={ouv.get('profondeur_totale')}m")
```

### Téléchargement du ZIP

```python
import requests

def download_zip(job_id: str, output_path: str):
    """Télécharge le ZIP d'un job terminé."""
    response = requests.get(f"{BASE_URL}/jobs/{job_id}/zip", stream=True)
    response.raise_for_status()
    
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    print(f"ZIP sauvegardé : {output_path}")

# Utilisation après collect_site()
download_zip(job_id, f"resultats_{code_site}.zip")
```

---

## 5. Exemples Python — Batch (liste de sites)

```python
import requests
import time
import json

BASE_URL = "http://localhost:8001"

def collect_batch(sites: list[dict], output_file: str = None) -> list[dict]:
    """
    Collecte une liste de sites BSS en séquence.
    
    Args:
        sites: Liste de dicts avec lat, lon, code_site (optionnel), emprise_m (optionnel)
        output_file: Chemin du fichier JSON de sortie (optionnel)
    
    Returns:
        Liste des résultats pour chaque site
    """
    print(f"Démarrage batch : {len(sites)} site(s)")
    
    # Soumettre le batch
    response = requests.post(f"{BASE_URL}/search/batch", json=sites)
    response.raise_for_status()
    job_data = response.json()
    job_id = job_data["job_id"]
    print(f"Batch job : {job_id}")
    
    # Polling
    while True:
        status_resp = requests.get(f"{BASE_URL}/jobs/{job_id}/status")
        status_data = status_resp.json()
        status = status_data["status"]
        progress = status_data.get("progress", "")
        
        if progress:
            print(f"  [{status}] {progress}")
        
        if status == "completed":
            print(f"  Batch terminé.")
            break
        elif status == "failed":
            raise RuntimeError(f"Batch échoué : {status_data.get('error')}")
        
        time.sleep(5)
    
    # Récupérer les résultats
    result_resp = requests.get(f"{BASE_URL}/jobs/{job_id}/result")
    result_resp.raise_for_status()
    results = result_resp.json()
    
    # Sauvegarder si demandé
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"Résultats sauvegardés : {output_file}")
    
    return results


# ── Exemple d'utilisation ─────────────────────────────────────────────────────
if __name__ == "__main__":
    
    # Liste de sites à traiter
    sites = [
        {"lat": 43.836699, "lon": 4.360054, "code_site": "FRA030001NIM", "emprise_m": 500},
        {"lat": 43.610769, "lon": 3.876716, "code_site": "FRA034001MPL", "emprise_m": 300},
        {"lat": 44.837789, "lon": -0.579180, "code_site": "FRA033001BDX", "emprise_m": 500},
    ]
    
    results = collect_batch(sites, output_file="batch_resultats.json")
    
    # Résumé
    print("\n=== RÉSUMÉ ===")
    for r in results:
        status = "OK" if r["success"] else "ERREUR"
        code = r["input"].get("code_site", "?")
        nb = r.get("nb_ouvrages", 0)
        geo = r.get("georisques") or {}
        zone = geo.get("zone_sismique", "N/A")
        print(f"  [{status}] {code} : {nb} ouvrages | Zone sismique : {zone}")
```

### Lecture depuis un fichier JSON

```python
import json

# Charger une liste depuis un fichier JSON
with open("mes_sites.json", "r", encoding="utf-8") as f:
    sites = json.load(f)

# Format du fichier mes_sites.json :
# [
#   {"lat": 43.836699, "lon": 4.360054, "code_site": "FRA030001NIM"},
#   {"lat": 43.610769, "lon": 3.876716}
# ]

results = collect_batch(sites, output_file="resultats_complets.json")
```

---

## 6. Intégration dans une brique métier

### Classe client réutilisable

```python
import requests
import time
from typing import Optional

class BssExplorerClient:
    """Client Python pour l'API BSS Explorer."""
    
    def __init__(self, base_url: str = "http://localhost:8001", timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
    
    def health(self) -> dict:
        """Vérifie la disponibilité de l'API."""
        return self.session.get(f"{self.base_url}/health").json()
    
    def search(self, lat: float, lon: float, code_site: str = None, emprise_m: int = 500) -> str:
        """Soumet une recherche et retourne le job_id."""
        payload = {"lat": lat, "lon": lon, "emprise_m": emprise_m}
        if code_site:
            payload["code_site"] = code_site
        resp = self.session.post(f"{self.base_url}/search", json=payload)
        resp.raise_for_status()
        return resp.json()["job_id"]
    
    def wait_for_completion(self, job_id: str, poll_interval: int = 3) -> dict:
        """Attend la fin d'un job et retourne le statut final."""
        start = time.time()
        while time.time() - start < self.timeout:
            resp = self.session.get(f"{self.base_url}/jobs/{job_id}/status")
            data = resp.json()
            if data["status"] in ("completed", "failed"):
                return data
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} non terminé après {self.timeout}s")
    
    def get_result(self, job_id: str) -> dict:
        """Récupère le résultat JSON d'un job terminé."""
        resp = self.session.get(f"{self.base_url}/jobs/{job_id}/result")
        resp.raise_for_status()
        return resp.json()
    
    def get_zip(self, job_id: str) -> bytes:
        """Télécharge le ZIP d'un job terminé."""
        resp = self.session.get(f"{self.base_url}/jobs/{job_id}/zip")
        resp.raise_for_status()
        return resp.content
    
    def collect_and_wait(self, lat: float, lon: float, 
                          code_site: str = None, emprise_m: int = 500) -> dict:
        """Raccourci : soumet + attend + retourne le résultat."""
        job_id = self.search(lat, lon, code_site, emprise_m)
        status = self.wait_for_completion(job_id)
        if status["status"] == "failed":
            raise RuntimeError(f"Collecte échouée : {status.get('error')}")
        return self.get_result(job_id)


# ── Utilisation ───────────────────────────────────────────────────────────────
client = BssExplorerClient(base_url="http://mon-serveur/api")

# Vérification
print(client.health())

# Collecte simple
result = client.collect_and_wait(
    lat=43.836699, lon=4.360054,
    code_site="FRA030001NIM", emprise_m=500
)
print(f"{result['nb_ouvrages']} ouvrages trouvés")

# Téléchargement du ZIP
zip_bytes = client.get_zip(result["job_id"])
with open("resultats.zip", "wb") as f:
    f.write(zip_bytes)
```

---

## 7. Codes d'erreur

| Code HTTP | Signification | Action recommandée |
|---|---|---|
| 200 | Succès | — |
| 202 | Job accepté (asynchrone) | Interroger `/status` |
| 400 | Paramètres invalides | Vérifier le corps de la requête |
| 404 | Job introuvable | Vérifier le `job_id` |
| 409 | Job pas encore terminé | Attendre et réessayer |
| 422 | Erreur de validation | Vérifier les types de données |
| 500 | Erreur serveur | Consulter les logs (`docker compose logs app`) |

### Exemple de réponse d'erreur

```json
{
  "detail": "Paramètre 'lat' invalide : doit être compris entre -90 et 90"
}
```

---

*Documentation API BSS Explorer v9.0.0 — FERRAPD*
