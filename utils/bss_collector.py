#!/usr/bin/env python3
"""
bss_collector.py — Collecteur BSS BRGM autonome (mode production)
==================================================================
Usage :
    # Site unique (objet JSON)
    echo '{"code_site":"FRA034001MPL","lat":43.610769,"lon":3.876716,"emprise_m":500}' | python3 bss_collector.py

    # Liste de sites (tableau JSON)
    echo '[{"code_site":"FRA034001MPL","lat":43.610769,"lon":3.876716},{"lat":43.83,"lon":4.36}]' | python3 bss_collector.py

    # Depuis un fichier
    python3 bss_collector.py < sites.json
    python3 bss_collector.py --input sites.json
    python3 bss_collector.py --input sites.json --output resultats.json

    # Verbeux (logs sur stderr)
    python3 bss_collector.py --input sites.json --verbose

Dépendances :
    pip install requests beautifulsoup4 pyproj lxml

Retour JSON :
    Tableau de résultats, un élément par site :
    [
      {
        "input": { ...paramètres d'entrée... },
        "success": true,
        "mode": "WFS BRGM",
        "nb_ouvrages": 12,
        "ouvrages": [ ...liste des ouvrages... ],
        "georisques": { ...données Géorisques... },
        "error": null
      },
      ...
    ]
"""

import sys
import json
import math
import re
import argparse
import logging
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs, quote

try:
    import requests
    from bs4 import BeautifulSoup
    from pyproj import Transformer
    import xml.etree.ElementTree as ET
except ImportError as e:
    print(json.dumps({"error": f"Dépendance manquante : {e}. Installez avec : pip install requests beautifulsoup4 pyproj lxml"}))
    sys.exit(1)

# ─── Configuration ────────────────────────────────────────────────────────────
WFS_URL = "https://geoservices.brgm.fr/geologie"
INFOTERRE_BASE = "http://ficheinfoterre.brgm.fr/InfoterreFiche/ficheBss.action"
ADES_BASE = "https://ades.eaufrance.fr/Fiche/PtEau?Code="
GEORISQUES_SISMIQUE = "https://www.georisques.gouv.fr/api/v1/zonage_sismique"
GEORISQUES_RGA = "https://www.georisques.gouv.fr/api/v1/rga"
GEORISQUES_WMS_INONDATION = "https://mapsref.brgm.fr/wxs/georisques/risques"
USER_AGENT = "Mozilla/5.0 (compatible; BSS-Collector/1.0; production)"
REQUEST_TIMEOUT = 30
INFOTERRE_TIMEOUT = 20
GEORISQUES_TIMEOUT = 15

# ─── Projections Lambert 93 ↔ WGS84 ──────────────────────────────────────────
_l93_to_wgs84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
_wgs84_to_l93 = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)


def l93_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convertit des coordonnées Lambert 93 (x, y) en WGS84 (lat, lon)."""
    lon, lat = _l93_to_wgs84.transform(x, y)
    return lat, lon


def wgs84_to_l93(lat: float, lon: float) -> tuple[float, float]:
    """Convertit des coordonnées WGS84 (lat, lon) en Lambert 93 (x, y)."""
    x, y = _wgs84_to_l93.transform(lon, lat)
    return x, y


def compute_bbox_l93(lat: float, lon: float, half_side_m: float) -> str:
    """Calcule la bbox en Lambert 93 pour une emprise carrée centrée sur (lat, lon)."""
    x, y = wgs84_to_l93(lat, lon)
    return f"{x - half_side_m},{y - half_side_m},{x + half_side_m},{y + half_side_m}"


# ─── Distance haversine ───────────────────────────────────────────────────────
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en mètres entre deux points WGS84 (formule haversine)."""
    R = 6_371_000
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─── Collecte WFS BRGM ───────────────────────────────────────────────────────
def collect_wfs(lat: float, lon: float, emprise_m: float, log) -> Optional[list[dict]]:
    """
    Interroge le service WFS BRGM (BSS_EAU_POINT) et retourne la liste des ouvrages.
    Retourne None en cas d'erreur réseau, [] si aucun ouvrage dans l'emprise.
    """
    half_side = emprise_m / 2
    bbox = compute_bbox_l93(lat, lon, half_side)

    params = {
        "SERVICE": "WFS",
        "VERSION": "1.1.0",
        "REQUEST": "GetFeature",
        "TYPENAME": "ms:BSS_EAU_POINT",
        "BBOX": bbox,
        "SRSNAME": "EPSG:2154",
        "outputFormat": "GML2",
        "maxFeatures": "200",
    }

    log(f"  WFS BRGM : bbox={bbox[:60]}…")

    try:
        resp = requests.get(
            WFS_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/xml, text/xml", "User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        xml_text = resp.text

        if not xml_text or "ExceptionReport" in xml_text:
            log("  WFS : réponse invalide ou exception BRGM")
            return None

        # Parser le GML2
        root = ET.fromstring(xml_text)
        ns = {
            "wfs": "http://www.opengis.net/wfs",
            "gml": "http://www.opengis.net/gml",
            "ms": "http://mapserver.gis.umn.edu/mapserver",
        }

        members = root.findall(".//gml:featureMember", ns)
        if not members:
            log("  WFS : aucun ouvrage dans l'emprise")
            return []

        log(f"  WFS : {len(members)} ouvrage(s) trouvé(s)")

        ouvrages = []
        for member in members:
            feat = member.find("ms:BSS_EAU_POINT", ns)
            if feat is None:
                # Essayer sans namespace
                feat = member.find("BSS_EAU_POINT")
            if feat is None:
                feat = member

            def get_val(key: str) -> str:
                """Récupère la valeur d'un champ WFS (avec ou sans namespace ms:)."""
                for prefix in (f"ms:{key}", key, f"{{{ns['ms']}}}{key}"):
                    el = feat.find(prefix) if feat is not None else None
                    if el is not None and el.text:
                        return el.text.strip()
                return ""

            # Coordonnées Lambert 93
            geom = feat.find("ms:msGeometry/gml:Point/gml:pos", ns) or \
                   feat.find("ms:geometry/gml:Point/gml:pos", ns) or \
                   feat.find(".//gml:pos", ns) or \
                   feat.find(".//gml:coordinates", ns)

            x_l93, y_l93 = 0.0, 0.0
            if geom is not None and geom.text:
                parts = re.split(r"[\s,]+", geom.text.strip())
                if len(parts) >= 2:
                    try:
                        x_l93 = float(parts[0])
                        y_l93 = float(parts[1])
                    except ValueError:
                        pass

            if x_l93 == 0 or y_l93 == 0:
                continue

            ouv_lat, ouv_lon = l93_to_wgs84(x_l93, y_l93)
            dist = haversine_m(lat, lon, ouv_lat, ouv_lon)

            code_bss = get_val("CODE_BSS") or get_val("code_bss") or ""
            code_bss_id = code_bss.replace("/", "").replace(" ", "")

            # ID national BSS (ex: BSS002GQNQ)
            bss_id_national = get_val("bss_id") or get_val("BSS_ID") or ""
            if not bss_id_national:
                lien = get_val("lien_infoterre") or get_val("LIEN_INFOTERRE") or ""
                m = re.search(r"id=([A-Z0-9]+)$", lien)
                bss_id_national = m.group(1) if m else code_bss_id

            prof_str = (get_val("prof_invest") or get_val("PROF_INVEST") or
                        get_val("PROF_TOTALE") or get_val("prof_totale") or "")
            nature_str = (get_val("nature_pe") or get_val("NATURE_PE") or
                          get_val("NATURE") or get_val("nature") or "Inconnu")
            commune_str = (get_val("commune_actuelle") or get_val("NOM_COMMUNE") or
                           get_val("nom_commune") or "")
            aquifere_str = (get_val("carac_aquifere") or get_val("NOM_AQUIFERE") or
                            get_val("nom_aquifere") or "")
            bassin_str = (get_val("bassin_dce") or get_val("NOM_BASSIN_DCE") or
                          get_val("nom_bassin_dce") or "")
            ades_url = (get_val("lien_ades") or get_val("lien_bsseau") or
                        f"{ADES_BASE}{bss_id_national}")

            try:
                # Filtrer les valeurs non numeriques (ex: "None", "N/D")
                prof = float(prof_str) if prof_str and re.match(r'^[\d.,]+$', prof_str.strip()) else None
            except ValueError:
                prof = None

            ouvrages.append({
                "code_bss": code_bss,
                "code_bss_id": bss_id_national,
                "nom_commune": commune_str,
                "nature": nature_str,
                "profondeur_totale": prof,
                "altitude_ngf": None,
                "altitude_precision": "",
                "prof_investigation": None,
                "niveau_eau": None,
                "niveau_eau_date": "",
                "lat": round(ouv_lat, 7),
                "lon": round(ouv_lon, 7),
                "x_l93": round(x_l93, 2),
                "y_l93": round(y_l93, 2),
                "distance_centre_m": round(dist),
                "aquifere": aquifere_str,
                "bassin_dce": bassin_str,
                "log_geologique": [],
                "documents": [],
                "url_infoterre": f"{INFOTERRE_BASE}?id={bss_id_national}",
                "url_ades": ades_url,
            })

        # Trier par distance croissante
        ouvrages.sort(key=lambda o: o["distance_centre_m"])
        return ouvrages

    except requests.RequestException as e:
        log(f"  WFS erreur réseau : {e}")
        return None
    except ET.ParseError as e:
        log(f"  WFS erreur XML : {e}")
        return None


# ─── Scraping InfoTerre ───────────────────────────────────────────────────────
def scrape_infoterre(code_bss_id: str, log) -> dict:
    """
    Scrape la fiche InfoTerre d'un ouvrage BSS.
    Retourne : altitude_ngf, altitude_precision, log_geologique, documents.
    """
    url = f"{INFOTERRE_BASE}?id={code_bss_id}"
    result = {
        "altitude_ngf": None,
        "altitude_precision": "",
        "prof_investigation": None,
        "niveau_eau": None,
        "niveau_eau_date": "",
        "log_geologique": [],
        "documents": [],
    }

    try:
        resp = requests.get(
            url,
            timeout=INFOTERRE_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        resp.raise_for_status()

        # InfoTerre utilise l'encodage ISO-8859-1 (latin-1)
        html = resp.content.decode("iso-8859-1", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        # ── Altitude NGF ───────────────────────────────────────────────────────
        altitude_ngf = None
        altitude_precision = ""

        for tag in soup.find_all(["h3", "h4"]):
            if altitude_ngf is not None:
                break
            if "altitude" in tag.get_text().lower():
                parent_text = tag.parent.get_text() if tag.parent else ""
                m = re.search(r"([\d]+[.,][\d]+|[\d]+)\s*m", parent_text)
                if m:
                    altitude_ngf = float(m.group(1).replace(",", "."))
                    prec_m = re.search(r"\b(IGN|EPD|GPS|LIDAR)\b", parent_text, re.IGNORECASE)
                    if prec_m:
                        altitude_precision = prec_m.group(1).upper()

        # Fallback th/td
        if altitude_ngf is None:
            for tag in soup.find_all(["th", "td"]):
                if altitude_ngf is not None:
                    break
                if "altitude" in tag.get_text().lower():
                    sibling = tag.find_next_sibling("td")
                    if sibling:
                        sib_text = sibling.get_text()
                        # Eviter de matcher "None m" ou des textes non numeriques
                        m = re.search(r"(?<![A-Za-z])([\d]+[.,][\d]+|[\d]+)\s*m(?![A-Za-z])", sib_text)
                        if m:
                            altitude_ngf = float(m.group(1).replace(",", "."))

        result["altitude_ngf"] = altitude_ngf
        result["altitude_precision"] = altitude_precision

        # ── Profondeur atteinte + Niveau d'eau (section Description technique) ─────────────────────────────
        # Structure DOM : div#content_description_technique > h3 > span (next sibling)
        for h3 in soup.find_all("h3"):
            h3_text = h3.get_text().strip()
            span = h3.find_next_sibling("span")
            span_val = span.get_text().strip() if span else ""

            if "profondeur atteinte" in h3_text.lower():
                m = re.search(r"([\d]+[.,][\d]*|[\d]+)\s*m", span_val, re.IGNORECASE)
                if m:
                    result["prof_investigation"] = float(m.group(1).replace(",", "."))

            if "niveau d" in h3_text.lower() and "eau" in h3_text.lower():
                # Valeur : "12.5 m - 1970-12-19 00:00:00.0" ou "12.5 m"
                m_val = re.search(r"([\d]+[.,][\d]*|[\d]+)\s*m", span_val, re.IGNORECASE)
                if m_val:
                    result["niveau_eau"] = float(m_val.group(1).replace(",", "."))
                m_date = re.search(r"(\d{4}-\d{2}-\d{2})", span_val)
                if m_date:
                    result["niveau_eau_date"] = m_date.group(1)

        # ── Log géologique ─────────────────────────────────────────────────────
        log_geo = []
        for table in soup.find_all("table"):
            headers = [th.get_text().strip().lower() for th in table.find_all("th")]
            has_prof = any("profondeur" in h or "prof" in h for h in headers)
            has_litho = any("lithologie" in h or "nature" in h for h in headers)
            if not (has_prof and has_litho):
                continue

            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                first_cell = cells[0].get_text().strip()
                prof_match = (
                    re.search(r"de\s+([\d.,]+)\s+[àa]\s+([\d.,]+)", first_cell, re.IGNORECASE) or
                    re.search(r"([\d.,]+)\s*[-–]\s*([\d.,]+)", first_cell)
                )
                if not prof_match:
                    continue
                try:
                    prof_de = float(prof_match.group(1).replace(",", "."))
                    prof_a = float(prof_match.group(2).replace(",", "."))
                except ValueError:
                    continue

                litho = ""
                strati = ""
                if len(cells) >= 3:
                    litho = cells[1].get_text().strip()
                    strati = cells[2].get_text().strip()
                elif len(cells) == 2:
                    litho = cells[1].get_text().strip()

                if litho and not math.isnan(prof_de) and not math.isnan(prof_a):
                    log_geo.append({
                        "prof_de": prof_de,
                        "prof_a": prof_a,
                        "lithologie": litho,
                        "stratigraphie": strati,
                    })

        result["log_geologique"] = log_geo

        # ── Documents numérisés ────────────────────────────────────────────────
        documents = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text().strip()
            text_lower = text.lower()
            href_lower = href.lower()

            if not (
                "scan" in href_lower or ".tif" in href_lower or ".pdf" in href_lower or
                "coupe" in text_lower or "scan" in text_lower or "document" in text_lower
            ):
                continue

            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = f"http://ficheinfoterre.brgm.fr{href}"
            else:
                full_url = f"http://ficheinfoterre.brgm.fr/InfoterreFiche/{href}"

            doc_type = (
                "COUPE GÉOLOGIQUE" if "coupe" in text_lower else
                "LOG GÉOLOGIQUE" if "log" in text_lower else
                "DOCUMENT NUMÉRISÉ"
            )

            # Extraire les paramètres du scan TIFF
            scan_name = None
            scan_path = None
            proxy_url = None
            try:
                parsed = urlparse(full_url)
                qs = parse_qs(parsed.query)
                s_name = qs.get("name", [None])[0]
                s_path = qs.get("path", [None])[0]
                if s_name and s_path and re.search(r"\.TIF$", s_name, re.IGNORECASE):
                    scan_name = s_name
                    scan_path = s_path
                    proxy_url = f"/api/bss/scan-proxy?name={quote(s_name)}&path={quote(s_path)}"
            except Exception:
                pass

            documents.append({
                "nom": text or href.split("?")[0].split("/")[-1] or "Document",
                "type": doc_type,
                "url": full_url,
                "scan_name": scan_name,
                "scan_path": scan_path,
                "proxy_url": proxy_url,
            })

        result["documents"] = documents

    except requests.RequestException as e:
        log(f"    InfoTerre erreur réseau pour {code_bss_id}: {e}")
    except Exception as e:
        log(f"    InfoTerre erreur pour {code_bss_id}: {e}")

    return result


# ─── Scraping Géorisques ──────────────────────────────────────────────────────
def scrape_georisques(lat: float, lon: float, log) -> dict:
    """
    Interroge les APIs Géorisques pour obtenir le zonage sismique et l'aléa RGA.
    """
    result = {
        "zone_sismique": "N/D",
        "code_zone_sismique": "",
        "alea_rga": "N/D",
        "code_alea_rga": "",
    }

    # Zonage sismique
    try:
        log("  Géorisques : zonage sismique…")
        resp = requests.get(
            GEORISQUES_SISMIQUE,
            params={"latlon": f"{lon},{lat}"},
            timeout=GEORISQUES_TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("data") and len(data["data"]) > 0:
            z = data["data"][0]
            result["code_zone_sismique"] = z.get("code_zone", "")
            result["zone_sismique"] = z.get("zone_sismicite", f"Zone {z.get('code_zone', '')}")
    except Exception as e:
        log(f"    Géorisques sismique erreur : {e}")

    # Aléa RGA
    try:
        log("  Géorisques : aléa RGA…")
        resp = requests.get(
            GEORISQUES_RGA,
            params={"latlon": f"{lon},{lat}"},
            timeout=GEORISQUES_TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        result["code_alea_rga"] = data.get("codeExposition", "")
        result["alea_rga"] = data.get("exposition", f"Code {data.get('codeExposition', '')}")
    except Exception as e:
        log(f"    Géorisques RGA erreur : {e}")

    # Zone inondable (PPRI) via WMS GetFeatureInfo
    try:
        log("  Géorisques : zone inondable (PPRI)\u2026")
        delta = 0.0005  # ~50m de résolution
        wms_params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": "PPRN_ZONE_INOND",
            "QUERY_LAYERS": "PPRN_ZONE_INOND",
            "INFO_FORMAT": "text/plain",
            "SRS": "EPSG:4326",
            "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}",
            "WIDTH": "256",
            "HEIGHT": "256",
            "X": "128",
            "Y": "128",
            "FEATURE_COUNT": "10",
        }
        resp = requests.get(
            GEORISQUES_WMS_INONDATION,
            params=wms_params,
            timeout=GEORISQUES_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        text = resp.text

        if "Search returned no results" in text or "Feature" not in text:
            result["zone_inondable"] = "Non"
            result["ppri_nom"] = ""
            result["ppri_zone"] = ""
            result["ppri_reglement"] = ""
            result["ppri_code_zone"] = ""
            result["ppri_etat"] = ""
            result["ppri_date_approbation"] = ""
            result["ppri_url_reglement"] = ""
        else:
            # Parser le texte plain
            result["zone_inondable"] = "Oui"
            fields = {}
            for line in text.splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("Feature") and not line.startswith("Layer"):
                    key, _, val = line.partition("=")
                    fields[key.strip()] = val.strip().strip("'")

            result["ppri_nom"] = fields.get("nom_ppr", "")
            result["ppri_zone"] = fields.get("libelle_zone", "")
            result["ppri_reglement"] = fields.get("libelle_reglement_standardise", "")
            result["ppri_code_zone"] = fields.get("code_zone_reglement", "")
            result["ppri_etat"] = fields.get("etat", "")
            result["ppri_date_approbation"] = fields.get("date_approbation", "")
            result["ppri_url_reglement"] = fields.get("url_reglement_zone", "")

            # Déterminer le niveau d'aléa à partir du code zone et du libellé
            code_z = result["ppri_code_zone"].upper()
            lib_z = result["ppri_zone"].lower()
            lib_r = result["ppri_reglement"].lower()
            if "interdiction" in lib_r or any(x in code_z for x in ["R", "TF", "F1"]):
                result["niveau_alea_inondation"] = "Fort"
            elif any(x in lib_z for x in ["précaution", "precaution"]) or "hors zone" in lib_r:
                result["niveau_alea_inondation"] = "Précaution"
            elif any(x in code_z for x in ["B", "M", "Z2"]):
                result["niveau_alea_inondation"] = "Moyen"
            else:
                result["niveau_alea_inondation"] = "Prescriptions"

        log(f"  Géorisques inondation : {result.get('zone_inondable', 'N/D')}")
        if result.get("zone_inondable") == "Oui":
            log(f"    PPRI: {result['ppri_nom']} | Zone: {result['ppri_zone']} | Aléa: {result.get('niveau_alea_inondation', '')}")
    except Exception as e:
        log(f"    Géorisques inondation erreur : {e}")
        result["zone_inondable"] = "N/D"

    # ── Profondeur Hors Gel Fondations (PHGF) ───────────────────────────────
    # Source : NF P 94-261 / DTU 13.1
    # Formule : H = H0 + (A - 150) / 4000  si A > 150 m
    # H0 dépend de la zone climatique (département)
    # Altitude récupérée via Open-Meteo Elevation API
    try:
        log("  PHGF : calcul profondeur hors gel…")
        # 1. Récupérer le département via geo.api.gouv.fr
        resp_dept = requests.get(
            "https://geo.api.gouv.fr/communes",
            params={"lat": lat, "lon": lon, "fields": "codeDepartement", "limit": 1},
            timeout=10,
            headers={"User-Agent": USER_AGENT},
        )
        dept_code = ""
        if resp_dept.status_code == 200 and resp_dept.json():
            dept_code = resp_dept.json()[0].get("codeDepartement", "")

        # 2. Récupérer l'altitude via Open-Meteo
        resp_alt = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": lat, "longitude": lon},
            timeout=10,
        )
        altitude = None
        if resp_alt.status_code == 200:
            elev_data = resp_alt.json()
            elevations = elev_data.get("elevation", [])
            if elevations:
                altitude = elevations[0]

        # 3. Table H0 par département (NF P 94-261 / DTU 13.12)
        DEPT_H0 = {
            # Zone 1 — Gel faible (0.50 m)
            "06": 0.50, "11": 0.50, "13": 0.50, "17": 0.50, "2A": 0.50, "2B": 0.50,
            "22": 0.50, "29": 0.50, "30": 0.50, "33": 0.50, "34": 0.50, "40": 0.50,
            "44": 0.50, "56": 0.50, "64": 0.50, "66": 0.50, "83": 0.50, "85": 0.50,
            # Zone 3 — Gel sévère (0.80 m)
            "02": 0.80, "03": 0.80, "08": 0.80, "10": 0.80, "15": 0.80, "23": 0.80,
            "25": 0.80, "39": 0.80, "51": 0.80, "52": 0.80, "54": 0.80, "55": 0.80,
            "57": 0.80, "59": 0.80, "60": 0.80, "62": 0.80, "70": 0.80, "80": 0.80,
            "88": 0.80, "90": 0.80,
            # Zone 4 — Gel très sévère (0.90 m)
            "04": 0.90, "05": 0.90, "65": 0.90, "67": 0.90, "68": 0.90,
            "73": 0.90, "74": 0.90,
        }
        # Défaut zone 2 (0.60 m) pour les départements non listés
        h0 = DEPT_H0.get(dept_code, 0.60)

        # 4. Calcul PHGF
        if altitude is not None and altitude > 150:
            phgf = h0 + (altitude - 150) / 4000
        else:
            phgf = h0
        phgf = round(max(phgf, 0.50), 2)

        # Zone label
        if h0 <= 0.50:
            zone_label = "Zone 1 (gel faible)"
        elif h0 <= 0.60:
            zone_label = "Zone 2 (gel modéré)"
        elif h0 <= 0.80:
            zone_label = "Zone 3 (gel sévère)"
        else:
            zone_label = "Zone 4 (gel très sévère)"

        result["PHGF"] = phgf
        result["PHGF_cm"] = int(phgf * 100)
        result["zone_gel"] = zone_label
        result["H0_gel"] = h0
        result["altitude_site"] = altitude
        result["dept_code"] = dept_code

        log(f"  PHGF : {phgf:.2f} m ({int(phgf*100)} cm) | {zone_label} | H0={h0} m | Alt={altitude} m | Dépt={dept_code}")
    except Exception as e:
        log(f"    PHGF erreur : {e}")
        result["PHGF"] = None
        result["PHGF_cm"] = None
        result["zone_gel"] = "N/D"

    log(f"  Géorisques : sismique={result['zone_sismique']} | RGA={result['alea_rga']} | inondation={result.get('zone_inondable', 'N/D')} | PHGF={result.get('PHGF', 'N/D')} m")
    return result


# ─── Collecte complète d'un site ─────────────────────────────────────────────
def collect_site(site_input: dict, verbose: bool = False) -> dict:
    """
    Collecte complète pour un site BSS.

    Paramètres d'entrée (dict) :
        lat         : float  — latitude WGS84 (obligatoire, ou alias 'latitude')
        lon         : float  — longitude WGS84 (obligatoire, ou alias 'longitude')
        code_site   : str    — code site BSS (optionnel, ex: FRA034001MPL)
        emprise_m   : int    — demi-côté de la zone de recherche en mètres (défaut: 500, min: 100, max: 2000)

    Retour (dict) :
        input       : dict   — paramètres d'entrée normalisés
        success     : bool   — True si la collecte a réussi
        mode        : str    — "WFS BRGM" ou "Erreur"
        nb_ouvrages : int    — nombre d'ouvrages trouvés
        ouvrages    : list   — liste des ouvrages avec toutes les données
        georisques  : dict   — données Géorisques (sismique + RGA)
        error       : str|None — message d'erreur si success=False
    """
    messages = []

    def log(msg: str):
        messages.append(msg)
        if verbose:
            print(f"[BSS] {msg}", file=sys.stderr)

    # ── Normalisation des paramètres d'entrée ──────────────────────────────────
    lat = float(site_input.get("lat") or site_input.get("latitude") or 0)
    lon = float(site_input.get("lon") or site_input.get("longitude") or 0)
    code_site = str(site_input.get("code_site") or site_input.get("codeSite") or site_input.get("code") or "SITE").upper()
    emprise_m = max(100, min(2000, int(site_input.get("emprise_m") or site_input.get("emprise") or 500)))

    normalized_input = {
        "code_site": code_site,
        "lat": lat,
        "lon": lon,
        "emprise_m": emprise_m,
    }

    if lat == 0 or lon == 0:
        return {
            "input": normalized_input,
            "success": False,
            "mode": "Erreur",
            "nb_ouvrages": 0,
            "ouvrages": [],
            "georisques": None,
            "error": "Latitude et longitude requises (non nulles)",
            "log": messages,
        }

    log(f"=== Collecte {code_site} | lat={lat} lon={lon} emprise={emprise_m}m ===")

    # ── 1. Collecte WFS BRGM ──────────────────────────────────────────────────
    log("Étape 1/3 : WFS BRGM…")
    ouvrages = collect_wfs(lat, lon, emprise_m, log)

    if ouvrages is None:
        return {
            "input": normalized_input,
            "success": False,
            "mode": "Erreur",
            "nb_ouvrages": 0,
            "ouvrages": [],
            "georisques": None,
            "error": "Échec de la connexion au WFS BRGM",
            "log": messages,
        }

    if len(ouvrages) == 0:
        log("Aucun ouvrage trouvé dans l'emprise.")
        georisques = scrape_georisques(lat, lon, log)
        return {
            "input": normalized_input,
            "success": True,
            "mode": "WFS BRGM",
            "nb_ouvrages": 0,
            "ouvrages": [],
            "georisques": georisques,
            "error": None,
            "log": messages,
        }

    # ── 2. Enrichissement InfoTerre ───────────────────────────────────────────
    log(f"Étape 2/3 : Scraping InfoTerre ({len(ouvrages)} ouvrage(s))…")
    for i, ouv in enumerate(ouvrages):
        code_id = ouv["code_bss_id"]
        if not code_id:
            continue
        log(f"  [{i + 1}/{len(ouvrages)}] InfoTerre : {code_id}")
        info = scrape_infoterre(code_id, log)
        ouv["altitude_ngf"] = info["altitude_ngf"]
        ouv["altitude_precision"] = info["altitude_precision"]
        ouv["prof_investigation"] = info["prof_investigation"]
        ouv["niveau_eau"] = info["niveau_eau"]
        ouv["niveau_eau_date"] = info["niveau_eau_date"]
        ouv["log_geologique"] = info["log_geologique"]
        ouv["documents"] = info["documents"]

    # ── 3. Données Géorisques ─────────────────────────────────────────────────
    log("Étape 3/3 : Géorisques…")
    georisques = scrape_georisques(lat, lon, log)

    # ── Résultat final ────────────────────────────────────────────────────────
    closest = ouvrages[0] if ouvrages else None

    log(f"=== Collecte terminée : {len(ouvrages)} ouvrage(s) ===")

    return {
        "input": normalized_input,
        "success": True,
        "mode": "WFS BRGM",
        "nb_ouvrages": len(ouvrages),
        "ouvrages": ouvrages,
        "closest": closest,
        "georisques": georisques,
        "error": None,
        "log": messages,
    }


# ─── Point d'entrée principal ─────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Collecteur BSS BRGM — lit un JSON (site unique ou liste) et retourne les données collectées en JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input", "-i",
        metavar="FILE",
        help="Fichier JSON d'entrée (défaut : stdin)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Fichier JSON de sortie (défaut : stdout)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Afficher les messages de progression sur stderr",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Ne pas inclure le champ 'log' dans la sortie JSON",
    )
    args = parser.parse_args()

    # Lecture de l'entrée
    try:
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            raw = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        error_out = [{"input": {}, "success": False, "error": f"JSON invalide : {e}", "nb_ouvrages": 0, "ouvrages": [], "georisques": None}]
        print(json.dumps(error_out, ensure_ascii=False, indent=2))
        sys.exit(1)
    except FileNotFoundError as e:
        error_out = [{"input": {}, "success": False, "error": str(e), "nb_ouvrages": 0, "ouvrages": [], "georisques": None}]
        print(json.dumps(error_out, ensure_ascii=False, indent=2))
        sys.exit(1)

    # Normaliser en liste
    if isinstance(raw, dict):
        sites = [raw]
    elif isinstance(raw, list):
        sites = raw
    else:
        error_out = [{"input": {}, "success": False, "error": "L'entrée doit être un objet JSON ou un tableau d'objets", "nb_ouvrages": 0, "ouvrages": [], "georisques": None}]
        print(json.dumps(error_out, ensure_ascii=False, indent=2))
        sys.exit(1)

    if len(sites) > 50:
        error_out = [{"input": {}, "success": False, "error": "Maximum 50 sites par appel", "nb_ouvrages": 0, "ouvrages": [], "georisques": None}]
        print(json.dumps(error_out, ensure_ascii=False, indent=2))
        sys.exit(1)

    # Collecte séquentielle
    results = []
    for i, site in enumerate(sites):
        if args.verbose:
            print(f"\n[BSS] --- Site {i + 1}/{len(sites)} ---", file=sys.stderr)
        result = collect_site(site, verbose=args.verbose)
        if args.no_log:
            result.pop("log", None)
        results.append(result)

    # Écriture de la sortie
    output_json = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        if args.verbose:
            print(f"\n[BSS] Résultats écrits dans : {args.output}", file=sys.stderr)
    else:
        print(output_json)


def collect_bss(lat: float, lon: float, emprise_m: float = 1000,
                code_site: str = None, verbose: bool = False) -> dict:
    """
    Alias public de collect_site() pour l'interface Streamlit.
    Retourne le résultat d'un site unique (dict).
    """
    site_input = {"lat": lat, "lon": lon, "emprise_m": emprise_m}
    if code_site:
        site_input["code_site"] = code_site
    return collect_site(site_input, verbose=verbose)


def parse_batch_input(text: str) -> list[dict]:
    """
    Parse une saisie texte multi-sites pour le mode batch de l'interface Streamlit.
    Formats acceptés (une entrée par ligne) :
      - JSON objet  : {"lat": 43.61, "lon": 3.88, "emprise_m": 500}
      - CSV simple  : 43.61,3.88,500   (lat,lon[,emprise_m])
      - Coordonnées : 43.61 3.88
    Retourne une liste de dicts avec les clés lat, lon, emprise_m.
    """
    results = []
    for raw_line in text.strip().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Tentative JSON
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if "lat" in obj and "lon" in obj:
                    obj.setdefault("emprise_m", 1000)
                    results.append(obj)
                    continue
            except json.JSONDecodeError:
                pass
        # Tentative CSV ou espace
        sep = "," if "," in line else None
        parts = [p.strip() for p in (line.split(sep) if sep else line.split())]
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            emprise_m = float(parts[2]) if len(parts) > 2 else 1000
            results.append({"lat": lat, "lon": lon, "emprise_m": emprise_m})
        except (IndexError, ValueError):
            pass  # Ligne ignorée si non parseable
    return results


if __name__ == "__main__":
    main()
