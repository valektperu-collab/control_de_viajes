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
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 12px;
        border: 1px solid #374151;
        color: #f9fafb;
    }
    .hero-card-verde { background: #064e3b; border-color: #059669; }
    .hero-card-ambar { background: #78350f; border-color: #d97706; }
    .hero-card-rojo  { background: #7f1d1d; border-color: #dc2626; }
    .hero-card-dark  { background: #111827; border-color: #374151; }

    .hero-title { font-size: 0.95rem; font-weight: 700; margin-bottom: 6px; }
    .hero-meta { font-size: 1.25rem; font-weight: 800; }
    .hero-sub { font-size: 0.85rem; margin-bottom: 6px; opacity: 0.9; }
    .hero-alert {
        background: rgba(0, 0, 0, 0.28);
        border-left: 3px solid #ffffff;
        padding: 8px 10px;
        border-radius: 4px;
        font-size: 0.84rem;
        margin-top: 8px;
        line-height: 1.35;
    }
    .btn-especial {
        margin-bottom: 6px;
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
    if img_pil.mode in ("RGBA", "P"):
        img_pil = img_pil.convert("RGB")
    img_pil.save(buffer, format="JPEG", quality=75, optimize=True)
    buffer.seek(0)
    return Image.open(buffer)

# --- PROCESAMIENTO CON IA ---
def procesar_capturas(imagenes):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise Exception("Falta configurar GEMINI_API_KEY en Secrets.")

    client = genai.Client(api_key=api_key)
    imagenes_ligeras = [optimizar_imagen(im) for im in imagenes]

    prompt = """
    Analiza esta(s) captura(s) de taxi.
    Devuelve ÚNICAMENTE un JSON sin bloques ```json:
    {
        "id_servicio": "ID o número",
        "anio": 2026,
        "mes": 10,
        "dia": 21,
        "hora": 23,
        "minuto": 20,
        "monto_total": 0.0,
        "tarifa_base": 0.0,
        "monto_recargos": 0.0,
        "monto_reembolsos": 0.0,
        "tipo_asignacion": "Directo, Kusi, Latam o CDS",
        "tipo_usuario": "Corporativo o Particular",
        "metodo_pago": "Empresa, Efectivo, Yape o Tarjeta"
    }
    REGLAS:
    - Extrae FECHA y HORA reales del servicio de la cabecera. Si no figura el año, usa 2026.
    - monto_recargos: suma extras/espera/recargos.
    - monto_reembolsos: peajes y estacionamientos.
    - tipo_asignacion: Directo, Kusi, Latam o CDS.
    - metodo_pago: Empresa, Efectivo, Yape o Tarjeta.
    """

    modelos = ["gemini-3.8-flash", "gemini-2.5-flash"]
    ultimo_error = None

    for m in modelos:
        try:
            contents = imagenes_ligeras + [prompt]
            response = client.models.generate_content(model=m, contents=contents)
            raw = response.text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            return json.loads(raw.strip())
        except Exception as e:
            ultimo_error = e
            continue

    raise Exception(f"Error de lectura: {ultimo_error}")

def calcular_fecha_operativa(dt, tipo_turno, hora_corte):
    if tipo_turno == "Nocturno" and dt.hour < hora_corte:
        return (dt - timedelta(days=1)).date()
    return dt.date()

def obtener_semana_operativa(fecha_op):
    return f"{fecha_op.isocalendar()[0]}-W{fecha_op.isocalendar()[1]:02d}"

# --- ENCABEZADO ---
st.markdown('<div class="titulo-app">🚖 Copiloto de Metas y Guardia</div>', unsafe_allow_html=True)

st.sidebar.header("⚙️ Ajustes")
tipo_turno = st.sidebar.selectbox("Turno", ["Nocturno", "Diurno"])
hora_corte = st.sidebar.number_input("Corte Salida", min_value=1, max_value=12, value=5 if tipo_turno == "Nocturno" else 0)
regimen = st.sidebar.selectbox("Régimen", [5, 6], format_func=lambda x: f"{x}x{7-x}")

tab1, tab2, tab3, tab4 = st.tabs(["📊 Guardia & Metas", "📸 Registrar", "💰 Abonos", "📋 Historial"])

if "datos_autocompletados" not in st.session_state:
    st.session_state["datos_autocompletados"] = {
        "id_servicio": "",
        "fecha": datetime.now().date(),
        "hora": datetime.now().time(),
        "tarifa_base": 0.0,
        "recargos": 0.0,
        "reembolsos": 0.0,
        "tipo_usuario": "Corporativo",
        "tipo_asignacion": "Directo",
        "metodo_pago": "Empresa"
    }

for pre_id in ["pre1", "pre2", "pre3"]:
    if f"{pre_id}_monto" not in st.session_state:
        st.session_state[f"{pre_id}_monto"] = 0.0
    if f"{pre_id}_estado" not in st.session_state:
        st.session_state[f"{pre_id}_estado"] = "Pendiente"

# --- TAB 1: GUARDIA & METAS ---
with tab1:
    conn = sqlite3.connect("produccion_viajes.db")
    df = pd.read_sql_query("SELECT * FROM viajes", conn)
    conn.close()

    if not df.empty:
        semanas_disp = sorted(df["semana_operativa"].unique(), reverse=True)
        semana_sel = st.selectbox("Semana Operativa", semanas_disp)
        df_sem = df[df["semana_operativa"] == semana_sel]

        resumen_diario = df_sem.groupby("fecha_operativa").agg(
            viajes=('id_servicio', 'count'),
            produccion=('produccion_neta', 'sum')
        ).reset_index()

        ultimo_dia = resumen_diario["fecha_operativa"].max()
        prod_hoy = resumen_diario[resumen_diario["fecha_operativa"] == ultimo_dia]["produccion"].values[0]
        prod_acumulada = df_sem["produccion_neta"].sum()
        dias_trabajados = len(resumen_diario)
        
        meta_semanal_100 = regimen * 450.0
        falta_semanal_total = max(0.0, meta_semanal_100 - prod_acumulada)
        
        # Preasignados activos
        preasig_activos = sum([
            st.session_state[f"{p}_monto"] for p in ["pre1", "pre2", "pre3"] 
            if st.session_state[f"{p}_estado"] == "Pendiente"
        ])
        total_guardia_proy = prod_hoy + preasig_activos

        # 1. EVALUACIÓN DE PISOS HISTÓRICOS (Condición eliminatoria estricta)
        dias_cerrados = resumen_diario[resumen_diario["fecha_operativa"] != ultimo_dia]
        alguna_falla_piso_cerrada = (dias_cerrados["produccion"] < 300.0).any() if not dias_cerrados.empty else False

        # 2. EVALUACIÓN DE PISO DE HOY
        falta_piso_hoy = max(0.0, 300.0 - total_guardia_proy)
        piso_hoy_ok = (falta_piso_hoy == 0)

        # 3. EVALUACIÓN DE RITMO SEMANAL
        ritmo_esperado_acumulado = dias_trabajados * 450.0
        diferencia_ritmo = prod_acumulada - ritmo_esperado_acumulado
        dias_futuros = max(0, regimen - dias_trabajados)
        promedio_futuro = (falta_semanal_total / dias_futuros) if dias_futuros > 0 else 0.0

        # Determinación del Color de la Hero Card y Mensaje Táctico
        ahora_hora = datetime.now().hour

        if alguna_falla_piso_cerrada:
            clase_card = "hero-card-rojo"
            badge_estado = "🔴 BONO ANULADO (Falla en Piso Diario)"
            alerta_msg = "🚫 Un día anterior cerró con menos de S/ 300. La empresa no liquidará bono semanal por incumplimiento de piso."
        elif not piso_hoy_ok:
            clase_card = "hero-card-ambar"
            badge_estado = f"⚠️ FALTA PISO (S/ {falta_piso_hoy:.2f})"
            if ahora_hora >= 1 and ahora_hora < 5:
                alerta_msg = f"🚨 URGENTE: Pasó la 1:00 AM y faltan S/ {falta_piso_hoy:.2f} para los S/ 300 obligatorios de hoy. ¡No cierres sin cumplir el piso o pierdes el bono de toda la semana!"
            else:
                alerta_msg = f"⚠️ Meta obligatoria inmediata: asegurar el piso de S/ 300. Te faltan S/ {falta_piso_hoy:.2f} antes de que termine el turno."
        else:
            # Piso asegurado
            if falta_semanal_total <= 50.0 and falta_semanal_total > 0:
                clase_card = "hero-card-verde"
                badge_estado = "🟢 CIERRE DE SEMANA CERCA"
                alerta_msg = f"🎯 Estás a solo S/ {falta_semanal_total:.2f} de los S/ 2,250. Recuerda que para cobrar el bono debes cumplir siempre el piso de S/ 300 de cada día trabajado."
            elif diferencia_ritmo >= 0:
                clase_card = "hero-card-verde"
                badge_estado = "🟢 RITMO GANADOR"
                if dias_futuros > 0:
                    alerta_msg = f"🔥 ¡Excelente ritmo! Vas +S/ {diferencia_ritmo:.2f} sobre la meta acumulada. Gracias a tu colchón, tu promedio para los siguientes {dias_futuros} día(s) bajó a solo S/ {promedio_futuro:.2f}/día. Evalúa descansar."
                else:
                    alerta_msg = "🏆 ¡Misión semanal completada! Bono de S/ 100 asegurado."
            else:
                clase_card = "hero-card-ambar"
                badge_estado = "🟡 PISO OK | RITMO POR RECUPERAR"
                alerta_msg = f"✅ Piso de S/ 300 asegurado. Tu promedio diario para los siguientes {dias_futuros} día(s) es de S/ {promedio_futuro:.2f} para alcanzar los S/ 2,250."

        pct_semana = min(100.0, (prod_acumulada / meta_semanal_100) * 100) if meta_semanal_100 > 0 else 0

        # RENDER HERO CARD
        st.markdown(f"""
        <div class="hero-card {clase_card}">
            <div class="hero-title">🚖 GUARDIA EN CURSO &nbsp;|&nbsp; {badge_estado}</div>
            <div class="hero-meta">S/ {prod_acumulada:.2f} <span style="font-size:0.9rem; font-weight:400;">de S/ {meta_semanal_100:.0f} (Bono 100)</span></div>
            <div class="hero-sub">Pendiente semanal: <strong>S/ {falta_semanal_total:.2f}</strong> ({pct_semana:.1f}% avance)</div>
            <hr style="margin: 6px 0; border-color: rgba(255,255,255,0.2);">
            <div style="font-size:0.88rem; margin-top:4px;">
                <strong>Hoy Hecho:</strong> S/ {prod_hoy:.2f} &nbsp;|&nbsp; <strong>Preasig:</strong> S/ {preasig_activos:.2f}<br>
                <strong>Piso Hoy:</strong> {"✅ ASEGURADO" if piso_hoy_ok else f"<span style='color:#fca5a5;'>Faltan S/ {falta_piso_hoy:.2f}</span>"} &nbsp;|&nbsp;
                <strong>Promedio Restante:</strong> S/ {promedio_futuro:.2f}/día
            </div>
            <div class="hero-alert">{alerta_msg}</div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("💼 Preasignados de la Guardia (Pre 1, 2, 3)", expanded=False):
            for pid, label in [("pre1", "Pre 1"), ("pre2", "Pre 2"), ("pre3", "Pre 3")]:
                col_a, col_b, col_c = st.columns([2, 1.5, 1.5])
                with col_a:
                    st.session_state[f"{pid}_monto"] = st.number_input(f"{label} (S/)", min_value=0.0, value=st.session_state[f"{pid}_monto"], step=5.0, key=f"inp_{pid}")
                with col_b:
                    st.caption(f"Estado: {st.session_state[f'{pid}_estado']}")
                with col_c:
                    if st.session_state[f"{pid}_estado"] == "Pendiente":
                        if st.button("✅ Hecho", key=f"ok_{pid}"):
                            st.session_state[f"{pid}_estado"] = "Hecho"
                            st.rerun()
                        if st.button("❌ Caído", key=f"no_{pid}"):
                            st.session_state[f"{pid}_estado"] = "Caído"
                            st.rerun()
                    else:
                        if st.button("Reactivar", key=f"rst_{pid}"):
                            st.session_state[f"{pid}_estado"] = "Pendiente"
                            st.rerun()

        st.markdown("#### 📋 Detalle de la Semana")
        resumen_mostrar = resumen_diario.copy()
        resumen_mostrar.index = [f"Día {i+1}" for i in range(len(resumen_mostrar))]
        resumen_mostrar["Piso S/ 300"] = resumen_mostrar["produccion"].apply(lambda x: "✅ OK" if x >= 300.0 else "❌ Falla")
        st.dataframe(resumen_mostrar, use_container_width=True)
    else:
        st.info("Sin registros aún. Ve a la pestaña '📸 Registrar' para cargar tu primer viaje.")

# --- TAB 2: REGISTRAR VIAJE Y JORNADAS ESPECIALES ---
with tab2:
    st.markdown("#### 🌟 Jornadas Especiales (Meta Cumplida S/ 450)")
    c_esp1, c_esp2 = st.columns(2)
    with c_esp1:
        if st.button("🎓 Jornada Instructor / Guía", use_container_width=True):
            f_hoy = datetime.now().date()
            sem_op = obtener_semana_operativa(f_hoy)
            id_inst = f"INST-{f_hoy.strftime('%Y%m%d')}"
            conn = sqlite3.connect("produccion_viajes.db")
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO viajes (
                    id_servicio, fecha_hora_viaje, fecha_operativa, semana_operativa,
                    tipo_asignacion, tipo_usuario, categoria_servicio, metodo_pago,
                    origen, destino, distancia_km, duracion_min, tarifa_base,
                    monto_recargos, monto_reembolsos, produccion_neta, monto_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id_inst, datetime.now().isoformat(), f_hoy.isoformat(), sem_op,
                "Instructor / Guía", "Empresa", "Especial", "Empresa",
                "Sede", "Ruta Guía", 0.0, 0.0, 450.0,
                0.0, 0.0, 450.0, 450.0
            ))
            conn.commit()
            conn.close()
            st.success(f"✅ Jornada de Instructor acreditada (S/ 450.00) para el {f_hoy}.")
            st.rerun()

    with c_esp2:
        if st.button("🏨 Guardia en CDS (Hotel Costa del Sol)", use_container_width=True):
            f_hoy = datetime.now().date()
            sem_op = obtener_semana_operativa(f_hoy)
            id_cds = f"CDS-{f_hoy.strftime('%Y%m%d')}"
            conn = sqlite3.connect("produccion_viajes.db")
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO viajes (
                    id_servicio, fecha_hora_viaje, fecha_operativa, semana_operativa,
                    tipo_asignacion, tipo_usuario, categoria_servicio, metodo_pago,
                    origen, destino, distancia_km, duracion_min, tarifa_base,
                    monto_recargos, monto_reembolsos, produccion_neta, monto_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id_cds, datetime.now().isoformat(), f_hoy.isoformat(), sem_op,
                "CDS - Costa del Sol", "Empresa", "Especial", "Empresa",
                "Hotel Costa del Sol", "Aeropuerto / Sede", 0.0, 0.0, 450.0,
                0.0, 0.0, 450.0, 450.0
            ))
            conn.commit()
            conn.close()
            st.success(f"✅ Guardia en CDS acreditada (S/ 450.00) para el {f_hoy}.")
            st.rerun()

    st.divider()
    st.markdown("#### 📸 Registro por Capturas de Taxi")
    uploaded_files = st.file_uploader("Captura(s) del viaje", type=["jpg", "jpeg", "png"], accept_multiple_files=True)

    if uploaded_files:
        imgs = [Image.open(f) for f in uploaded_files]
        if st.button("⚡ Extraer Rápido con IA"):
            with st.spinner("Optimizando y analizando..."):
                try:
                    datos = procesar_capturas(imgs)
                    try:
                        f_dt = date(int(datos.get("anio", 2026)), int(datos.get("mes", 10)), int(datos.get("dia", 21)))
                        h_dt = time(int(datos.get("hora", 0)), int(datos.get("minuto", 0)))
                    except Exception:
                        f_dt = datetime.now().date()
                        h_dt = datetime.now().time()

                    asig_raw = str(datos.get("tipo_asignacion", "")).lower()
                    if "cds" in asig_raw or "costa" in asig_raw:
                        tipo_asig_final = "CDS"
                    elif "latam" in asig_raw:
                        tipo_asig_final = "Latam"
                    elif "kusi" in asig_raw:
                        tipo_asig_final = "Kusi"
                    else:
                        tipo_asig_final = "Directo"

                    pago_raw = str(datos.get("metodo_pago", "")).lower()
                    pago_final = "Yape" if "yape" in pago_raw else ("Efectivo" if "efectiv" in pago_raw else ("Tarjeta / POS" if "tarjeta" in pago_raw else "Empresa"))

                    st.session_state["datos_autocompletados"] = {
                        "id_servicio": str(datos.get("id_servicio", "")),
                        "fecha": f_dt,
                        "hora": h_dt,
                        "tarifa_base": float(datos.get("tarifa_base", 0.0)),
                        "recargos": float(datos.get("monto_recargos", 0.0)),
                        "reembolsos": float(datos.get("monto_reembolsos", 0.0)),
                        "tipo_usuario": datos.get("tipo_usuario", "Corporativo"),
                        "tipo_asignacion": tipo_asig_final,
                        "metodo_pago": pago_final
                    }
                    st.success("¡Datos listos!")
                except Exception as e:
                    st.error(f"Aviso: {e}")

    with st.form("form_registro"):
        d = st.session_state["datos_autocompletados"]
        c1, c2 = st.columns(2)
        with c1:
            id_servicio = st.text_input("N° Servicio", value=d["id_servicio"])
            fecha_input = st.date_input("Fecha", value=d["fecha"])
            tarifa_base = st.number_input("Tarifa Base (S/)", min_value=0.0, value=d["tarifa_base"], step=1.0)
            recargos = st.number_input("Recargos (S/)", min_value=0.0, value=d["recargos"], step=1.0)
        with c2:
            hora_input = st.time_input("Hora", value=d["hora"])
            reembolsos = st.number_input("Peajes (S/)", min_value=0.0, value=d["reembolsos"], step=1.0)
            opciones_asig = ["Directo", "Kusi", "Latam", "CDS"]
            idx_asig = opciones_asig.index(d["tipo_asignacion"]) if d["tipo_asignacion"] in opciones_asig else 0
            tipo_asignacion = st.selectbox("Asignación", opciones_asig, index=idx_asig)
            opciones_pago = ["Empresa", "Efectivo", "Yape", "Tarjeta / POS"]
            idx_pago = opciones_pago.index(d["metodo_pago"]) if d["metodo_pago"] in opciones_pago else 0
            metodo_pago = st.selectbox("Pago", opciones_pago, index=idx_pago)

        produccion_neta = tarifa_base + recargos
        monto_total = produccion_neta + reembolsos
        st.markdown(f"**Neta:** S/ {produccion_neta:.2f} | **Total a Rendir:** S/ {monto_total:.2f}")

        if st.form_submit_button("💾 Guardar Viaje"):
            if not id_servicio:
                st.error("Ingresa N° de Servicio.")
            else:
                dt_viaje = datetime.combine(fecha_input, hora_input)
                f_op = calcular_fecha_operativa(dt_viaje, tipo_turno, hora_corte)
                sem_op = obtener_semana_operativa(f_op)

                conn = sqlite3.connect("produccion_viajes.db")
                c = conn.cursor()
                try:
                    c.execute('''
                        INSERT OR REPLACE INTO viajes (
                            id_servicio, fecha_hora_viaje, fecha_operativa, semana_operativa,
                            tipo_asignacion, tipo_usuario, categoria_servicio, metodo_pago,
                            origen, destino, distancia_km, duracion_min, tarifa_base,
                            monto_recargos, monto_reembolsos, produccion_neta, monto_total
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        id_servicio, dt_viaje.isoformat(), f_op.isoformat(), sem_op,
                        tipo_asignacion, "Corporativo", "Lite", metodo_pago,
                        "", "", 0.0, 0.0, tarifa_base,
                        recargos, reembolsos, produccion_neta, monto_total
                    ))
                    conn.commit()
                    st.success("Guardado exitosamente.")
                except Exception as e:
                    st.error(f"Error: {e}")
                finally:
                    conn.close()

# --- TAB 3: RENDICIÓN EFECTIVO / YAPE ---
with tab3:
    st.markdown("#### 💰 Rendición Efectivo / Yape")
    conn = sqlite3.connect("produccion_viajes.db")
    df_liq = pd.read_sql_query("""
        SELECT id_servicio, fecha_hora_viaje, metodo_pago, monto_total, abono_enviado, fecha_hora_abono 
        FROM viajes 
        WHERE metodo_pago IN ('Efectivo', 'Yape')
        ORDER BY fecha_hora_viaje DESC
    """, conn)
    conn.close()

    if not df_liq.empty:
        pendientes = df_liq[df_liq["abono_enviado"] == 0]
        st.markdown(f"**Pendiente por rendir:** S/ {pendientes['monto_total'].sum():.2f} ({len(pendientes)} viajes)")
        st.divider()

        for _, row in df_liq.iterrows():
            id_serv = row["id_servicio"]
            formato_envio = f"{id_serv} - 2986"
            esta_enviado = bool(row["abono_enviado"])

            c1, c2 = st.columns([3, 2])
            with c1:
                st.code(formato_envio, language=None)
                st.caption(f"**S/ {row['monto_total']:.2f}** ({row['metodo_pago']})")
                if esta_enviado:
                    st.success(f"Enviado: {row['fecha_hora_abono']}")
            with c2:
                if not esta_enviado:
                    if st.button("Marcar Enviado", key=f"env_{id_serv}"):
                        ahora_str = datetime.now().strftime("%d/%m/%y %H:%M")
                        conn = sqlite3.connect("produccion_viajes.db")
                        cur = conn.cursor()
                        cur.execute("UPDATE viajes SET abono_enviado = 1, fecha_hora_abono = ? WHERE id_servicio = ?", (ahora_str, id_serv))
                        conn.commit()
                        conn.close()
                        st.rerun()
                else:
                    if st.button("Desmarcar", key=f"des_{id_serv}"):
                        conn = sqlite3.connect("produccion_viajes.db")
                        cur = conn.cursor()
                        cur.execute("UPDATE viajes SET abono_enviado = 0, fecha_hora_abono = NULL WHERE id_servicio = ?", (id_serv,))
                        conn.commit()
                        conn.close()
                        st.rerun()
    else:
        st.info("No hay viajes en Efectivo o Yape.")

# --- TAB 4: HISTORIAL CON FILTRO SEMANAL ---
with tab4:
    st.markdown("#### 📋 Historial de Servicios")
    conn = sqlite3.connect("produccion_viajes.db")
    df_all = pd.read_sql_query("""
        SELECT 
            id_servicio,
            semana_operativa,
            fecha_operativa,
            tipo_asignacion,
            metodo_pago,
            produccion_neta,
            monto_reembolsos,
            monto_total
        FROM viajes 
        ORDER BY fecha_hora_viaje DESC
    """, conn)
    conn.close()

    if not df_all.empty:
        semanas_lista = sorted(df_all["semana_operativa"].unique().tolist(), reverse=True)
        semana_actual = semanas_lista[0]
        
        opciones_filtro = [f"Esta semana ({semana_actual})"]
        if len(semanas_lista) > 1:
            opciones_filtro.append(f"Semana anterior ({semanas_lista[1]})")
        opciones_filtro.append("Ver todos los servicios")

        filtro_sel = st.selectbox("Filtrar por:", opciones_filtro, index=0)

        if "Esta semana" in filtro_sel:
            df_mostrar = df_all[df_all["semana_operativa"] == semana_actual]
        elif "Semana anterior" in filtro_sel:
            df_mostrar = df_all[df_all["semana_operativa"] == semanas_lista[1]]
        else:
            df_mostrar = df_all

        st.caption(f"Mostrando {len(df_mostrar)} registros.")
        st.dataframe(df_mostrar, use_container_width=True, hide_index=True)

        st.divider()
        lista_ids = df_mostrar["id_servicio"].tolist()
        id_borrar = st.selectbox("Eliminar servicio o jornada:", lista_ids)
        if st.button("❌ Confirmar Eliminación"):
            conn = sqlite3.connect("produccion_viajes.db")
            c = conn.cursor()
            c.execute("DELETE FROM viajes WHERE id_servicio = ?", (id_borrar,))
            conn.commit()
            conn.close()
            st.success("Registro eliminado.")
            st.rerun()
    else:
        st.info("Sin registros.")
