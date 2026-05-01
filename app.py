"""
BSS Explorer — Application Streamlit v12
=========================================
Collecte hydrogéologique automatisée depuis la Banque du Sous-Sol BRGM.

Paramètres d'entrée JSON :
  CS      = Code site BSS (ex: FRA034001MPL)
  LaOPY   = Latitude WGS84 (°)
  LoOPY   = Longitude WGS84 (°)
  emprise_m = Emprise de recherche (m) — optionnel, défaut 500 (formulaire) / 2000 (batch)

Paramètres de sortie JSON :
  NOuv        = Nombre d'ouvrages (u)
  NOuvALog    = Nombre d'ouvrages avec log géologique (u)
  IZS         = Zone sismique
  IARGA       = Aléa RGA
  ouvrages[]:
    COM         = Commune
    BDCE        = Bassin DCE
    DOuvPC      = Distance ouvrage/point ciblé (m)
    NInv        = Nature de l'investigation
    PIOuv       = Profondeur d'investigation (m)
    PeS         = Niveau d'eau (m)
    AltOuv      = Altitude NGF (mNGF)
    NDAOuv      = Nombre de documents associés (u)
    NCALOuv     = Nombre de couches dans le log (u)
    PC1..PCX    = Épaisseur de chaque couche (m) — seulement si log disponible
    TeC1..TeCX  = Texture (lithologie) de chaque couche
    StC1..StCX  = Stratigraphie de chaque couche
    FAOuv_{code_bss} = Dossier des documents de l'ouvrage
"""

import json
import sys
import os
import re
import io
import zipfile
from datetime import datetime
from typing import Optional

import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

sys.path.insert(0, os.path.dirname(__file__))

from utils.bss_collector import collect_bss, parse_batch_input
from utils.db import init_db, upsert_session, list_sessions, get_session, delete_session

# ─── Palette lithologique ─────────────────────────────────────────────────────
LITHO_COLORS = {
    "remblai":  "#8B4513",
    "gravier":  "#A9A9A9",
    "alluvion": "#3CB371",
    "calcaire": "#90EE90",
    "argile":   "#4682B4",
    "limon":    "#9370DB",
    "marne":    "#5B9BD5",
    "gres":     "#D2691E",
    "sable":    "#FFD700",
    "craie":    "#F5F5DC",
    "granite":  "#BC8F8F",
    "basalte":  "#696969",
    "schiste":  "#8FBC8F",
    "autre":    "#BDBDBD",
}

def get_litho_color(lithologie: str) -> str:
    import unicodedata
    l = unicodedata.normalize("NFD", lithologie.lower())
    l = "".join(c for c in l if unicodedata.category(c) != "Mn")
    dominant = l.split("(")[0].strip().split()[0] if l.split("(")[0].strip() else ""
    priority = [k for k in LITHO_COLORS if k != "autre"]
    for key in priority:
        if key in dominant:
            return LITHO_COLORS[key]
    for key in priority:
        if key in l:
            return LITHO_COLORS[key]
    return LITHO_COLORS["autre"]


# ─── Validation code site BSS ─────────────────────────────────────────────────
CS_PATTERN = re.compile(r"^FRA0[0-9]{2}0[0-9]{4}$|^FRA0[0-9]{2}[0-9]{5}$", re.IGNORECASE)

def validate_cs(cs: str) -> bool:
    """Valide le format du code site BSS : FRA0XX00XXX ou FRA0XX0XXXX."""
    return bool(CS_PATTERN.match(cs.strip())) if cs else False


# ─── Transformation JSON sortie selon la nomenclature v11 ─────────────────────
def build_output_json(result: dict, site_input: dict) -> dict:
    """Construit le JSON de sortie selon la nomenclature FERRAPD v11."""
    ouvrages_raw = result.get("ouvrages", [])
    geo = result.get("georisques", {}) or {}

    ouvrages_out = []
    for o in ouvrages_raw:
        log = o.get("log_geologique", [])
        docs = o.get("documents", [])
        code_bss = o.get("code_bss", "")

        ouvrage_dict = {
            "code_bss": code_bss,
            "COM":      o.get("nom_commune", o.get("commune", "")),
            "BDCE":     o.get("bassin_dce", ""),
            "DOuvPC":   round(o.get("distance_centre_m", 0), 1),
            "NInv":     o.get("nature", ""),
            "PIOuv":    o.get("prof_investigation"),
            "PeS":      o.get("niveau_eau"),
            "AltOuv":   o.get("altitude_ngf"),
            "NDAOuv":   len(docs),
            "NCALOuv":  len(log),
            "aquifere": o.get("aquifere", ""),
            "lat":      o.get("lat"),
            "lon":      o.get("lon"),
            "url_infoterre": o.get("url_infoterre", ""),
            "url_ades":      o.get("url_ades", ""),
        }

        # PC1..PCX, TeC1..TeCX, StC1..StCX — seulement si log disponible
        if log:
            for idx, couche in enumerate(log, start=1):
                prof_de = couche.get("prof_de", 0) or 0
                prof_a  = couche.get("prof_a", 0) or 0
                ouvrage_dict[f"PC{idx}"]  = round(prof_a - prof_de, 2)
                ouvrage_dict[f"TeC{idx}"] = couche.get("lithologie", "")
                ouvrage_dict[f"StC{idx}"] = couche.get("stratigraphie", "")

        # FAOuv_{code_bss} — dossier des documents
        safe_code = code_bss.replace("/", "_").replace(" ", "_")
        ouvrage_dict[f"FAOuv_{safe_code}"] = [
            {"nom": d.get("nom", ""), "type": d.get("type", ""), "url": d.get("url", "")}
            for d in docs
        ]

        ouvrages_out.append(ouvrage_dict)

    output = {
        "CS":        site_input.get("CS", site_input.get("code_site", "")),
        "LaOPY":     site_input.get("LaOPY", site_input.get("lat", "")),
        "LoOPY":     site_input.get("LoOPY", site_input.get("lon", "")),
        "emprise_m": site_input.get("emprise_m", 500),
        "mode":      result.get("mode", "WFS BRGM"),
        "NOuv":      result.get("nb_ouvrages", 0),
        "NOuvALog":  sum(1 for o in ouvrages_raw if o.get("log_geologique")),
        "IZS":       geo.get("zone_sismique", ""),
        "IARGA":     geo.get("alea_rga", ""),
        "ouvrages":  ouvrages_out,
    }
    return output


# ─── Configuration de la page ─────────────────────────────────────────────────
APP_ICON = "🔩"   # Icône emoji de secours
FERRAPD_LOGO_URL  = "https://raw.githubusercontent.com/ferrcad-creator/bss-explorer/main/assets/ferrapd_logo.png"
FERRAPD_ICONE_URL = "https://raw.githubusercontent.com/ferrcad-creator/bss-explorer/main/assets/ferrapd_icone.png"

st.set_page_config(
    page_title="BSS Explorer — FERRAPD",
    page_icon=FERRAPD_ICONE_URL,
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
    .main-header p  { color: #90a4ae; margin: 0.3rem 0 0 0; font-size: 0.9rem; }
    .stButton > button { border-radius: 8px; }
    div[data-testid="stSidebar"] { background: #0a1628; }
    /* IARGA : réduire la valeur de la métrique pour éviter la troncature */
    [data-testid="stMetric"][aria-label="IARGA"] [data-testid="stMetricValue"] > div,
    [data-testid="stMetricValue"] p { font-size: 1rem !important; white-space: normal !important; word-break: break-word !important; }
    /* Popup modal documents */
    .doc-modal-overlay {
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(0,0,0,0.85); z-index: 9999;
        display: flex; flex-direction: column; align-items: center;
        justify-content: flex-start; padding: 20px; box-sizing: border-box;
    }
    .doc-modal-bar {
        width: 100%; max-width: 1000px;
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 12px;
    }
    .doc-link-btn {
        display: inline-block; margin: 3px; padding: 5px 10px;
        background: #1e3a5f; color: #93c5fd;
        border: 1px solid #1e40af; border-radius: 5px;
        font-size: 12px; text-decoration: none; cursor: pointer;
    }
</style>
""", unsafe_allow_html=True)

# ─── Initialisation DB ────────────────────────────────────────────────────────
@st.cache_resource
def setup_database():
    return init_db()

db_ok = setup_database()

# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="main-header" style="display:flex;align-items:center;gap:16px;">
    <img src="{FERRAPD_LOGO_URL}" width="56" height="56" style="border-radius:8px;flex-shrink:0;" alt="FERRAPD" />
    <div>
        <h1 style="margin:0;">BSS Explorer</h1>
        <p style="margin:0.3rem 0 0 0;">Banque du Sous-Sol BRGM — Collecte hydrogéologique automatisée | FERRAPD</p>
    </div>
</div>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(FERRAPD_ICONE_URL, width=80)
    st.markdown("### Navigation")
    page = st.radio(
        "Page",
        [f"{APP_ICON} Nouvelle collecte", "📋 Historique", "ℹ️ À propos"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    if db_ok:
        sessions = list_sessions()
        st.metric("Sessions en base", len(sessions))
    else:
        st.warning("Base de données non connectée")
    st.markdown("---")
    st.caption("BSS Explorer v14 — FERRAPD\nDonnées : BRGM / Géorisques / ADES")


# ─── Fonctions d'affichage ────────────────────────────────────────────────────

def build_folium_map(ouvrages: list, lat_centre: float, lon_centre: float,
                     emprise_m: int, code_site: str, georisques: Optional[dict]) -> folium.Map:
    """Construit une carte Folium enrichie."""
    import math

    m = folium.Map(location=[lat_centre, lon_centre], zoom_start=16, tiles=None)

    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satellite Esri",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr="CARTO", name="CARTO Dark",
    ).add_to(m)

    # Emprise
    half = emprise_m / 2
    dlat = half / 111000
    dlon = half / (111000 * math.cos(math.radians(lat_centre)))
    folium.Polygon(
        locations=[
            [lat_centre - dlat, lon_centre - dlon],
            [lat_centre - dlat, lon_centre + dlon],
            [lat_centre + dlat, lon_centre + dlon],
            [lat_centre + dlat, lon_centre - dlon],
        ],
        color="#ef4444", weight=2, dash_array="6 4",
        fill=True, fill_opacity=0.05,
        tooltip=f"Emprise {emprise_m}m × {emprise_m}m",
    ).add_to(m)

    # Marqueur centre
    folium.Marker(
        location=[lat_centre, lon_centre],
        tooltip=f"<b>{code_site}</b><br>Point de référence",
        icon=folium.DivIcon(html='<div style="font-size:20px;line-height:1;">⭐</div>', icon_anchor=(10, 10)),
    ).add_to(m)

    # Marqueurs ouvrages
    for o in ouvrages:
        log = o.get("log_geologique", [])
        docs = o.get("documents", [])

        log_rows = ""
        for c in log:
            color = get_litho_color(c.get("lithologie", ""))
            log_rows += (
                f'<tr>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;">'
                f'{c.get("prof_de","")}&ndash;{c.get("prof_a","")} m</td>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;">'
                f'<span style="display:inline-block;width:10px;height:10px;background:{color};'
                f'border-radius:2px;margin-right:4px;vertical-align:middle;"></span>'
                f'{c.get("lithologie","")}</td>'
                f'<td style="padding:3px 6px;font-size:11px;border-bottom:1px solid #ddd;color:#666;">'
                f'{c.get("stratigraphie","")}</td>'
                f'</tr>'
            )

        docs_html = "".join(
            f'<a href="{d.get("url","#")}" target="_blank" '
            f'style="display:inline-block;margin:3px;padding:4px 8px;background:#1e3a5f;'
            f'color:#93c5fd;border:1px solid #1e40af;border-radius:4px;font-size:11px;'
            f'text-decoration:none;">📄 {d.get("nom","Document")}</a>'
            for d in docs
        )

        geo = georisques or {}
        geo_html = ""
        if geo.get("zone_sismique"):
            geo_html += f'<tr><td style="color:#666;font-size:11px;">IZS</td><td style="font-size:11px;"><b>{geo["zone_sismique"]}</b></td></tr>'
        if geo.get("alea_rga"):
            geo_html += f'<tr><td style="color:#666;font-size:11px;">IARGA</td><td style="font-size:11px;"><b>{geo["alea_rga"]}</b></td></tr>'

        prof_tot = o.get("profondeur_totale")
        prof_inv = o.get("prof_investigation")
        niv_eau  = o.get("niveau_eau")
        niv_date = o.get("niveau_eau_date", "")
        alt_ngf  = o.get("altitude_ngf")
        alt_prec = o.get("altitude_precision", "")

        popup_html = f"""
<div style="font-family:Inter,sans-serif;min-width:320px;max-width:460px;">
  <div style="background:linear-gradient(135deg,#0f2d4a,#1e3a5f);color:#fff;padding:10px 14px;border-radius:6px 6px 0 0;">
    <div style="font-size:14px;font-weight:700;">{APP_ICON} {o.get("code_bss","")}</div>
    <div style="font-size:11px;opacity:0.8;">{o.get("nature","")} — {o.get("nom_commune","")}</div>
  </div>
  <div style="padding:12px;background:#fff;">
    <table style="width:100%;border-collapse:collapse;">
      <tr><td style="color:#555;font-size:12px;width:120px;">Prof. totale</td>
          <td style="font-size:12px;"><b>{f"{prof_tot} m" if prof_tot is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">PIOuv</td>
          <td style="font-size:12px;"><b>{f"{prof_inv} m" if prof_inv is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">PeS</td>
          <td style="font-size:12px;"><b>{f"{niv_eau} m" if niv_eau is not None else "N/D"}{f" ({niv_date})" if niv_date else ""}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">AltOuv</td>
          <td style="font-size:12px;"><b>{f"{alt_ngf} mNGF {alt_prec}" if alt_ngf is not None else "N/D"}</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">DOuvPC</td>
          <td style="font-size:12px;"><b>{o.get("distance_centre_m", 0):.0f} m</b></td></tr>
      <tr><td style="color:#555;font-size:12px;">Aquifère</td>
          <td style="font-size:12px;">{o.get("aquifere","N/D")}</td></tr>
      <tr><td style="color:#555;font-size:12px;">BDCE</td>
          <td style="font-size:12px;">{o.get("bassin_dce","N/D")}</td></tr>
      {geo_html}
    </table>
    {"<div style='margin-top:10px;border-top:1px solid #eee;padding-top:8px;'><div style='font-size:12px;font-weight:600;color:#1e3a5f;margin-bottom:6px;'>📋 Coupe géologique</div><table style='border-collapse:collapse;width:100%;'><thead><tr style='background:#f0f4f8;'><th style='padding:3px 6px;font-size:11px;text-align:left;'>Prof.</th><th style='padding:3px 6px;font-size:11px;text-align:left;'>Lithologie</th><th style='padding:3px 6px;font-size:11px;text-align:left;'>Stratigraphie</th></tr></thead><tbody>" + log_rows + "</tbody></table></div>" if log_rows else ""}
    {"<div style='margin-top:10px;border-top:1px solid #eee;padding-top:8px;'><div style='font-size:12px;font-weight:600;color:#1e3a5f;margin-bottom:4px;'>📄 Documents InfoTerre</div>" + docs_html + "</div>" if docs_html else ""}
    <div style="margin-top:10px;border-top:1px solid #eee;padding-top:8px;display:flex;gap:8px;">
      <a href="{o.get("url_infoterre","#")}" target="_blank"
         style="flex:1;text-align:center;padding:6px;background:#0f4c81;color:#fff;border-radius:5px;font-size:11px;text-decoration:none;">🔗 InfoTerre</a>
      <a href="{o.get("url_ades","#")}" target="_blank"
         style="flex:1;text-align:center;padding:6px;background:#065f46;color:#fff;border-radius:5px;font-size:11px;text-decoration:none;">💧 ADES</a>
    </div>
  </div>
</div>"""

        # Couleur verte si log géologique présent, bleue sinon
        has_log = len(log) > 0
        marker_color = "#15803d" if has_log else "#1e40af"
        marker_fill  = "#22c55e" if has_log else "#3b82f6"

        folium.CircleMarker(
            location=[o.get("lat", 0), o.get("lon", 0)],
            radius=10,
            color=marker_color, weight=2,
            fill=True, fill_color=marker_fill, fill_opacity=0.85,
            tooltip=f"<b>{o.get('code_bss','')}</b><br>{o.get('nature','')} — {o.get('nom_commune','')}<br>{'\U0001f4cb Log géologique' if has_log else ''}<br><small>Cliquer pour la fiche</small>",
            popup=folium.Popup(popup_html, max_width=480),
        ).add_to(m)

    # Légende lithologique — textes en NOIR pour lisibilité
    legend_items = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">'
        f'<div style="width:14px;height:14px;background:{color};border-radius:3px;flex-shrink:0;border:1px solid #ccc;"></div>'
        f'<span style="font-size:11px;color:#111;">{key.capitalize()}</span></div>'
        for key, color in LITHO_COLORS.items() if key != "autre"
    )
    geo_legend = ""
    if georisques:
        geo_legend = (
            f'<hr style="border-color:#ccc;margin:6px 0;">'
            f'<div style="font-size:12px;color:#111;font-weight:600;margin-bottom:4px;">⚡ Géorisques</div>'
            f'<div style="font-size:11px;color:#111;">IZS : <b>{georisques.get("zone_sismique","N/D")}</b></div>'
            f'<div style="font-size:11px;color:#111;">IARGA : <b>{georisques.get("alea_rga","N/D")}</b></div>'
        )
    legend_html = f"""
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:rgba(255,255,255,0.97);
     padding:12px 16px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.18);
     font-family:Inter,sans-serif;max-height:420px;overflow-y:auto;border:1px solid #ddd;">
  <div style="font-size:13px;font-weight:700;color:#111;margin-bottom:8px;">⚡ Légende lithologique</div>
  {legend_items}
  <div style="display:flex;align-items:center;gap:6px;margin:2px 0;">
    <div style="width:14px;height:14px;background:#BDBDBD;border-radius:3px;flex-shrink:0;border:1px solid #ccc;"></div>
    <span style="font-size:11px;color:#111;">Autre</span>
  </div>
  <hr style="border-color:#ccc;margin:6px 0;">
  <div style="font-size:11px;color:#111;display:flex;align-items:center;gap:6px;">
    <span style="color:#ef4444;font-weight:bold;">- - -</span> Emprise {emprise_m}m
  </div>
  <div style="font-size:11px;color:#111;">⭐ Point de référence</div>
  <div style="font-size:11px;color:#111;display:flex;align-items:center;gap:6px;">
    <span style="color:#22c55e;font-size:16px;">●</span> Ouvrage avec log géologique
  </div>
  <div style="font-size:11px;color:#111;display:flex;align-items:center;gap:6px;">
    <span style="color:#3b82f6;font-size:16px;">●</span> Ouvrage sans log
  </div>
  {geo_legend}
</div>"""
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl().add_to(m)
    return m


def render_log_geologique(ouvrages: list):
    """Affiche les logs géologiques avec barre SVG alignée sur le tableau."""
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
            if log:
                # Hauteur d'une ligne de tableau ≈ 35px, alignée sur la barre SVG
                ROW_H = 35
                bar_width = 40
                bar_height = len(log) * ROW_H
                max_prof = max((c.get("prof_a", 0) for c in log), default=1) or 1
                scale = bar_height / max_prof

                svg_rects = ""
                for c in log:
                    y = c.get("prof_de", 0) * scale
                    h = max(ROW_H * 0.9, (c.get("prof_a", 0) - c.get("prof_de", 0)) * scale)
                    color = get_litho_color(c.get("lithologie", ""))
                    svg_rects += (
                        f'<rect x="0" y="{y:.1f}" width="{bar_width}" height="{h:.1f}" '
                        f'fill="{color}" stroke="#fff" stroke-width="0.5"/>'
                    )

                col_svg, col_table = st.columns([1, 6])
                with col_svg:
                    st.markdown(
                        f'<div style="padding-top:38px;">'
                        f'<svg width="{bar_width}" height="{bar_height}" '
                        f'style="border:1px solid #334155;border-radius:3px;">'
                        f'{svg_rects}</svg></div>',
                        unsafe_allow_html=True,
                    )
                with col_table:
                    df_log = pd.DataFrame([{
                        "Profondeur": f"{c.get('prof_de','')} – {c.get('prof_a','')} m",
                        "Lithologie (TeC)": c.get("lithologie", ""),
                        "Stratigraphie (StC)": c.get("stratigraphie", ""),
                        f"PC{idx+1} (m)": round((c.get("prof_a", 0) or 0) - (c.get("prof_de", 0) or 0), 2),
                    } for idx, c in enumerate(log)])
                    st.dataframe(df_log, use_container_width=True, hide_index=True)

            col_a, col_b = st.columns(2)
            with col_a:
                if o.get("url_infoterre"):
                    st.link_button("🔗 Fiche InfoTerre", o["url_infoterre"])
            with col_b:
                if o.get("url_ades"):
                    st.link_button("💧 Données ADES", o["url_ades"])


def render_documents(ouvrages: list):
    """Affiche les documents InfoTerre avec popup modal (iframe) au clic."""
    ouvrages_avec_docs = [o for o in ouvrages if o.get("documents")]
    if not ouvrages_avec_docs:
        st.info("Aucun document numérisé disponible pour les ouvrages de ce site.")
        return

    # JavaScript pour la modale de visualisation des documents
    modal_js = """
<script>
function openDocModal(url, nom) {
    // Supprimer modale existante
    var existing = document.getElementById('bss-doc-modal');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.id = 'bss-doc-modal';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.88);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px;box-sizing:border-box;';

    var bar = document.createElement('div');
    bar.style.cssText = 'width:100%;max-width:1100px;display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;';
    bar.innerHTML = '<span style="color:#93c5fd;font-size:14px;font-weight:600;">📄 ' + nom + '</span>'
      + '<div style="display:flex;gap:8px;">'
      + '<a href="' + url + '" target="_blank" style="padding:6px 14px;background:#1e3a5f;color:#93c5fd;border:1px solid #1e40af;border-radius:6px;font-size:12px;text-decoration:none;">↗ Ouvrir dans un nouvel onglet</a>'
      + '<button onclick="document.getElementById(\'bss-doc-modal\').remove()" style="padding:6px 14px;background:#ef4444;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer;">✕ Fermer</button>'
      + '</div>';
    overlay.appendChild(bar);

    var frame = document.createElement('iframe');
    frame.src = url;
    frame.style.cssText = 'width:100%;max-width:1100px;flex:1;border:none;border-radius:8px;background:#fff;';
    overlay.appendChild(frame);

    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
}
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var m = document.getElementById('bss-doc-modal');
        if (m) m.remove();
    }
});
</script>
"""
    st.markdown(modal_js, unsafe_allow_html=True)

    for o in ouvrages_avec_docs:
        docs = o.get("documents", [])
        code = o.get("code_bss", "")
        commune = o.get("nom_commune", o.get("commune", ""))
        safe_code = code.replace("/", "_").replace(" ", "_")

        with st.expander(f"📄 FAOuv_{safe_code} — {commune} — {len(docs)} document(s)", expanded=False):
            for d in docs:
                url = d.get("url", "#")
                nom = d.get("nom", "Document")
                doc_type = d.get("type", "")

                col_type, col_btn = st.columns([1, 4])
                with col_type:
                    st.caption(doc_type)
                with col_btn:
                    # Bouton qui ouvre la modale JS
                    st.markdown(
                        f'<button onclick="openDocModal(\'{url}\', \'{nom.replace(chr(39), " ")}\' )" '
                        f'style="display:inline-block;margin:3px;padding:5px 12px;background:#1e3a5f;'
                        f'color:#93c5fd;border:1px solid #1e40af;border-radius:5px;font-size:12px;cursor:pointer;">'
                        f'📄 {nom}</button>',
                        unsafe_allow_html=True,
                    )


def build_zip_with_documents(result: dict, site_input: dict, ouvrages: list,
                              lat_c, lon_c, emprise, cs, geo) -> bytes:
    """
    Construit un ZIP en mémoire contenant :
    - BSS_{CS}_{date}.json (export complet)
    - BSS_{CS}_{date}.csv (tableau ouvrages)
    - carte_BSS_{CS}_{date}.html (carte Folium)
    - documents/{code_bss_safe}/{nom_fichier} (documents InfoTerre par ouvrage)
    - README.txt
    """
    import requests as req_lib

    _ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    _cs_clean = (cs or 'site').replace('/', '-').replace(' ', '_')

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:

        # 1. JSON
        output_json = build_output_json(result, site_input)
        json_str = json.dumps(output_json, ensure_ascii=False, indent=2, default=str)
        zf.writestr(f"BSS_{_cs_clean}_{_ts}.json", json_str.encode('utf-8'))

        # 2. CSV
        csv_lines = [
            "code_bss;NInv;COM;LaOPY;LoOPY;PIOuv(m);PeS(m);Date_PeS;AltOuv(mNGF);"
            "Aquifere;BDCE;DOuvPC(m);NCALOuv;NDAOuv;url_infoterre;url_ades"
        ]
        for o in ouvrages:
            csv_lines.append(";".join([
                f'"{o.get("code_bss","")}"',
                f'"{o.get("nature","")}"',
                f'"{o.get("nom_commune", o.get("commune",""))}"',
                str(o.get("lat", "")),
                str(o.get("lon", "")),
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
        zf.writestr(f"BSS_{_cs_clean}_{_ts}.csv",
                    ("\ufeff" + "\n".join(csv_lines)).encode('utf-8'))

        # 3. Carte HTML
        if lat_c and lon_c:
            try:
                fmap = build_folium_map(ouvrages, lat_c, lon_c, emprise, cs, geo or None)
                zf.writestr(f"carte_BSS_{_cs_clean}_{_ts}.html",
                            fmap._repr_html_().encode('utf-8'))
            except Exception:
                pass

        # 4. Documents InfoTerre par ouvrage
        doc_count = 0
        for o in ouvrages:
            docs = o.get("documents", [])
            if not docs:
                continue
            code_bss = o.get("code_bss", "inconnu")
            safe_code = code_bss.replace("/", "_").replace(" ", "_")

            for idx, d in enumerate(docs, start=1):
                url = d.get("url", "")
                nom = d.get("nom", f"doc_{idx}")
                if not url or url == "#":
                    continue

                # Déterminer le nom de fichier
                scan_name = d.get("scan_name", "")
                if scan_name:
                    filename = scan_name
                else:
                    # Extraire depuis l'URL
                    filename = nom.replace(" ", "_").replace("/", "_")
                    if not any(filename.lower().endswith(ext) for ext in ('.pdf', '.tif', '.tiff', '.jpg', '.png')):
                        filename += ".pdf"

                # Télécharger le document
                try:
                    resp = req_lib.get(url, timeout=15, stream=True)
                    if resp.status_code == 200:
                        content = resp.content
                        archive_path = f"documents/{safe_code}/{filename}"
                        zf.writestr(archive_path, content)
                        doc_count += 1
                except Exception:
                    # Si le téléchargement échoue, on continue sans bloquer
                    pass

        # 5. README
        readme = f"""BSS Explorer — Export complet avec documents
======================================================
Site          : {cs}
Coordonnées   : {lat_c}, {lon_c}
Emprise       : {emprise} m
Exporté le    : {datetime.now().strftime('%Y-%m-%d %H:%M')}

Résultats
---------
Ouvrages trouvés  : {len(ouvrages)}
Zone sismique     : {(geo or {}).get('zone_sismique', 'N/A')}
Aléa RGA          : {(geo or {}).get('alea_rga', 'N/A')}
Documents téléchargés : {doc_count}

Contenu du ZIP
--------------
BSS_{_cs_clean}_{_ts}.json   — Données complètes (format BSS Explorer)
BSS_{_cs_clean}_{_ts}.csv    — Tableau CSV (séparateur ; — Excel)
carte_BSS_{_cs_clean}_{_ts}.html — Carte Folium interactive
documents/                    — Documents InfoTerre classés par ouvrage
  {{code_bss}}/
    {{nom_document}}.tif/.pdf
README.txt                    — Ce fichier

Sources
-------
BRGM WFS      : https://geoservices.brgm.fr/geologie
InfoTerre     : http://ficheinfoterre.brgm.fr
Géorisques    : https://www.georisques.gouv.fr
ADES          : https://ades.eaufrance.fr

© FERRAPD — BSS Explorer v14
"""
        zf.writestr("README.txt", readme.encode('utf-8'))

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def render_result_tabs(result: dict, site_input: dict):
    """Affiche les résultats dans des onglets enrichis."""
    ouvrages = result.get("ouvrages", [])
    geo = result.get("georisques", {}) or {}
    closest = result.get("closest") or (ouvrages[0] if ouvrages else None)
    lat_c   = site_input.get("LaOPY", site_input.get("lat", 0))
    lon_c   = site_input.get("LoOPY", site_input.get("lon", 0))
    emprise = site_input.get("emprise_m", 500)
    cs      = site_input.get("CS", site_input.get("code_site", ""))

    nb_logs = sum(1 for o in ouvrages if o.get("log_geologique"))

    # ── Métriques ──────────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("NOuv", result.get("nb_ouvrages", 0))
    with m2:
        # IZS — texte complet, taille réduite si long
        izs = geo.get("zone_sismique", "N/A") or "N/A"
        st.metric("IZS", izs)
    with m3:
        # IARGA — valeur complète, taille réduite via HTML pour éviter la troncature
        iarga = geo.get("alea_rga", "N/A") or "N/A"
        st.markdown(
            '<p style="font-size:11px;color:rgba(250,250,250,0.6);margin:0 0 4px 0;">IARGA</p>'
            f'<p style="font-size:13px;font-weight:700;color:#fff;margin:0;line-height:1.3;word-break:break-word;white-space:normal;">{iarga}</p>',
            unsafe_allow_html=True,
        )
    with m4:
        if closest:
            st.metric("DOuvPC", f"{closest.get('distance_centre_m', 0):.0f} m")
    with m5:
        st.metric("NOuvALog", nb_logs)

    # ── Ouvrage le plus proche ─────────────────────────────────────────────────
    if closest:
        with st.expander("🎯 Ouvrage le plus proche — détail", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(f"**Code BSS :** {closest.get('code_bss','')}")
                st.markdown(f"**NInv :** {closest.get('nature','')}")
                st.markdown(f"**COM :** {closest.get('nom_commune', closest.get('commune',''))}")
                st.markdown(f"**DOuvPC :** {closest.get('distance_centre_m', 0):.0f} m")
            with c2:
                prof_t = closest.get("profondeur_totale")
                prof_i = closest.get("prof_investigation")
                niv    = closest.get("niveau_eau")
                niv_d  = closest.get("niveau_eau_date", "")
                alt    = closest.get("altitude_ngf")
                st.markdown(f"**Prof. totale :** {f'{prof_t} m' if prof_t else 'N/D'}")
                st.markdown(f"**PIOuv :** {f'{prof_i} m' if prof_i else 'N/D'}")
                st.markdown(f"**PeS :** {f'{niv} m' if niv else 'N/D'}{f' ({niv_d})' if niv_d else ''}")
                st.markdown(f"**AltOuv :** {f'{alt} mNGF' if alt else 'N/D'}")
            with c3:
                st.markdown(f"**Aquifère :** {closest.get('aquifere','N/D')}")
                st.markdown(f"**BDCE :** {closest.get('bassin_dce','N/D')}")
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
                    fmap = build_folium_map(ouvrages, lat_c, lon_c, emprise, cs, geo or None)
                    st_folium(fmap, width="100%", height=560, returned_objects=[])
                except Exception as e:
                    st.error(f"Erreur carte : {e}")
        else:
            st.warning("Coordonnées LaOPY/LoOPY non disponibles.")

    with tab_tableau:
        if ouvrages:
            df = pd.DataFrame([{
                "Code BSS":     o.get("code_bss", ""),
                "NInv":         o.get("nature", ""),
                "COM":          o.get("nom_commune", o.get("commune", "")),
                "PIOuv (m)":    o.get("prof_investigation", ""),
                "PeS (m)":      o.get("niveau_eau", ""),
                "Date PeS":     o.get("niveau_eau_date", ""),
                "AltOuv (mNGF)":o.get("altitude_ngf", ""),
                "Aquifère":     o.get("aquifere", ""),
                "BDCE":         o.get("bassin_dce", ""),
                "DOuvPC (m)":   f"{o.get('distance_centre_m', 0):.0f}",
                "NCALOuv":      len(o.get("log_geologique", [])),
                "NDAOuv":       len(o.get("documents", [])),
            } for o in ouvrages])
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown("**Liens directs InfoTerre / ADES :**")
            for o in ouvrages:
                cols = st.columns([2, 1, 1])
                with cols[0]:
                    st.caption(o.get("code_bss", ""))
                with cols[1]:
                    if o.get("url_infoterre"):
                        st.markdown(
                            f'<a href="{o["url_infoterre"]}" target="_blank" '
                            f'style="display:inline-block;padding:3px 8px;background:#0f4c81;color:#fff;'
                            f'border-radius:4px;font-size:11px;text-decoration:none;">🔗 InfoTerre</a>',
                            unsafe_allow_html=True,
                        )
                with cols[2]:
                    if o.get("url_ades"):
                        st.markdown(
                            f'<a href="{o["url_ades"]}" target="_blank" '
                            f'style="display:inline-block;padding:3px 8px;background:#065f46;color:#fff;'
                            f'border-radius:4px;font-size:11px;text-decoration:none;">💧 ADES</a>',
                            unsafe_allow_html=True,
                        )

    with tab_logs:
        render_log_geologique(ouvrages)

    with tab_docs:
        render_documents(ouvrages)

    # ── Exports ────────────────────────────────────────────────────────────────
    st.divider()
    col_dl1, col_dl2, col_dl3, col_dl4 = st.columns(4)
    _ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    _cs_clean = (cs or 'site').replace('/', '-').replace(' ', '_')

    with col_dl1:
        # CSV enrichi avec nomenclature v11
        csv_lines = [
            "code_bss,NInv,COM,LaOPY,LoOPY,PIOuv(m),PeS(m),Date_PeS,AltOuv(mNGF),"
            "Aquifere,BDCE,DOuvPC(m),NCALOuv,NDAOuv,url_infoterre,url_ades"
        ]
        for o in ouvrages:
            csv_lines.append(",".join([
                f'"{o.get("code_bss","")}"',
                f'"{o.get("nature","")}"',
                f'"{o.get("nom_commune", o.get("commune",""))}"',
                str(o.get("lat", "")),
                str(o.get("lon", "")),
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
            file_name=f"BSS_{_cs_clean}_{_ts}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_dl2:
        # JSON sortie avec nomenclature v12
        output_json = build_output_json(result, site_input)
        json_bytes = json.dumps(output_json, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        json_filename = f"BSS_{_cs_clean}_{_ts}.json"
        st.download_button(
            "📥 Exporter JSON",
            data=json_bytes,
            file_name=json_filename,
            mime="application/json",
            use_container_width=True,
            help=(
                f"Cliquez pour télécharger '{json_filename}'.\n"
                "Dans la fenêtre de téléchargement Windows, naviguez vers votre dossier OneDrive "
                "(ex : C:\\Users\\VotreNom\\OneDrive\\BSS) pour enregistrer directement dans l'arborescence."
            ),
        )
        # Indication visuelle pour OneDrive
        st.caption(
            f"💾 Fichier : `{json_filename}` — Enregistrez dans votre dossier OneDrive "
            "via la fenêtre de téléchargement Windows."
        )

    with col_dl3:
        if lat_c and lon_c:
            try:
                fmap_dl = build_folium_map(ouvrages, lat_c, lon_c, emprise, cs, geo or None)
                st.download_button(
                    "🗺️ Carte HTML",
                    data=fmap_dl._repr_html_().encode("utf-8"),
                    file_name=f"carte_BSS_{_cs_clean}_{_ts}.html",
                    mime="text/html",
                    use_container_width=True,
                )
            except Exception:
                pass

    with col_dl4:
        # ZIP complet avec documents InfoTerre classés par ouvrage
        nb_docs_total = sum(len(o.get("documents", [])) for o in ouvrages)
        zip_label = f"📦 ZIP + {nb_docs_total} doc(s)" if nb_docs_total > 0 else "📦 ZIP complet"
        # Générer le ZIP au clic (session_state pour éviter DuplicateElementId)
        zip_key = f"zip_btn_{_cs_clean}"
        if zip_key not in st.session_state:
            st.session_state[zip_key] = None
        if st.button(zip_label, use_container_width=True, key=zip_key + "_trigger",
                     help="Télécharge un ZIP contenant le JSON, le CSV, la carte HTML "
                          "et tous les documents InfoTerre classés par ouvrage."):
            with st.spinner(f"Préparation du ZIP ({nb_docs_total} document(s) InfoTerre)..."):
                st.session_state[zip_key] = build_zip_with_documents(
                    result, site_input, ouvrages, lat_c, lon_c, emprise, cs, geo
                )
        if st.session_state[zip_key] is not None:
            st.download_button(
                "⬇️ Télécharger le ZIP",
                data=st.session_state[zip_key],
                file_name=f"BSS_{_cs_clean}_{_ts}.zip",
                mime="application/zip",
                use_container_width=True,
                key=zip_key + "_download",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : NOUVELLE COLLECTE
# ═══════════════════════════════════════════════════════════════════════════════
if page == f"{APP_ICON} Nouvelle collecte":
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
                cs_val = st.text_input(
                    "CS — Code site BSS",
                    value="FRA034001MPL",
                    placeholder="FRA0XX00XXX ou FRA0XX0XXXX",
                    help="Format attendu : FRA0XX00XXX ou FRA0XX0XXXX (X = chiffre 0–9)",
                )
                col_lat, col_lon = st.columns(2)
                with col_lat:
                    la_val = st.number_input("LaOPY — Latitude (°)", value=43.610769, format="%.6f", step=0.000001)
                with col_lon:
                    lo_val = st.number_input("LoOPY — Longitude (°)", value=3.876716, format="%.6f", step=0.000001)
                emprise_m = st.slider("Emprise de recherche (m)", 100, 2000, 500, 50)
                submitted = st.form_submit_button("▶ Collecter les données", use_container_width=True, type="primary")

            if submitted:
                if not validate_cs(cs_val):
                    st.warning(f"⚠️ Le code site « {cs_val} » ne correspond pas au format attendu (FRA0XX00XXX ou FRA0XX0XXXX). La collecte sera quand même lancée.")
                sites_to_collect = [{
                    "CS": cs_val.strip(),
                    "code_site": cs_val.strip(),
                    "LaOPY": la_val,
                    "LoOPY": lo_val,
                    "lat": la_val,
                    "lon": lo_val,
                    "emprise_m": emprise_m,
                }]

        else:
            uploaded = st.file_uploader("Charger un fichier JSON", type=["json"])
            st.caption("""
**Format attendu :**
```json
// Site unique
{"CS":"FRA034001MPL","LaOPY":43.610769,"LoOPY":3.876716}

// Liste de sites
[
  {"CS":"FRA034001MPL","LaOPY":43.610769,"LoOPY":3.876716},
  {"CS":"FRA030001MPL","LaOPY":43.836699,"LoOPY":4.360054,"emprise_m":800}
]

// Format batch (généré par le script de traitement)
{"batch":true,"sites":[{"CS":"...","LaOPY":...,"LoOPY":...}, ...]}
```
Les clés `lat`/`lon`/`code_site` sont également acceptées pour la rétrocompatibilité.
Les champs internes (`_meta`, `EDSM`) sont automatiquement ignorés à l'import.
""")
            if uploaded:
                try:
                    raw = json.loads(uploaded.read().decode("utf-8"))
                    # Support format batch {"batch": true, "sites": [...]}
                    if isinstance(raw, dict) and raw.get("batch") and "sites" in raw:
                        raw_list = raw["sites"]
                    elif isinstance(raw, list):
                        raw_list = raw
                    elif isinstance(raw, dict):
                        raw_list = [raw]
                    else:
                        raw_list = []

                    # Filtrer les champs internes (_meta, EDSM, batch, etc.)
                    for item in raw_list:
                        for key in list(item.keys()):
                            if key.startswith('_') or key in ('EDSM', 'batch', 'date_generation', 'nombre_sites'):
                                del item[key]

                    # Normaliser les clés (CS→code_site, LaOPY→lat, LoOPY→lon)
                    for item in raw_list:
                        if "CS" in item and "code_site" not in item:
                            item["code_site"] = item["CS"]
                        if "LaOPY" in item and "lat" not in item:
                            item["lat"] = item["LaOPY"]
                        if "LoOPY" in item and "lon" not in item:
                            item["lon"] = item["LoOPY"]

                    sites_to_collect = raw_list
                    st.success(f"{len(sites_to_collect)} site(s) chargé(s)")
                    if st.button("▶ Lancer la collecte en lot", type="primary", use_container_width=True):
                        pass
                except Exception as e:
                    st.error(f"Erreur de lecture JSON : {e}")

    with col_result:
        if sites_to_collect:
            st.subheader(f"Collecte de {len(sites_to_collect)} site(s)")
            results = []

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, site in enumerate(sites_to_collect):
                cs_label = site.get("CS", site.get("code_site", f"site {i+1}"))
                status_text.info(f"⏳ Collecte en cours : {cs_label} ({i+1}/{len(sites_to_collect)})")
                try:
                    result = collect_bss(
                        lat=float(site.get("LaOPY", site.get("lat", 0))),
                        lon=float(site.get("LoOPY", site.get("lon", 0))),
                        emprise_m=int(site.get("emprise_m", 2000)),
                        code_site=site.get("CS", site.get("code_site", "")),
                    )
                    result["input"] = site
                    result["success"] = True
                    results.append(result)

                    if db_ok:
                        upsert_session(
                            code_site=result.get("code_site", cs_label),
                            lat=float(site.get("LaOPY", site.get("lat", 0))),
                            lon=float(site.get("LoOPY", site.get("lon", 0))),
                            emprise_m=int(site.get("emprise_m", 2000)),
                            nb_ouvrages=result.get("nb_ouvrages", 0),
                            mode=result.get("mode", "WFS BRGM"),
                            ouvrages=result.get("ouvrages", []),
                            georisques=result.get("georisques"),
                            map_html=None,
                        )
                except Exception as e:
                    results.append({"input": site, "success": False, "error": str(e), "nb_ouvrages": 0})

                progress_bar.progress((i + 1) / len(sites_to_collect))

            status_text.success(
                f"✅ Collecte terminée — {sum(1 for r in results if r.get('success'))} succès, "
                f"{sum(1 for r in results if not r.get('success'))} échec(s)"
            )

            for result in results:
                site_input = result.get("input", {})
                cs_label = result.get("code_site") or site_input.get("CS", site_input.get("code_site", "Site inconnu"))
                if not result.get("success"):
                    st.error(f"❌ {cs_label} — {result.get('error', 'Erreur inconnue')}")
                    continue

                with st.expander(f"✅ {cs_label} — {result.get('nb_ouvrages', 0)} ouvrage(s)", expanded=len(results) == 1):
                    render_result_tabs(result, site_input)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : HISTORIQUE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Historique":
    st.subheader("Historique des collectes")

    if not db_ok:
        st.warning("⚠️ Base de données non connectée. Configurez DATABASE_URL dans les Secrets Streamlit.")
        st.code('DATABASE_URL = "postgresql://user:password@host:5432/dbname?sslmode=require"')
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
                    st.markdown(
                        f"**{session['code_site']}** — {session['nb_ouvrages']} ouvrage(s)\n"
                        f"`LaOPY={session['lat']:.4f}, LoOPY={session['lon']:.4f}` — "
                        f"Emprise : {session['emprise_m']} m — Mis à jour : {date_str}"
                    )
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
                cs_loaded = loaded.get("code_site", "session")
                st.subheader(f"Session chargée : {cs_loaded}")

                loaded_result = {
                    "code_site":   cs_loaded,
                    "nb_ouvrages": loaded.get("nb_ouvrages", 0),
                    "ouvrages":    loaded.get("ouvrages", []),
                    "georisques":  loaded.get("georisques"),
                    "closest":     loaded.get("ouvrages", [None])[0] if loaded.get("ouvrages") else None,
                    "success":     True,
                }
                loaded_input = {
                    "CS":       cs_loaded,
                    "code_site": cs_loaded,
                    "LaOPY":    loaded.get("lat", 0),
                    "LoOPY":    loaded.get("lon", 0),
                    "lat":      loaded.get("lat", 0),
                    "lon":      loaded.get("lon", 0),
                    "emprise_m": loaded.get("emprise_m", 500),
                }
                render_result_tabs(loaded_result, loaded_input)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE : À PROPOS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "ℹ️ À propos":
    st.subheader("À propos de BSS Explorer")
    st.markdown(f"""
**BSS Explorer** est un outil de collecte hydrogéologique automatisée développé par **FERRAPD**.

### Sources de données

| Source | Description |
|--------|-------------|
| [BRGM WFS](https://geoservices.brgm.fr/geologie) | Ouvrages BSS (forages, piézomètres, puits) |
| [InfoTerre BRGM](http://ficheinfoterre.brgm.fr) | Fiches détaillées (AltOuv, log géologique, PIOuv, PeS, documents numérisés) |
| [Géorisques](https://www.georisques.gouv.fr) | IZS (zone sismique), IARGA (aléa RGA) |
| [ADES](https://ades.eaufrance.fr) | Données piézométriques nationales |

### Nomenclature des paramètres

**Entrée JSON :**

| Paramètre | Signification | Format |
|-----------|---------------|--------|
| `CS` | Code site BSS | `FRA0XX00XXX` ou `FRA0XX0XXXX` |
| `LaOPY` | Latitude WGS84 | Degrés décimaux (°) |
| `LoOPY` | Longitude WGS84 | Degrés décimaux (°) |
| `emprise_m` | Emprise de recherche | Mètres (défaut : 500 formulaire / 2000 batch) |

**Sortie JSON (niveau site) :**

| Paramètre | Signification | Unité |
|-----------|---------------|-------|
| `NOuv` | Nombre d'ouvrages | u |
| `NOuvALog` | Nombre d'ouvrages avec log géologique | u |
| `IZS` | Zone sismique | — |
| `IARGA` | Aléa retrait-gonflement argiles | — |

**Sortie JSON (par ouvrage) :**

| Paramètre | Signification | Unité |
|-----------|---------------|-------|
| `COM` | Commune | — |
| `BDCE` | Bassin DCE | — |
| `DOuvPC` | Distance ouvrage / point ciblé | m |
| `NInv` | Nature de l'investigation | — |
| `PIOuv` | Profondeur d'investigation | m |
| `PeS` | Niveau d'eau (piézométrie au sol) | m |
| `AltOuv` | Altitude de l'ouvrage | mNGF |
| `NDAOuv` | Nombre de documents associés | u |
| `NCALOuv` | Nombre de couches dans le log | u |
| `PC1..PCX` | Épaisseur de chaque couche (prof_a − prof_de) | m |
| `TeC1..TeCX` | Texture (lithologie) de chaque couche | — |
| `StC1..StCX` | Stratigraphie de chaque couche | — |
| `FAOuv_{{code_bss}}` | Dossier des documents numérisés | — |

### Version
BSS Explorer v14 — Build {datetime.now().strftime('%Y-%m-%d')}
""")
