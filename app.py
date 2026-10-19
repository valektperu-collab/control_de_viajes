import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta, date, time
import json
from PIL import Image
from google import genai
from google.genai import types

st.set_page_config(page_title="Control de Viajes y Metas", page_icon="🚖", layout="wide")

# --- BASE DE DATOS ---
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

# --- PROCESAMIENTO CON IA ---
def procesar_capturas(imagenes):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise Exception("Falta configurar GEMINI_API_KEY en Secrets de Streamlit.")

    client = genai.Client(api_key=api_key)

    prompt = """
    Analiza esta(s) captura(s) de pantalla de un aplicativo de taxi (puede ser 1 captura completa o 2 partes continuas del mismo viaje).
    Devuelve ÚNICAMENTE un objeto JSON válido (sin formato markdown ni comillas ```) con estas claves exactas:
    {
        "id_servicio": "número de servicio o ID (string)",
        "anio": 2026,
        "mes": 10,
        "dia": 18,
        "hora": 23,
        "minuto": 20,
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
    REGLAS ESTRICTAS:
    - Extrae la FECHA y HORA reales del servicio que aparecen en la cabecera. Si el año no figura, usa 2026.
    - monto_recargos: suma de dinámica, tiempo de espera, car seat, recargo reserva u horario exclusivo.
    - monto_reembolsos: suma ÚNICAMENTE de peajes y estacionamientos.
    - Si son dos imágenes del mismo viaje, combina la información sin duplicar montos.
    """

    modelos = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.8-flash"]
    ultimo_error = None

    for m in modelos:
        try:
            contents = list(imagenes) + [prompt]
            response = client.models.generate_content(
                model=m,
                contents=contents,
            )
            raw_text = response.text.strip()

            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0]
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0]

            return json.loads(raw_text.strip())
        except Exception as e:
            ultimo_error = e
            continue

    raise Exception(f"Error al procesar: {ultimo_error}")

def calcular_fecha_operativa(dt, tipo_turno, hora_corte):
    # En turno nocturno, las horas de madrugada (antes del corte) pertenecen a la guardia del día anterior
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

tab1, tab2, tab3 = st.tabs(["📸 Registrar Viaje", "📊 Panel Semanal & Semáforo", "📋 Historial"])

if "datos_autocompletados" not in st.session_state:
    st.session_state["datos_autocompletados"] = {
        "id_servicio": "",
        "fecha": datetime.now().date(),
        "hora": datetime.now().time(),
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
    st.subheader("Subir captura(s) del servicio")
    st.caption("Sube 1 captura (Kusi) o 2 capturas continuas (Directo).")

    uploaded_files = st.file_uploader(
        "Selecciona 1 o 2 capturas", 
        type=["jpg", "jpeg", "png"], 
        accept_multiple_files=True
    )

    if uploaded_files:
        imgs = [Image.open(f) for f in uploaded_files]
        cols = st.columns(len(imgs))
        for idx, img in enumerate(imgs):
            with cols[idx]:
                st.image(img, caption=f"Parte {idx+1}", use_column_width=True)

        if st.button("🔍 Extraer Datos con IA"):
            with st.spinner("Analizando con IA..."):
                try:
                    datos = procesar_capturas(imgs)

                    try:
                        f_dt = date(int(datos.get("anio", 2026)), int(datos.get("mes", 10)), int(datos.get("dia", 18)))
                        h_dt = time(int(datos.get("hora", 0)), int(datos.get("minuto", 0)))
                    except Exception:
                        f_dt = datetime.now().date()
                        h_dt = datetime.now().time()

                    st.session_state["datos_autocompletados"] = {
                        "id_servicio": str(datos.get("id_servicio", "")),
                        "fecha": f_dt,
                        "hora": h_dt,
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
                    st.success("¡Datos extraídos con fecha y hora del servicio!")
                except Exception as e:
                    st.error(f"Aviso de extracción: {e}")

    with st.form("form_registro"):
        d = st.session_state["datos_autocompletados"]
        st.write("Verifica los datos antes de guardar:")
        col1, col2 = st.columns(2)

        with col1:
            id_servicio = st.text_input("N° de Servicio", value=d["id_servicio"])
            fecha_input = st.date_input("Fecha del viaje (detectada)", value=d["fecha"])
            hora_input = st.time_input("Hora del viaje (detectada)", value=d["hora"])
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

        st.info(f"**Producción Neta:** S/ {produccion_neta:.2f} | **Total con peajes:** S/ {monto_total:.2f}")

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
                    st.success(f"Viaje N° {id_servicio} guardado para el turno {f_op} (Semana {sem_op}).")
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
                finally:
                    conn.close()

# --- TAB 2: PANEL SEMANAL & PROYECCIÓN ---
with tab2:
    conn = sqlite3.connect("produccion_viajes.db")
    df = pd.read_sql_query("SELECT * FROM viajes", conn)
    conn.close()

    if not df.empty:
        semanas_disp = sorted(df["semana_operativa"].unique(), reverse=True)
        semana_sel = st.selectbox("Seleccionar Semana Operativa", semanas_disp)
        df_sem = df[df["semana_operativa"] == semana_sel]

        # Resumen por día operativo
        resumen_diario = df_sem.groupby("fecha_operativa").agg(
            viajes=('id_servicio', 'count'),
            produccion=('produccion_neta', 'sum'),
            reembolsos=('monto_reembolsos', 'sum'),
            km=('distancia_km', 'sum')
        ).reset_index()

        # Determinar el último día registrado de la semana seleccionada
        ultimo_dia_registrado = resumen_diario["fecha_operativa"].max()
        
        dias_trabajados = len(resumen_diario)
        produccion_total = df_sem["produccion_neta"].sum()
        viajes_totales = len(df_sem)

        # Metas según régimen
        meta_b25 = regimen * 400.0
        meta_b55 = regimen * 421.0
        meta_b100 = regimen * 450.0

        # --- SIMULADOR DE PREASIGNADOS ---
        st.subheader("🔮 Planificador de Guardia (Preasignados)")
        with st.expander("Calcular proyección con servicios preasignados", expanded=True):
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                preasig_1 = st.number_input("Monto Preasignado 1 (S/)", min_value=0.0, value=0.0, step=5.0)
            with col_p2:
                preasig_2 = st.number_input("Monto Preasignado 2 (S/)", min_value=0.0, value=0.0, step=5.0)
            
            suma_preasig = preasig_1 + preasig_2
            
            # Obtener producción de la guardia activa
            prod_guardia_activa = resumen_diario[resumen_diario["fecha_operativa"] == ultimo_dia_registrado]["produccion"].values[0]
            prod_guardia_proyectada = prod_guardia_activa + suma_preasig
            prod_semana_proyectada = produccion_total + suma_preasig

            falta_piso_guardia = max(0.0, 300.0 - prod_guardia_proyectada)
            falta_b100_proy = max(0.0, meta_b100 - prod_semana_proyectada)

            if suma_preasig > 0:
                st.info(f"💼 **Preasignados sumados:** S/ {suma_preasig:.2f} | **Producción proyectada de la guardia:** S/ {prod_guardia_proyectada:.2f}")
                if falta_piso_guardia == 0:
                    st.success("✅ ¡Piso diario de S/ 300 asegurado con tus preasignados!")
                else:
                    st.warning(f"⚠️ Aún te faltan **S/ {falta_piso_guardia:.2f}** en la calle para llegar al piso de S/ 300.")
                
                if falta_b100_proy == 0:
                    st.success("🔥 ¡Con estos servicios completas la meta para el **Bono de S/ 100**!")
                else:
                    st.write(f"🎯 Faltan **S/ {falta_b100_proy:.2f}** antes de cerrar la guardia para alcanzar los S/ 100.")

        st.divider()

        # --- ESTADO DE BONOS ---
        st.subheader("🎯 Estado Actual de Bonos Semanales")
        
        falta_b25 = max(0.0, meta_b25 - produccion_total)
        falta_b55 = max(0.0, meta_b55 - produccion_total)
        falta_b100 = max(0.0, meta_b100 - produccion_total)

        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            if falta_b25 == 0:
                st.success("🎉 **Bono S/ 25:** ¡ALCANZADO!")
            else:
                st.info(f"🥉 **Para Bono S/ 25:**\nFaltan **S/ {falta_b25:.2f}**")

        with col_b2:
            if falta_b55 == 0:
                st.success("🎉 **Bono S/ 55:** ¡ALCANZADO!")
            else:
                st.info(f"🥈 **Para Bono S/ 55:**\nFaltan **S/ {falta_b55:.2f}**")

        with col_b3:
            if falta_b100 == 0:
                st.success("🔥 **Bono S/ 100:** ¡ALCANZADO!")
            else:
                st.info(f"🥇 **Para Bono S/ 100:**\nFaltan **S/ {falta_b100:.2f}**")

        # Barra de progreso
        progreso = min(1.0, produccion_total / meta_b100) if meta_b100 > 0 else 0.0
        st.write(f"**Progreso hacia Bono S/ 100 (Meta: S/ {meta_b100:.2f}):**")
        st.progress(progreso)

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Días Trabajados", f"{dias_trabajados} / {regimen}")
        col_m2.metric("N° Total de Viajes", viajes_totales)
        col_m3.metric("Producción Neta", f"S/ {produccion_total:.2f}")

        bono_actual = 100 if produccion_total >= meta_b100 else (55 if produccion_total >= meta_b55 else (25 if produccion_total >= meta_b25 else 0))
        col_m4.metric("Bono Alcanzado", f"S/ {bono_actual}.00")

        st.divider()
        st.subheader("📋 Detalle Diario de la Semana")

        # Corregir índice para que empiece en Día 1
        resumen_mostrar = resumen_diario.copy()
        resumen_mostrar.index = [f"Día {i+1}" for i in range(len(resumen_mostrar))]
        resumen_mostrar["Estado Piso"] = resumen_mostrar["produccion"].apply(
            lambda x: "✅ Cumplido (>=300)" if x >= 300.0 else "❌ Falla (<300)"
        )
        st.dataframe(resumen_mostrar, use_container_width=True)
    else:
        st.info("Aún no hay viajes registrados.")

# --- TAB 3: HISTORIAL Y ELIMINACIÓN ---
with tab3:
    st.subheader("Todos los Viajes Registrados")
    conn = sqlite3.connect("produccion_viajes.db")
    df_all = pd.read_sql_query("SELECT * FROM viajes ORDER BY fecha_hora_viaje DESC", conn)
    conn.close()

    if not df_all.empty:
        st.dataframe(df_all, use_container_width=True)

        st.divider()
        st.subheader("🗑️ Eliminar Viaje")
        lista_ids = df_all["id_servicio"].tolist()
        id_borrar = st.selectbox("Selecciona el N° de Servicio a eliminar:", lista_ids)

        if st.button("❌ Confirmar Eliminación"):
            conn = sqlite3.connect("produccion_viajes.db")
            c = conn.cursor()
            c.execute("DELETE FROM viajes WHERE id_servicio = ?", (id_borrar,))
            conn.commit()
            conn.close()
            st.success(f"Servicio {id_borrar} eliminado.")
            st.rerun()
    else:
        st.info("No hay registros en la base de datos.")
    
