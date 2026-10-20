import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta, date, time
import json
import io
from PIL import Image
from google import genai

# --- CONFIGURACIÓN DE PÁGINA Y ESTILOS MÓVILES ---
st.set_page_config(page_title="Control de Viajes", page_icon="🚖", layout="wide")

st.markdown("""
<style>
    /* Ajuste para que el encabezado no choque con la barra superior */
    .block-container { 
        padding-top: 3.8rem !important; 
        padding-bottom: 2rem !important; 
        padding-left: 0.8rem !important; 
        padding-right: 0.8rem !important; 
    }
    .titulo-app {
        font-size: 1.22rem !important;
        font-weight: 800 !important;
        color: #111827;
        margin-bottom: 0.5rem !important;
    }
    
    .hero-card {
        background: #111827;
        color: #f9fafb;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 12px;
        border: 1px solid #374151;
    }
    .hero-title { font-size: 0.95rem; font-weight: 700; color: #fbbf24; margin-bottom: 6px; }
    .hero-meta { font-size: 1.1rem; font-weight: 700; color: #ffffff; }
    .hero-sub { font-size: 0.85rem; color: #9ca3af; margin-bottom: 6px; }
    .hero-alert {
        background: #1f2937;
        border-left: 3px solid #3b82f6;
        padding: 8px 10px;
        border-radius: 4px;
        font-size: 0.84rem;
        margin-top: 8px;
        line-height: 1.35;
    }
</style>
""", unsafe_allow_html=True)

# --- BASE DE DATOS LOCAL ---
def init_db():
    conn = sqlite3.connect("produccion_viajes.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS viajes (
            id_servicio TEXT PRIMARY KEY,
            fecha_hora_viaje TEXT,
            fecha_operativa TEXT,
            semana_operativa TEXT,
            tipo_asignacion TEXT,
            tipo_usuario TEXT,
            categoria_servicio TEXT,
            metodo_pago TEXT,
            origen TEXT,
            destino TEXT,
            distancia_km REAL,
            duracion_min REAL,
            tarifa_base REAL,
            monto_recargos REAL,
            monto_reembolsos REAL,
            produccion_neta REAL,
            monto_total REAL,
            abono_enviado INTEGER DEFAULT 0,
            fecha_hora_abono TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE viajes ADD COLUMN metodo_pago TEXT DEFAULT 'Empresa'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE viajes ADD COLUMN abono_enviado INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE viajes ADD COLUMN fecha_hora_abono TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

init_db()

# --- OPTIMIZACIÓN RÁPIDA DE IMÁGENES ---
def optimizar_imagen(img_pil):
    w, h = img_pil.size
    if w > 800:
        ratio = 800 / float(w)
        nuevo_h = int(float(h) * ratio)
        img_pil = img_pil.resize((800, nuevo_h), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO()
    if img_pil.mode in
    
