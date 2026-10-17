import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
from PIL import Image
import google.generativeai as genai

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Control de Producción y Metas", page_icon="🚖", layout="wide")

# --- CONEXIÓN DIRECTA A GEMINI ---
def obtener_modelo_vision():
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        # Usamos el modelo más reciente indicado por la API
        for nombre_modelo in ["gemini-3.8-flash", "gemini-3.5-flash-lite"]:
            try:
                m = genai.GenerativeModel(nombre_modelo)
                return m
            except Exception:
                continue
        return genai.GenerativeModel("gemini-3.8-flash")
    except Exception as e:
        st.error(f"Error al configurar la API: {e}")
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
    Devuelve ÚNICAMENTE un objeto JSON válido (sin bloques ```json, solo texto crudo JSON) con estas claves:
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
    - monto_total: cobro total mostrado.
    """
    response = model.generate_content([image, prompt])
    raw_text = response.text.strip()
    
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
                            st.success("¡Datos extraídos! Revisa abajo y presiona Guardar.")
                        except Exception as e:
                            st.error(f"Error al analizar la imagen: {e}")

    # Formulario
    with st.form("form_registro"):
        d = st.session_state["datos_autocompletados"]
        st.write("Verifica o completa los datos del servicio:")
        col1, col2 = st.columns(2)
        
        with col1:
            id_servicio = st.text_input("N° de Servicio", value=d["id_servicio"])
            fecha_input = st.date_input("Fecha del viaje", datetime.now().date())
            hora_input = st.time_input("Hora del viaje", datetime.now().time())
            tarifa_base = st.number_input("Tarifa Base (S/)", min_value=0.0, value=d["tarifa_base"], step=1.0)
            recargos = st.number_input("Recargos / Esperas / Extras (S/)", min_value=0.0, value=d["recargos"], step=1.0)
            
        with col2:
            reembolsos = st.number_input("Peajes / Estacionamiento (S/)", min_value=0.0, value=d["reembolsos"], step=1.0)
            distancia = st.number_input("Distancia (Km)", min_value=0.0, value=d["distancia"], step=0.1)
            duracion = st.number_input("Duración (min)", min_value=0.0, value=d["duracion"], step=1.0)
            origen = st.text_input("Origen", value=d["origen"])
            destino = st.text_input("Destino", value=d["destino"])
            tipo_usuario = st.selectbox("Tipo de Usuario", ["Corporativo", "Particular"], index=0 if d["tipo_usuario"] == "Corporativo" else 1)
            tipo_asignacion = st.selectbox("Tipo Asignación", ["Reserva", "Directo"], index=0 if d["tipo_asignacion"] == "Reserva" else 1)

        produccion_neta = tarifa_base + recargos
        monto_total = produccion_neta + reembolsos
        
        st.info(f"**Producción Neta (Válida para meta):** S/ {produccion_neta:.2f} | **Total con peajes:** S/ {monto_total:.2f}")
        
        submitted = st.form_submit_button("💾 Guardar Viaje")
        
        if submitted:
            if not id_servicio:
                st.error("Debes ingresar el N° de Servicio.")
            else:
                dt_viaje = datetime.combine(fecha_input, hora_input)
                f_op = calcular_fecha_operativa(dt_viaje, tipo_turno, hora_corte)
                sem_op = obtener_semana_operativa(f_op)
                
                conn = sqlite3.connect("produccion_viajes.db")
                c = conn.cursor()
                try:
                    c.execute('''
                        INSERT OR REPLACE INTO viajes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        id_servicio, dt_viaje.isoformat(), f_op.isoformat(), sem_op,
                        tipo_asignacion, tipo_usuario, "Lite", origen, destino,
                        distancia, duracion, tarifa_base, recargos, reembolsos,
                        produccion_neta, monto_total
                    ))
                    conn.commit()
                    st.success(f"Viaje N° {id_servicio} guardado exitosamente en el turno {f_op}.")
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
                finally:
                    conn.close()

# --- TAB 2: PANEL SEMANAL ---
with tab2:
    st.subheader("Estado de Metas y Bonos")
    
    conn = sqlite3.connect("produccion_viajes.db")
    df = pd.read_sql_query("SELECT * FROM viajes", conn)
    conn.close()
    
    if not df.empty:
        semanas_disp = df["semana_operativa"].unique()
        semana_sel = st.selectbox("Seleccionar Semana Operativa", semanas_disp, index=len(semanas_disp)-1)
        
        df_sem = df[df["semana_operativa"] == semana_sel]
        
        resumen_diario = df_sem.groupby("fecha_operativa").agg(
            viajes=('id_servicio', 'count'),
            produccion=('produccion_neta', 'sum'),
            reembolsos=('monto_reembolsos', 'sum'),
            km=('distancia_km', 'sum')
        ).reset_index()
        
        dias_trabajados = len(resumen_diario)
        produccion_total = df_sem["produccion_neta"].sum()
        viajes_totales = len(df_sem)
        
        dias_cumplen_piso = (resumen_diario["produccion"] >= 300.0).sum()
        piso_ok = (dias_cumplen_piso == dias_trabajados) and (dias_trabajados == regimen)
        
        meta_b25 = regimen * 400.0
        meta_b55 = regimen * 421.0
        meta_b100 = regimen * 450.0
        
        bono_obtenido = 0
        if piso_ok:
            if produccion_total >= meta_b100:
                bono_obtenido = 100
            elif produccion_total >= meta_b55:
                bono_obtenido = 55
            elif produccion_total >= meta_b25:
                bono_obtenido = 25

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Días Trabajados", f"{dias_trabajados} / {regimen}")
        col_m2.metric("N° de Viajes", viajes_totales)
        col_m3.metric("Producción Acumulada", f"S/ {produccion_total:.2f}")
        col_m4.metric("Bono Semanal", f"S/ {bono_obtenido}.00")

        if dias_trabajados > 0:
            if dias_cumplen_piso < dias_trabajados:
                st.error("⚠️ Atención: Hay al menos un día donde no se alcanzaron los S/ 300. Bono bloqueado.")
            elif dias_trabajados < regimen:
                st.warning(f"Llevas {dias_trabajados} de {regimen} días cumpliendo el piso de S/ 300.")
            else:
                st.success("✅ Cumpliste el piso de S/ 300 en todos los días de tu turno.")

        st.write(f"**Progreso hacia Bono S/ 100 (Meta: S/ {meta_b100:.2f}):**")
        progreso = min(1.0, produccion_total / meta_b100) if meta_b100 > 0 else 0.0
        st.progress(progreso)
        
        if produccion_total < meta_b100:
            restante = meta_b100 - produccion_total
            st.caption(f"Faltan **S/ {restante:.2f}** en la producción para el bono de S/ 100.")

        st.divider()
        st.subheader("Desglose Diario de la Semana")
        st.dataframe(resumen_diario, use_container_width=True)
    else:
        st.info("Aún no hay viajes registrados.")

# --- TAB 3: HISTORIAL ---
with tab3:
    st.subheader("Todos los Viajes Registrados")
    conn = sqlite3.connect("produccion_viajes.db")
    df_all = pd.read_sql_query("SELECT * FROM viajes ORDER BY fecha_hora_viaje DESC", conn)
    conn.close()
    if not df_all.empty:
        st.dataframe(df_all, use_container_width=True)
    else:
        st.info("No hay registros en la base de datos.")
