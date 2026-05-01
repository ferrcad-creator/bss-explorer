"""
BSS Explorer — Application Streamlit v10
=========================================
Collecte hydrogéologique automatisée depuis la Banque du Sous-Sol BRGM.
Fonctionnalités :
  - Carte interactive Leaflet/Folium (4 fonds, emprise, marqueurs enrichis)
  - Log géologique avec légende lithologique colorée
  - Documents numérisés InfoTerre (liens directs + aperçu)
  - Données hydrogéologiques ADES
  - Export JSON (BSS_{code_site}_{YYYY-MM-DD_HHhMM}.json)
"""

import json
import sys
import os
from datetime import datetime
from typing import Optional

import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

# Ajouter le répertoire courant au path pour les imports relatifs
sys.path.insert(0, os.path.dirname(__file__))

from utils.bss_collector import collect_bss, parse_batch_input
from utils.db import init_db, upsert_session, list_sessions, get_session, delete_session

# ─── Palette lithologique (identique à la version Manus) ─────────────────────
LITHO_COLORS = {
    "remblai":   "#8B4513",
    "gravier":   "#A9A9A9",
    "alluvion":  "#3CB371",
    "calcaire":  "#90EE90",
    "argile":    "#4682B4",
    "limon":     "#9370DB",
    "marne":     "#5B9BD5",
    "gres":      "#D2691E",
    "sable":     "#FFD700",
    "craie":     "#F5F5DC",
    "granite":   "#BC8F8F",
    "basalte":   "#696969",
    "schiste":   "#8FBC8F",
    "autre":     "#BDBDBD",
}

def get_litho_color(lithologie: str) -> str:
    """Retourne la couleur associée à une lithologie."""
    import unicodedata
    l = unicodedata.normalize("NFD", lithologie.lower())
    l = "".join(c for c in l if unicodedata.category(c) != "Mn")
    dominant = l.split("(")[0].strip().split()[0] if l.split("(")[0].strip() else ""
    priority = list(LITHO_COLORS.keys())[:-1]  # tout sauf "autre"
    for key in priority:
        if key in dominant:
            return LITHO_COLORS[key]
    for key in priority:
        if key in l:
            return LITHO_COLORS[key]
    return LITHO_COLORS["autre"]


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
    .log-bar {
        display: inline-block;
        height: 18px;
        border-radius: 3px;
        margin-right: 4px;
        vertical-align: middle;
    }
    .badge-ok { background: #1b5e20; color: #a5d6a7; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
    .badge-warn { background: #e65100; color: #ffcc80; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
    .stButton > button { border-radius: 8px; }
    div[data-testid="stSidebar"] { background: #0a1628; }
    .doc-link {
        display: inline-block;
        margin: 3px;
        padding: 5px 10px;
        background: #1e3a5f;
        color: #93c5fd;
        border: 1px solid #1e40af;
        border-radius: 5px;
        font-size: 12px;
        text-decoration: none;
    }
    .doc-link:hover { background: #1e40af; }
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
    st.caption("BSS Explorer v10 — FERRAPD\nDonnées : BRGM / Géorisques / ADES")


# ─── Fonctions d'affichage ────────────────────────────────────────────────────

def build_folium_map(ouvrages: list, lat_centre: float, lon_centre: float,
                     emprise_m: int, code_site: str, georisques: Optional[dict]) -> folium.Map:
    """Construit une carte Folium enrichie avec marqueurs, emprise et légende."""
    import math

    m = folium.Map(
        location=[lat_centre, lon_centre],
        zoom_start=16,
        tiles=None,
    )

    # Fonds de carte
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite Esri",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr="CARTO",
        name="CARTO Dark",
    ).add_to(m)

    # Emprise (carré Lambert 93 → WGS84 approximé)
    half = emprise_m / 2
    # Approximation : 1 degré lat ≈ 111 000 m, 1 degré lon ≈ 111 000 * cos(lat) m
    dlat = half / 111000
    dlon = half / (111000 * math.cos(math.radians(lat_centre)))
    emprise_coords = [
        [lat_centre - dlat, lon_centre - dlon],
        [lat_centre - dlat, lon_centre + dlon],
        [lat_centre + dlat, lon_centre + dlon],
        [lat_centre + dlat, lon_centre - dlon],
    ]
    folium.Polygon(
        locations=emprise_coords,
        color="#ef4444",
        weight=2,
        dash_array="6 4",
        fill=True,
        fill_opacity=0.05,
        tooltip=f"Emprise {emprise_m}m × {emprise_m}m",
    ).add_to(m)

    # Marqueur centre
    folium.Marker(
        location=[lat_centre, lon_centre],
        tooltip=f"<b>{code_site}</b><br>Point de référence",
        icon=folium.DivIcon(
            html='<div style="font-size:20px;line-height:1;">⭐</div>',
            icon_anchor=(10, 10),
        ),
    ).add_to(m)

    # Marqueurs ouvrages
    for o in ouvrages:
        # Construire le popup HTML enrichi
        log_rows = ""
        for c in o.get("log_geologique", []):
            color = get_litho_color(c.get("lithologie", ""))
            log_rows += (
                f'<tr>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;">'
                f'{c.get("prof_de", "")}&ndash;{c.get("prof_a", "")} m</td>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;">'
                f'<span style="display:inline-block;width:10px;height:10px;background:{color};'
                f'border-radius:2px;margin-right:4px;vertical-align:middle;"></span>'
                f'{c.get("lithologie", "")}</td>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;color:#666;">'
                f'{c.get("stratigraphie", "")}</td>'
                f'</tr>'
            )

        docs_html = ""
        for d in o.get("documents", []):
            docs_html += (
                f'<a href="{d.get("url", "#")}" target="_blank" '
                f'style="display:inline-block;margin:3px;padding:4px 8px;background:#1e3a5f;'
                f'color:#93c5fd;border:1px solid #1e40af;border-radius:4px;font-size:11px;'
                f'text-decoration:none;">📄 {d.get("nom", "Document")}</a>'
            )

        geo = georisques or {}
        geo_html = ""
        if geo.get("zone_sismique"):
            geo_html += f'<tr><td style="color:#666;font-size:11px;">Zone sismique</td><td style="font-size:11px;"><b>{geo["zone_sismique"]}</b></td></tr>'
        if geo.get("alea_rga"):
            geo_html += f'<tr><td style="color:#666;font-size:11px;">Aléa RGA</td><td style="font-size:11px;"><b>{geo["alea_rga"]}</b></td></tr>'

        prof_tot = o.get("profondeur_totale")
        prof_inv = o.get("prof_investigation")
        niv_eau = o.get("niveau_eau")
        niv_date = o.get("niveau_eau_date", "")
        alt_ngf = o.get("altitude_ngf")
        alt_prec = o.get("altitude_precision", "")

        popup_html = f"""
<div style="font-family:Inter,sans-serif;min-width:320px;max-width:460px;">
  <div style="background:linear-gradient(135deg,#0f2d4a,#1e3a5f);color:#fff;padding:10px 14px;border-radius:6px 6px 0 0;">
    <div style="font-size:14px;font-weight:700;">⚡ {o.get("code_bss","")}</div>
    <div style="font-size:11px;opacity:0.8;">{o.get("nature","")} — {o.get("nom_commune","")}</div>
  </div>
  <div style="padding:12px;background:#fff;">
    <table style="width:100%;border-collapse:collapse;">
      <tr><td style="color:#555;font-size:12px;width:140px;">Profondeur totale</td>
          <td style="font-size:12px;"><b>{f"{prof_tot} m" if prof_tot is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Prof. investigation</td>
          <td style="font-size:12px;"><b>{f"{prof_inv} m" if prof_inv is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Niveau d'eau</td>
          <td style="font-size:12px;"><b>{f"{niv_eau} m" if niv_eau is not None else "N/D"}{f" ({niv_date})" if niv_date else ""}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Altitude NGF</td>
          <td style="font-size:12px;"><b>{f"{alt_ngf} m {alt_prec}" if alt_ngf is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Distance centre</td>
          <td style="font-size:12px;"><b>{o.get("distance_centre_m", 0):.0f} m</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Aquifère</td>
          <td style="font-size:12px;">{o.get("aquifere","N/D")}</td></tr>
      <tr><td style="color:#555;font-size:12px;">Bassin DCE</td>
          <td style="font-size:12px;">{o.get("bassin_dce","N/D")}</td></tr>
      {geo_html}
    </table>
    {"<div style='margin-top:10px;border-top:1px solid #eee;padding-top:8px;'><div style='font-size:12px;font-weight:600;color:#1e3a5f;margin-bottom:6px;'>📋 Coupe géologique</div><table style='border-collapse:collapse;width:100%;'><thead><tr style='background:#f0f4f8;'><th style='padding:3px 6px;font-size:11px;text-align:left;'>Prof.</th><th style='padding:3px 6px;font-size:11px;text-align:left;'>Lithologie</th><th style='padding:3px 6px;font-size:11px;text-align:left;'>Stratigraphie</th></tr></thead><tbody>" + log_rows + "</tbody></table></div>" if log_rows else ""}
    {"<div style='margin-top:10px;border-top:1px solid #eee;padding-top:8px;'><div style='font-size:12px;font-weight:600;color:#1e3a5f;margin-bottom:4px;'>📄 Documents numérisés</div>" + docs_html + "</div>" if docs_html else ""}
    <div style="margin-top:10px;border-top:1px solid #eee;padding-top:8px;display:flex;gap:8px;">
      <a href="{o.get("url_infoterre","#")}" target="_blank"
         style="flex:1;text-align:center;padding:6px;background:#0f4c81;color:#fff;border-radius:5px;font-size:11px;text-decoration:none;">🔗 InfoTerre</a>
      <a href="{o.get("url_ades","#")}" target="_blank"
         style="flex:1;text-align:center;padding:6px;background:#065f46;color:#fff;border-radius:5px;font-size:11px;text-decoration:none;">💧 ADES</a>
    </div>
  </div>
</div>"""

        folium.CircleMarker(
            location=[o.get("lat", 0), o.get("lon", 0)],
            radius=10,
            color="#1e40af",
            weight=2,
            fill=True,
            fill_color="#3b82f6",
            fill_opacity=0.85,
            tooltip=f"<b>{o.get('code_bss','')}</b><br>{o.get('nature','')} — {o.get('nom_commune','')}<br><small>Cliquer pour la fiche</small>",
            popup=folium.Popup(popup_html, max_width=480),
        ).add_to(m)

    # Légende lithologique
    legend_items = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">'
        f'<div style="width:14px;height:14px;background:{color};border-radius:3px;flex-shrink:0;"></div>'
        f'<span style="font-size:11px;">{key.capitalize()}</span></div>'
        for key, color in LITHO_COLORS.items() if key != "autre"
    )
    geo_legend = ""
    if georisques:
        geo_legend = (
            f'<hr style="border-color:#334155;margin:6px 0;">'
            f'<div style="font-size:12px;color:#1e3a5f;font-weight:600;margin-bottom:4px;">⚡ Géorisques</div>'
            f'<div style="font-size:11px;">Sismique : <b>{georisques.get("zone_sismique","N/D")}</b></div>'
            f'<div style="font-size:11px;">RGA : <b>{georisques.get("alea_rga","N/D")}</b></div>'
        )
    legend_html = f"""
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:rgba(255,255,255,0.95);
     padding:12px 16px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.15);
     font-family:Inter,sans-serif;max-height:400px;overflow-y:auto;">
  <div style="font-size:13px;font-weight:700;color:#1e3a5f;margin-bottom:8px;">⚡ Légende lithologique</div>
  {legend_items}
  <div style="display:flex;align-items:center;gap:6px;margin:2px 0;">
    <div style="width:14px;height:14px;background:#BDBDBD;border-radius:3px;flex-shrink:0;"></div>
    <span style="font-size:11px;">Autre</span>
  </div>
  <hr style="border-color:#e2e8f0;margin:6px 0;">
  <div style="font-size:11px;display:flex;align-items:center;gap:6px;">
    <span style="color:#ef4444;font-weight:bold;">- - -</span> Emprise {emprise_m}m
  </div>
  <div style="font-size:11px;">⭐ Point de référence</div>
  <div style="font-size:11px;display:flex;align-items:center;gap:6px;">
    <span style="color:#3b82f6;font-size:16px;">●</span> Ouvrage BSS
  </div>
  {geo_legend}
</div>"""
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)
    return m


def render_log_geologique(ouvrages: list):
    """Affiche les logs géologiques de chaque ouvrage avec légende colorée."""
    ouvrages_avec_log = [o for o in ouvrages if o.get("log_geologique")]
    if not ouvrages_avec_log:
        st.info("Aucun log géologique disponible pour les ouvrages de ce site.")
        return

    for o in ouvrages_avec_log:
        log = o.get("log_geologique", [])
        code = o.get("code_bss", "")
        nature = o.get("nature", "")
        commune = o.get("nom_commune", o.get("commune", ""))

        with st.expander(f"📋 {code} — {nature} ({commune}) — {len(log)} couche(s)", expanded=False):
            # Barre visuelle SVG du log
            if log:
                max_prof = max((c.get("prof_a", 0) for c in log), default=20) or 20
                bar_width = 40
                bar_height = 200
                scale = bar_height / max_prof

                svg_rects = ""
                for c in log:
                    y = c.get("prof_de", 0) * scale
                    h = max(2, (c.get("prof_a", 0) - c.get("prof_de", 0)) * scale)
                    color = get_litho_color(c.get("lithologie", ""))
                    svg_rects += (
                        f'<rect x="0" y="{y:.1f}" width="{bar_width}" height="{h:.1f}" '
                        f'fill="{color}" stroke="#fff" stroke-width="0.5"/>'
                    )

                col_svg, col_table = st.columns([1, 5])
                with col_svg:
                    st.markdown(
                        f'<svg width="{bar_width}" height="{bar_height}" '
                        f'style="border:1px solid #334155;border-radius:3px;">'
                        f'{svg_rects}</svg>',
                        unsafe_allow_html=True,
                    )
                with col_table:
                    df_log = pd.DataFrame([{
                        "Profondeur": f"{c.get('prof_de','')} – {c.get('prof_a','')} m",
                        "Lithologie": c.get("lithologie", ""),
                        "Stratigraphie": c.get("stratigraphie", ""),
                    } for c in log])
                    st.dataframe(df_log, use_container_width=True, hide_index=True)

            # Liens InfoTerre et ADES
            col_a, col_b = st.columns(2)
            with col_a:
                if o.get("url_infoterre"):
                    st.link_button("🔗 Fiche InfoTerre complète", o["url_infoterre"])
            with col_b:
                if o.get("url_ades"):
                    st.link_button("💧 Données ADES", o["url_ades"])


def render_documents(ouvrages: list):
    """Affiche les documents numérisés InfoTerre pour chaque ouvrage."""
    ouvrages_avec_docs = [o for o in ouvrages if o.get("documents")]
    if not ouvrages_avec_docs:
        st.info("Aucun document numérisé disponible pour les ouvrages de ce site.")
        return

    for o in ouvrages_avec_docs:
        docs = o.get("documents", [])
        code = o.get("code_bss", "")
        commune = o.get("nom_commune", o.get("commune", ""))

        with st.expander(f"📄 {code} — {commune} — {len(docs)} document(s)", expanded=False):
            for d in docs:
                col_type, col_link = st.columns([1, 3])
                with col_type:
                    st.caption(d.get("type", "Document"))
                with col_link:
                    st.markdown(
                        f'<a href="{d.get("url","#")}" target="_blank" class="doc-link">'
                        f'📄 {d.get("nom","Document")}</a>',
                        unsafe_allow_html=True,
                    )


def render_result_tabs(result: dict, site_label: str):
    """Affiche les résultats d'une collecte dans des onglets enrichis."""
    ouvrages = result.get("ouvrages", [])
    geo = result.get("georisques", {}) or {}
    closest = result.get("closest") or (ouvrages[0] if ouvrages else None)
    lat_c = result.get("input", {}).get("lat", 0)
    lon_c = result.get("input", {}).get("lon", 0)
    emprise = result.get("input", {}).get("emprise_m", 500)

    # ── Métriques ──────────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("Ouvrages", result.get("nb_ouvrages", 0))
    with m2:
        st.metric("Zone sismique", geo.get("zone_sismique", "N/A"))
    with m3:
        st.metric("Aléa RGA", geo.get("alea_rga", "N/A"))
    with m4:
        if closest:
            st.metric("Ouvrage le + proche", f"{closest.get('distance_centre_m', 0):.0f} m")
    with m5:
        nb_logs = sum(1 for o in ouvrages if o.get("log_geologique"))
        st.metric("Avec log géol.", nb_logs)

    # ── Ouvrage le plus proche ─────────────────────────────────────────────────
    if closest:
        with st.expander("🎯 Ouvrage le plus proche — détail", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(f"**Code BSS :** {closest.get('code_bss','')}")
                st.markdown(f"**Nature :** {closest.get('nature','')}")
                st.markdown(f"**Commune :** {closest.get('nom_commune', closest.get('commune',''))}")
                st.markdown(f"**Distance :** {closest.get('distance_centre_m', 0):.0f} m")
            with c2:
                prof_t = closest.get("profondeur_totale")
                prof_i = closest.get("prof_investigation")
                niv = closest.get("niveau_eau")
                niv_d = closest.get("niveau_eau_date", "")
                alt = closest.get("altitude_ngf")
                st.markdown(f"**Prof. totale :** {f'{prof_t} m' if prof_t else 'N/D'}")
                st.markdown(f"**Prof. investigation :** {f'{prof_i} m' if prof_i else 'N/D'}")
                st.markdown(f"**Niveau d'eau :** {f'{niv} m' if niv else 'N/D'}{f' ({niv_d})' if niv_d else ''}")
                st.markdown(f"**Altitude NGF :** {f'{alt} m' if alt else 'N/D'}")
            with c3:
                st.markdown(f"**Aquifère :** {closest.get('aquifere','N/D')}")
                st.markdown(f"**Bassin DCE :** {closest.get('bassin_dce','N/D')}")
                if closest.get("url_infoterre"):
                    st.link_button("🔗 InfoTerre", closest["url_infoterre"])
                if closest.get("url_ades"):
                    st.link_button("💧 ADES", closest["url_ades"])

    # ── Onglets ────────────────────────────────────────────────────────────────
    tab_carte, tab_tableau, tab_logs, tab_docs = st.tabs([
        "🗺️ Carte interactive",
        "📊 Tableau des ouvrages",
        "📋 Logs géologiques",
        "📄 Documents InfoTerre",
    ])

    with tab_carte:
        if lat_c and lon_c:
            with st.spinner("Génération de la carte…"):
                try:
                    fmap = build_folium_map(ouvrages, lat_c, lon_c, emprise, site_label, geo or None)
                    st_folium(fmap, width="100%", height=550, returned_objects=[])
                except Exception as e:
                    st.error(f"Erreur de génération de la carte : {e}")
        else:
            st.warning("Coordonnées du centre non disponibles pour afficher la carte.")

    with tab_tableau:
        if ouvrages:
            df = pd.DataFrame([{
                "Code BSS": o.get("code_bss", ""),
                "Nature": o.get("nature", ""),
                "Commune": o.get("nom_commune", o.get("commune", "")),
                "Prof. tot. (m)": o.get("profondeur_totale", ""),
                "Prof. invest. (m)": o.get("prof_investigation", ""),
                "Niv. eau (m)": o.get("niveau_eau", ""),
                "Niv. eau date": o.get("niveau_eau_date", ""),
                "Alt. NGF (m)": o.get("altitude_ngf", ""),
                "Aquifère": o.get("aquifere", ""),
                "Bassin DCE": o.get("bassin_dce", ""),
                "Distance (m)": f"{o.get('distance_centre_m', 0):.0f}",
                "Log géol.": "✅" if o.get("log_geologique") else "—",
                "Docs": len(o.get("documents", [])),
            } for o in ouvrages])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Liens InfoTerre / ADES par ouvrage
            st.markdown("**Liens directs :**")
            for o in ouvrages:
                cols = st.columns([2, 1, 1])
                with cols[0]:
                    st.caption(o.get("code_bss", ""))
                with cols[1]:
                    if o.get("url_infoterre"):
                        st.markdown(
                            f'<a href="{o["url_infoterre"]}" target="_blank" class="doc-link">🔗 InfoTerre</a>',
                            unsafe_allow_html=True,
                        )
                with cols[2]:
                    if o.get("url_ades"):
                        st.markdown(
                            f'<a href="{o["url_ades"]}" target="_blank" class="doc-link">💧 ADES</a>',
                            unsafe_allow_html=True,
                        )

    with tab_logs:
        render_log_geologique(ouvrages)

    with tab_docs:
        render_documents(ouvrages)

    # ── Exports ────────────────────────────────────────────────────────────────
    st.divider()
    col_dl1, col_dl2, col_dl3 = st.columns(3)
    _ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    _site_clean = (site_label or 'site').replace('/', '-').replace(' ', '_')

    with col_dl1:
        csv_lines = ["Code BSS,Nature,Commune,Lat,Lon,Prof.tot(m),Prof.invest(m),Niv.eau(m),Niv.eau.date,Alt.NGF(m),Aquifere,Bassin.DCE,Distance(m),Nb.couches.log,Nb.docs,URL InfoTerre,URL ADES"]
        for o in ouvrages:
            csv_lines.append(",".join([
                f'"{o.get("code_bss","")}"',
                f'"{o.get("nature","")}"',
                f'"{o.get("nom_commune", o.get("commune",""))}"',
                str(o.get("lat", "")),
                str(o.get("lon", "")),
                str(o.get("profondeur_totale", "")),
                str(o.get("prof_investigation", "")),
                str(o.get("niveau_eau", "")),
                f'"{o.get("niveau_eau_date","")}"',
                str(o.get("altitude_ngf", "")),
                f'"{o.get("aquifere","")}"',
                f'"{o.get("bassin_dce","")}"',
                f'{o.get("distance_centre_m", 0):.0f}',
                str(len(o.get("log_geologique", []))),
                str(len(o.get("documents", []))),
                f'"{o.get("url_infoterre","")}"',
                f'"{o.get("url_ades","")}"',
            ]))
        st.download_button(
            "📥 CSV ouvrages",
            data=("\ufeff" + "\n".join(csv_lines)).encode("utf-8"),
            file_name=f"BSS_{_site_clean}_{_ts}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_dl2:
        st.download_button(
            "📥 Exporter JSON",
            data=json.dumps(result, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            file_name=f"BSS_{_site_clean}_{_ts}.json",
            mime="application/json",
            use_container_width=True,
            help="Enregistrez ce fichier dans votre dossier OneDrive ou tout autre emplacement de votre choix",
        )

    with col_dl3:
        # Générer la carte HTML standalone pour téléchargement
        if lat_c and lon_c:
            try:
                fmap_dl = build_folium_map(ouvrages, lat_c, lon_c, emprise, site_label, geo or None)
                map_html_str = fmap_dl._repr_html_()
                st.download_button(
                    "🗺️ Carte HTML",
                    data=map_html_str.encode("utf-8"),
                    file_name=f"carte_BSS_{_site_clean}_{_ts}.html",
                    mime="text/html",
                    use_container_width=True,
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : NOUVELLE COLLECTE
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🔍 Nouvelle collecte":
    col_form, col_result = st.columns([1, 2], gap="large")

    with col_form:
        st.subheader("Paramètres de collecte")

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
                    # parse_batch_input attend du texte ou un objet JSON
                    if isinstance(raw, list):
                        sites_to_collect = raw  # Tableau de sites directement
                    elif isinstance(raw, dict):
                        sites_to_collect = [raw]  # Site unique
                    else:
                        sites_to_collect = parse_batch_input(str(raw))
                    st.success(f"{len(sites_to_collect)} site(s) chargé(s)")
                    if st.button("▶ Lancer la collecte en lot", type="primary", use_container_width=True):
                        pass
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
                            map_html=None,
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
                    render_result_tabs(result, site_label)


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

            if st.session_state.get("show_loaded") and st.session_state.get("loaded_session"):
                loaded = st.session_state["loaded_session"]
                st.subheader(f"Session chargée : {loaded['code_site']}")

                # Reconstituer un objet result compatible avec render_result_tabs
                loaded_result = {
                    "code_site": loaded.get("code_site", ""),
                    "nb_ouvrages": loaded.get("nb_ouvrages", 0),
                    "ouvrages": loaded.get("ouvrages", []),
                    "georisques": loaded.get("georisques"),
                    "closest": loaded.get("ouvrages", [None])[0] if loaded.get("ouvrages") else None,
                    "input": {
                        "lat": loaded.get("lat", 0),
                        "lon": loaded.get("lon", 0),
                        "emprise_m": loaded.get("emprise_m", 500),
                        "code_site": loaded.get("code_site", ""),
                    },
                    "success": True,
                }
                render_result_tabs(loaded_result, loaded.get("code_site", "session"))


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
| [InfoTerre BRGM](http://ficheinfoterre.brgm.fr) | Fiches détaillées (altitude NGF, log géologique, profondeur, niveau d'eau, documents numérisés) |
| [Géorisques](https://www.georisques.gouv.fr) | Zone sismique, aléa retrait-gonflement argiles |
| [ADES](https://ades.eaufrance.fr) | Données piézométriques nationales |

### Données collectées par ouvrage
- Code BSS, nature, commune, coordonnées WGS84
- Profondeur totale, profondeur d'investigation
- Niveau d'eau mesuré (avec date)
- Altitude NGF (IGN)
- Aquifère et bassin DCE
- **Log géologique** (couches lithologiques avec légende colorée)
- **Documents numérisés** (scans BRGM, coupes géologiques)
- Liens directs InfoTerre et ADES

### Fonctionnalités
- 🗺️ **Carte interactive** Leaflet (OSM, Satellite, CARTO) avec emprise, marqueurs cliquables et popups enrichis
- 📋 **Logs géologiques** avec représentation graphique SVG et légende lithologique (13 faciès)
- 📄 **Documents InfoTerre** : liens directs vers les scans numérisés
- 💧 **Liens ADES** pour les données piézométriques
- 📥 **Export JSON** avec nom `BSS_{code_site}_{YYYY-MM-DD_HHhMM}.json`
- 📥 **Export CSV** enrichi (aquifère, bassin DCE, nb couches log, nb documents)

### Version
BSS Explorer v10 — Build {}
""".format(datetime.now().strftime('%Y-%m-%d')))
