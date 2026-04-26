"""
db.py — Gestion de la base de données PostgreSQL (Supabase)
============================================================
Stocke et récupère les sessions BSS (résultats de collecte).
Les credentials sont lus depuis st.secrets ou les variables d'environnement.
"""

import json
import os
from datetime import datetime
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


def get_connection():
    """Retourne une connexion PostgreSQL depuis les secrets Streamlit ou les variables d'env."""
    # Priorité 1 : variables d'environnement (Docker / OVH)
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url, sslmode="require")

    # Priorité 2 : secrets Streamlit
    try:
        import streamlit as st
        db_url = st.secrets.get("DATABASE_URL") or st.secrets.get("database", {}).get("url")
        if db_url:
            return psycopg2.connect(db_url, sslmode="require")
    except Exception:
        pass

    raise RuntimeError(
        "DATABASE_URL non configurée. "
        "Ajoutez-la dans les Secrets Streamlit ou en variable d'environnement."
    )


def init_db():
    """Crée la table bss_sessions si elle n'existe pas."""
    if not HAS_PSYCOPG2:
        return False
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bss_sessions (
                id SERIAL PRIMARY KEY,
                code_site VARCHAR(64) NOT NULL,
                lat DECIMAL(10,6) NOT NULL,
                lon DECIMAL(10,6) NOT NULL,
                emprise_m INTEGER NOT NULL DEFAULT 500,
                nb_ouvrages INTEGER NOT NULL DEFAULT 0,
                mode VARCHAR(32) NOT NULL DEFAULT 'WFS BRGM',
                ouvrages_json TEXT,
                georisques_json TEXT,
                map_html TEXT,
                csv_data TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(code_site, lat, lon, emprise_m)
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[DB] Erreur init_db : {e}")
        return False


def upsert_session(
    code_site: str,
    lat: float,
    lon: float,
    emprise_m: int,
    nb_ouvrages: int,
    mode: str,
    ouvrages: list,
    georisques: Optional[dict],
    map_html: Optional[str] = None,
    csv_data: Optional[str] = None,
) -> Optional[int]:
    """Insère ou met à jour une session BSS. Retourne l'ID de la session."""
    if not HAS_PSYCOPG2:
        return None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bss_sessions
                (code_site, lat, lon, emprise_m, nb_ouvrages, mode,
                 ouvrages_json, georisques_json, map_html, csv_data, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (code_site, lat, lon, emprise_m)
            DO UPDATE SET
                nb_ouvrages   = EXCLUDED.nb_ouvrages,
                mode          = EXCLUDED.mode,
                ouvrages_json = EXCLUDED.ouvrages_json,
                georisques_json = EXCLUDED.georisques_json,
                map_html      = EXCLUDED.map_html,
                csv_data      = EXCLUDED.csv_data,
                updated_at    = NOW()
            RETURNING id
        """, (
            code_site, lat, lon, emprise_m, nb_ouvrages, mode,
            json.dumps(ouvrages, ensure_ascii=False),
            json.dumps(georisques, ensure_ascii=False) if georisques else None,
            map_html,
            csv_data,
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[DB] Erreur upsert_session : {e}")
        return None


def list_sessions() -> list:
    """Retourne la liste des sessions (sans les données volumineuses)."""
    if not HAS_PSYCOPG2:
        return []
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, code_site, lat, lon, emprise_m, nb_ouvrages, mode,
                   created_at, updated_at
            FROM bss_sessions
            ORDER BY updated_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"[DB] Erreur list_sessions : {e}")
        return []


def get_session(session_id: int) -> Optional[dict]:
    """Retourne une session complète par son ID."""
    if not HAS_PSYCOPG2:
        return None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bss_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        result = dict(row)
        result["ouvrages"] = json.loads(result["ouvrages_json"]) if result["ouvrages_json"] else []
        result["georisques"] = json.loads(result["georisques_json"]) if result["georisques_json"] else None
        return result
    except Exception as e:
        print(f"[DB] Erreur get_session : {e}")
        return None


def delete_session(session_id: int) -> bool:
    """Supprime une session par son ID."""
    if not HAS_PSYCOPG2:
        return False
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM bss_sessions WHERE id = %s", (session_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        conn.close()
        return deleted
    except Exception as e:
        print(f"[DB] Erreur delete_session : {e}")
        return False


def get_all_sessions_for_refresh() -> list:
    """Retourne toutes les sessions avec leurs paramètres d'entrée pour le rafraîchissement."""
    if not HAS_PSYCOPG2:
        return []
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, code_site, lat, lon, emprise_m
            FROM bss_sessions
            ORDER BY updated_at ASC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"[DB] Erreur get_all_sessions_for_refresh : {e}")
        return []
