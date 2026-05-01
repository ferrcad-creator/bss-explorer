"""
BSS Explorer — Application Streamlit
=====================================
Collecte hydrogéologique automatisée depuis la Banque du Sous-Sol BRGM.
"""

import json
import io
import sys
import os
import time
import threading
from datetime import datetime
from typing import Optional

import streamlit as st
import pandas as pd

# Ajouter le répertoire courant au path pour les imports relatifs
sys.path.insert(0, os.path.dirname(__file__))

from utils.bss_collector import collect_bss, parse_batch_input
from utils.db import init_db, upsert_session, list_sessions, get_session, delete_session

# ─── Configuration de la page ─────────────────────────────────────────────────
st.set_page_config(
    page_title="BSS Explorer — FERRAPD",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS personnalisé ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #0a1628 0%, #1a2744 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        border: 1px solid #1e3a5f;
    }
    .main-header h1 { color: #4fc3f7; margin: 0; font-size: 1.8rem; }
    .main-header p { color: #90a4ae; margin: 0.3rem 0 0 0; font-size: 0.9rem; }
    .metric-card {
        background: #0d1b2a;
        border: 1px solid #1e3a5f;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .metric-card .value { font-size: 2rem; font-weight: bold; color: #4fc3f7; }
    .metric-card .label { font-size: 0.8rem; color: #90a4ae; margin-top: 0.2rem; }
    .ouvrage-card {
        background: #0d1b2a;
        border: 1px solid #1e3a5f;
        border-radius: 8px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.5rem;
    }
    .badge-ok { background: #1b5e20; color: #a5d6a7; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
    .badge-warn { background: #e65100; color: #ffcc80; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
    .stButton > button { border-radius: 8px; }
    div[data-testid="stSidebar"] { background: #0a1628; }
</style>
""", unsafe_allow_html=True)

# ─── Initialisation DB ────────────────────────────────────────────────────────
@st.cache_resource
def setup_database():
    return init_db()

db_ok = setup_database()

# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>⚙️ BSS Explorer</h1>
    <p>Banque du Sous-Sol BRGM — Collecte hydrogéologique automatisée | FERRAPD</p>
</div>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/BRGM_logo.svg/200px-BRGM_logo.svg.png", width=120)
    st.markdown("### Navigation")
    page = st.radio(
        "Page",
        ["🔍 Nouvelle collecte", "📋 Historique", "ℹ️ À propos"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    if db_ok:
        sessions = list_sessions()
        st.metric("Sessions en base", len(sessions))
    else:
        st.warning("Base de données non connectée")
    st.markdown("---")
    st.caption("BSS Explorer v9 — FERRAPD\nDonnées : BRGM / Géorisques")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : NOUVELLE COLLECTE
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🔍 Nouvelle collecte":
    col_form, col_result = st.columns([1, 2], gap="large")

    with col_form:
        st.subheader("Paramètres de collecte")

        # Mode de saisie
        mode_saisie = st.radio(
            "Mode de saisie",
            ["Formulaire", "Fichier JSON"],
            horizontal=True,
        )

        sites_to_collect = []

        if mode_saisie == "Formulaire":
            with st.form("collect_form"):
                code_site = st.text_input("Code site BSS", value="FRA034001MPL", placeholder="FRA0XXXXXXXX")
                col_lat, col_lon = st.columns(2)
                with col_lat:
                    lat = st.number_input("Latitude (WGS84)", value=43.610769, format="%.6f", step=0.000001)
                with col_lon:
                    lon = st.number_input("Longitude (WGS84)", value=3.876716, format="%.6f", step=0.000001)
                emprise_m = st.slider("Emprise de recherche (m)", 100, 2000, 500, 50)
                submitted = st.form_submit_button("▶ Collecter les données", use_container_width=True, type="primary")

            if submitted:
                sites_to_collect = [{"code_site": code_site, "lat": lat, "lon": lon, "emprise_m": emprise_m}]

        else:  # Fichier JSON
            uploaded = st.file_uploader("Charger un fichier JSON", type=["json"])
            st.caption("""
**Format attendu :**
```json
// Site unique
{"code_site":"FRA034001MPL","lat":43.610769,"lon":3.876716}

// Liste de sites
[
  {"code_site":"FRA034001MPL","lat":43.610769,"lon":3.876716},
  {"lat":43.836699,"lon":4.360054,"emprise_m":800}
]
```
""")
            if uploaded:
                try:
                    raw = json.loads(uploaded.read().decode("utf-8"))
                    sites_to_collect = parse_batch_input(raw)
                    st.success(f"{len(sites_to_collect)} site(s) chargé(s)")
                    if st.button("▶ Lancer la collecte en lot", type="primary", use_container_width=True):
                        pass  # Déclenché ci-dessous
                except Exception as e:
                    st.error(f"Erreur de lecture JSON : {e}")

    # ─── Collecte ─────────────────────────────────────────────────────────────
    with col_result:
        if sites_to_collect:
            st.subheader(f"Collecte de {len(sites_to_collect)} site(s)")
            results = []

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, site in enumerate(sites_to_collect):
                status_text.info(f"⏳ Collecte en cours : {site.get('code_site', f'site {i+1}')} ({i+1}/{len(sites_to_collect)})")
                try:
                    result = collect_bss(
                        lat=float(site["lat"]),
                        lon=float(site["lon"]),
                        emprise_m=int(site.get("emprise_m", 500)),
                        code_site=site.get("code_site", ""),
                    )
                    result["input"] = site
                    result["success"] = True
                    results.append(result)

                    # Sauvegarder en base
                    if db_ok:
                        upsert_session(
                            code_site=result.get("code_site", site.get("code_site", "")),
                            lat=float(site["lat"]),
                            lon=float(site["lon"]),
                            emprise_m=int(site.get("emprise_m", 500)),
                            nb_ouvrages=result.get("nb_ouvrages", 0),
                            mode=result.get("mode", "WFS BRGM"),
                            ouvrages=result.get("ouvrages", []),
                            georisques=result.get("georisques"),
                            map_html=result.get("map_html"),
                        )
                except Exception as e:
                    results.append({"input": site, "success": False, "error": str(e), "nb_ouvrages": 0})

                progress_bar.progress((i + 1) / len(sites_to_collect))

            status_text.success(f"✅ Collecte terminée — {sum(1 for r in results if r.get('success'))} succès, {sum(1 for r in results if not r.get('success'))} échec(s)")

            # ─── Affichage des résultats ───────────────────────────────────────
            for result in results:
                site_label = result.get("code_site") or result["input"].get("code_site", "Site inconnu")
                if not result.get("success"):
                    st.error(f"❌ {site_label} — {result.get('error', 'Erreur inconnue')}")
                    continue

                with st.expander(f"✅ {site_label} — {result.get('nb_ouvrages', 0)} ouvrage(s)", expanded=len(results) == 1):
                    # Métriques
                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.metric("Ouvrages", result.get("nb_ouvrages", 0))
                    with m2:
                        geo = result.get("georisques", {}) or {}
                        st.metric("Zone sismique", geo.get("zone_sismique", "N/A"))
                    with m3:
                        st.metric("Aléa RGA", geo.get("alea_rga", "N/A"))
                    with m4:
                        closest = result.get("closest")
                        if closest:
                            st.metric("Ouvrage le + proche", f"{closest.get('distance_centre_m', 0):.0f} m")

                    # Tableau des ouvrages
                    ouvrages = result.get("ouvrages", [])
                    if ouvrages:
                        df = pd.DataFrame([{
                            "Code BSS": o.get("code_bss", ""),
                            "Nature": o.get("nature", ""),
                            "Commune": o.get("commune", ""),
                            "Prof. tot. (m)": o.get("profondeur_totale", ""),
                            "Prof. invest. (m)": o.get("prof_investigation", ""),
                            "Niv. eau (m)": o.get("niveau_eau", ""),
                            "Alt. NGF (m)": o.get("altitude_ngf", ""),
                            "Distance (m)": f"{o.get('distance_centre_m', 0):.0f}",
                        } for o in ouvrages])
                        st.dataframe(df, use_container_width=True, hide_index=True)

                    # Exports
                    col_dl1, col_dl2, col_dl3 = st.columns(3)
                    with col_dl1:
                        # Export CSV
                        csv_lines = ["Code BSS,Nature,Commune,Lat,Lon,Prof.tot(m),Prof.invest(m),Niv.eau(m),Niv.eau.date,Alt.NGF(m),Distance(m),URL InfoTerre,URL ADES"]
                        for o in ouvrages:
                            csv_lines.append(",".join([
                                o.get("code_bss", ""),
                                o.get("nature", ""),
                                o.get("commune", ""),
                                str(o.get("lat", "")),
                                str(o.get("lon", "")),
                                str(o.get("profondeur_totale", "")),
                                str(o.get("prof_investigation", "")),
                                str(o.get("niveau_eau", "")),
                                str(o.get("niveau_eau_date", "")),
                                str(o.get("altitude_ngf", "")),
                                f"{o.get('distance_centre_m', 0):.0f}",
                                o.get("url_infoterre", ""),
                                o.get("url_ades", ""),
                            ]))
                        csv_content = "\ufeff" + "\n".join(csv_lines)
                        st.download_button(
                            "📥 CSV ouvrages",
                            data=csv_content.encode("utf-8"),
                            file_name=f"bss_{site_label}_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
                    with col_dl2:
                        # Export JSON — nom incluant code site + date + heure
                        json_content = json.dumps(result, ensure_ascii=False, indent=2, default=str)
                        _ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
                        _site_clean = (site_label or 'site').replace('/', '-').replace(' ', '_')
                        st.download_button(
                            "📥 Exporter JSON",
                            data=json_content.encode("utf-8"),
                            file_name=f"BSS_{_site_clean}_{_ts}.json",
                            mime="application/json",
                            use_container_width=True,
                            help="Enregistrez ce fichier dans votre dossier OneDrive ou tout autre emplacement de votre choix",
                        )
                    with col_dl3:
                        # Export carte HTML
                        map_html = result.get("map_html", "")
                        if map_html:
                            st.download_button(
                                "🗺️ Carte HTML",
                                data=map_html.encode("utf-8"),
                                file_name=f"carte_bss_{site_label}_{datetime.now().strftime('%Y%m%d')}.html",
                                mime="text/html",
                                use_container_width=True,
                            )

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : HISTORIQUE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Historique":
    st.subheader("Historique des collectes")

    if not db_ok:
        st.warning("⚠️ Base de données non connectée. Configurez DATABASE_URL dans les Secrets Streamlit.")
        st.code("""
# Dans les Secrets Streamlit (Settings > Secrets) :
DATABASE_URL = "postgresql://user:password@host:5432/dbname?sslmode=require"
        """)
    else:
        sessions = list_sessions()
        if not sessions:
            st.info("Aucune session en base. Lancez une collecte pour commencer.")
        else:
            st.caption(f"{len(sessions)} session(s) en base — mise à jour automatique tous les lundis à 7h")

            for session in sessions:
                col_info, col_actions = st.columns([3, 1])
                with col_info:
                    updated = session.get("updated_at")
                    date_str = updated.strftime("%d/%m/%Y %H:%M") if updated else "N/A"
                    st.markdown(f"""
**{session['code_site']}** — {session['nb_ouvrages']} ouvrage(s)
`{session['lat']:.4f}, {session['lon']:.4f}` — Emprise : {session['emprise_m']} m — Mis à jour : {date_str}
""")
                with col_actions:
                    col_load, col_del = st.columns(2)
                    with col_load:
                        if st.button("📂 Charger", key=f"load_{session['id']}"):
                            st.session_state["loaded_session"] = get_session(session["id"])
                            st.session_state["show_loaded"] = True
                    with col_del:
                        if st.button("🗑️", key=f"del_{session['id']}", help="Supprimer cette session"):
                            if delete_session(session["id"]):
                                st.success("Session supprimée")
                                st.rerun()
                st.divider()

            # Affichage de la session chargée
            if st.session_state.get("show_loaded") and st.session_state.get("loaded_session"):
                loaded = st.session_state["loaded_session"]
                st.subheader(f"Session chargée : {loaded['code_site']}")
                ouvrages = loaded.get("ouvrages", [])
                if ouvrages:
                    df = pd.DataFrame([{
                        "Code BSS": o.get("code_bss", ""),
                        "Nature": o.get("nature", ""),
                        "Commune": o.get("commune", ""),
                        "Prof. tot. (m)": o.get("profondeur_totale", ""),
                        "Prof. invest. (m)": o.get("prof_investigation", ""),
                        "Niv. eau (m)": o.get("niveau_eau", ""),
                        "Alt. NGF (m)": o.get("altitude_ngf", ""),
                        "Distance (m)": f"{o.get('distance_centre_m', 0):.0f}",
                    } for o in ouvrages])
                    st.dataframe(df, use_container_width=True, hide_index=True)

                    # Exports
                    col_dl1, col_dl2, col_dl3 = st.columns(3)
                    with col_dl1:
                        csv_lines = ["Code BSS,Nature,Commune,Lat,Lon,Prof.tot(m),Prof.invest(m),Niv.eau(m),Niv.eau.date,Alt.NGF(m),Distance(m)"]
                        for o in ouvrages:
                            csv_lines.append(",".join([
                                o.get("code_bss", ""), o.get("nature", ""), o.get("commune", ""),
                                str(o.get("lat", "")), str(o.get("lon", "")),
                                str(o.get("profondeur_totale", "")), str(o.get("prof_investigation", "")),
                                str(o.get("niveau_eau", "")), str(o.get("niveau_eau_date", "")),
                                str(o.get("altitude_ngf", "")), f"{o.get('distance_centre_m', 0):.0f}",
                            ]))
                        _ts2 = datetime.now().strftime('%Y-%m-%d_%Hh%M')
                        _site_clean2 = (loaded.get('code_site', 'site') or 'site').replace('/', '-').replace(' ', '_')
                        st.download_button(
                            "📥 CSV",
                            data=("\ufeff" + "\n".join(csv_lines)).encode("utf-8"),
                            file_name=f"BSS_{_site_clean2}_{_ts2}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
                    with col_dl2:
                        st.download_button(
                            "📥 Exporter JSON",
                            data=json.dumps(loaded, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                            file_name=f"BSS_{_site_clean2}_{_ts2}.json",
                            mime="application/json",
                            use_container_width=True,
                            help="Enregistrez ce fichier dans votre dossier OneDrive ou tout autre emplacement de votre choix",
                        )
                    with col_dl3:
                        if loaded.get("map_html"):
                            st.download_button("🗺️ Carte HTML", data=loaded["map_html"].encode("utf-8"),
                                               file_name=f"carte_{loaded['code_site']}.html", mime="text/html", use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : À PROPOS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "ℹ️ À propos":
    st.subheader("À propos de BSS Explorer")
    st.markdown("""
**BSS Explorer** est un outil de collecte hydrogéologique automatisée développé par **FERRAPD**.

### Sources de données
| Source | Description |
|--------|-------------|
| [BRGM WFS](https://geoservices.brgm.fr/geologie) | Ouvrages BSS (forages, piézomètres, puits) |
| [InfoTerre BRGM](http://ficheinfoterre.brgm.fr) | Fiches détaillées (altitude NGF, log géologique, profondeur, niveau d'eau) |
| [Géorisques](https://www.georisques.gouv.fr) | Zone sismique, aléa retrait-gonflement argiles |
| [ADES](https://ades.eaufrance.fr) | Données piézométriques nationales |

### Données collectées par ouvrage
- Code BSS, nature, commune, coordonnées WGS84
- Profondeur totale, profondeur d'investigation
- Niveau d'eau mesuré (avec date)
- Altitude NGF (IGN)
- Log géologique (couches lithologiques)
- Documents numérisés (scans BRGM)

### API REST
Une API FastAPI est disponible pour l'intégration dans des chaînes de traitement automatisées.
Voir `API_ORCHESTRATEUR.md` pour la documentation complète.

### Version
BSS Explorer v9 — Build {datetime.now().strftime('%Y-%m-%d')}
""".format(datetime=datetime))
