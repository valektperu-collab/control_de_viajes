import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
from PIL import Image
import google.generativeai as genai

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Control de Producción y Metas", page_icon="🚖", layout="wide")

# --- CONEXIÓN AUTOMÁTICA A GEMINI ---
def obtener_modelo_vision():
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        
        # Obtener lista de modelos compatibles para generación de contenido
        modelos_disponibles = [
            m.name for m in genai.list_models() 
            if 'generateContent' in m.supported_generation_methods
        ]
        
        # Prioridad de modelos de visión
        preferencias = [
            "models/gemini-1.5-flash",
            "models/gemini-1.5-flash-latest",
            "models/gemini-1.5-pro",
            "models/gemini-2.0-flash",
            "models/gemini-pro-vision"
        ]
        
        for pref in preferencias:
            if pref in modelos_disponibles:
                return genai.GenerativeModel(pref)
        
        # Fallback al primer modelo disponible
        if modelos_disponibles:
            return genai.GenerativeModel(modelos_disponibles[0])
        return None
    except Exception as e:
        st.error(f"Error al conectar con Gemini: {e}")
        return None

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
            origen TEXT,
            destino TEXT,
            distancia_km REAL,
            duracion_min REAL,
            tarifa_base REAL,
            monto_recargos REAL,
            monto_reembolsos REAL,
            produccion_neta REAL,
            monto_total REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- FUNCIONES DE EXTRACCIÓN Y CÁLCULO ---
def extraer_datos_imagen(image, model):
    prompt = """
    Analiza esta captura de pantalla de un servicio de aplicativo de taxi.
    Devuelve ÚNICAMENTE un objeto JSON válido (sin bloques de código markdown, solo el texto plano JSON) con las siguientes claves exactas:
    {
        "id_servicio": "número de servicio o ID (string)",
        "fecha_hora_texto": "fecha y hora tal como aparece (string)",
        "monto_total": 0.0,
        "tarifa_base": 0.0,
        "monto_recargos": 0.0,
        "monto_reembolsos": 0.0,
        "distancia_km": 0.0,
        "duracion_min": 0.0,
        "origen": "punto de partida (string)",
        "destino": "punto de llegada (string)",
        "tipo_asignacion": "Reserva / Directo u otro",
        "tipo_usuario": "Corporativo / Particular",
        "categoria_servicio": "Lite u otro"
    }
    REGLAS FINANCIERAS CRÍTICAS:
    - monto_recargos: suma de dinámica, tiempo de espera, car seat, recargo reserva u horario exclusivo.
    - monto_reembolsos: suma ÚNICAMENTE de peajes y estacionamientos.
    - monto_total: el cobro bruto total mostrado en pantalla.
    """
    response = model.generate_content([image, prompt])
    raw_text = response.text.strip()
    
    # Limpieza por si devuelve bloques markdown
    if "```json" in raw_text:
        raw_text = raw_text.split("```json")[1].split("```")[0]
    elif "```" in raw_text:
        raw_text = raw_text.split("```")[1].split("```")[0]
        
    return json.loads(raw_text.strip())

def calcular_fecha_operativa(dt, tipo_turno, hora_corte):
    if tipo_turno == "Nocturno" and dt.hour < hora_corte:
        return (dt - timedelta(days=1)).date()
    return dt.date()

def obtener_semana_operativa(fecha_op):
    return f"{fecha_op.isocalendar()[0]}-W{fecha_op.isocalendar()[1]:02d}"

# --- INTERFAZ ---
st.title("🚖 Control de Viajes y Metas Semanales")

st.sidebar.header("⚙️ Configuración de Turno")
tipo_turno = st.sidebar.selectbox("Tipo de Turno", ["Nocturno", "Diurno"])
hora_corte = st.sidebar.number_input("Hora de salida nocturna (Corte)", min_value=1, max_value=12, value=5 if tipo_turno == "Nocturno" else 0)
regimen = st.sidebar.selectbox("Régimen laboral", [5, 6], format_func=lambda x: f"{x}x{7-x}")

tab1, tab2, tab3 = st.tabs(["📸 Registrar Viaje", "📊 Panel Semanal", "📋 Historial"])

# Inicializar sesión para autocompletar si se sube imagen
if "datos_autocompletados" not in st.session_state:
    st.session_state["datos_autocompletados"] = {
        "id_servicio": "",
        "tarifa_base": 0.0,
        "recargos": 0.0,
        "reembolsos": 0.0,
        "distancia": 0.0,
        "duracion": 0.0,
        "origen": "",
        "destino": "",
        "tipo_usuario": "Corporativo",
        "tipo_asignacion": "Reserva"
    }

# --- TAB 1: REGISTRAR VIAJE ---
with tab1:
    st.subheader("Subir captura de pantalla")
    uploaded_file = st.file_uploader("Selecciona o toma una foto del servicio", type=["jpg", "jpeg", "png"])
    
    if uploaded_file is not None:
        col_img, col_btn = st.columns([1, 2])
        img = Image.open(uploaded_file)
        with col_img:
            st.image(img, caption="Captura cargada", use_column_width=True)
        with col_btn:
            if st.button("🔍 Extraer Datos con IA"):
                vision_model = obtener_modelo_vision()
                if vision_model is None:
                    st.error("No se pudo conectar con el modelo. Revisa tu GEMINI_API_KEY en Secrets.")
                else:
                    with st.spinner("Procesando captura con IA..."):
                        try:
                            datos = extraer_datos_imagen(img, vision_model)
                            st.session_state["datos_autocompletados"] = {
                                "id_servicio": str(datos.get("id_servicio", "")),
                                "tarifa_base": float(datos.get("tarifa_base", 0.0)),
                                "recargos": float(datos.get("monto_recargos", 0.0)),
                                "reembolsos": float(datos.get("monto_reembolsos", 0.0)),
                                "distancia": float(datos.get("distancia_km", 0.0)),
                                "duracion": float(datos.get("duracion_min", 0.0)),
                                "origen": str(datos.get("origen", "")),
                                "destino": str(datos.get("destino", "")),
                                "tipo_usuario": datos.get("tipo_usuario", "Corporativo"),
                                "tipo_asignacion": datos.get("tipo_asignacion", "Reserva")
                            }
                            st.success("¡Datos extraídos! Verifica los campos abajo y presiona Guardar.")
                        except Exception as e:
                            st.error(f"Error al analizar la imagen: {e}")

    # Formulario
    with st.form("form_registro"):
        d = st.session_state["datos_autocompletados"]
        st.write("Verifica o completa los datos del servicio:")
        col1, col2 = st.columns
