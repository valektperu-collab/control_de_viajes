import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta, date, time
import json
import io
import requests
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
</style>
""", unsafe_allow_html=True)

# URL WEBHOOK GOOGLE SHEETS
GSHEETS_URL = "https://script.google.com/macros/s/AKfycbzbnRBVGJC3AhvoM1gbE7V1p6we8DxpfRyhQfxP0zIJ1G9xCG1YOA6TLtrOwfHh588/exec"

# --- FUNCIONES DE SINCRONIZACIÓN GOOGLE SHEETS ---
def obtener_viajes_remotos():
    try:
        r = requests.get(GSHEETS_URL, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                # Convertir numéricos
                cols_num = ["distancia_km", "duracion_min", "tarifa_base", "monto_recargos", "monto_reembolsos", "produccion_neta", "monto_total"]
                for c in cols_num:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
                if "abono_enviado" in df.columns:
                    df["abono_enviado"] = pd.to_numeric(df["abono_enviado"], errors="coerce").fillna(0).astype(int)
                return df
    except Exception as e:
        st.warning(f"Aviso de sincronización nube: {e}")
    return pd.DataFrame()

def guardar_viaje_remoto(datos_dict):
    try:
        payload = {
            "action": "insert_or_replace",
            "data": datos_dict
        }
        r = requests.post(GSHEETS_URL, json=payload, timeout=12)
        return r.status_code == 200
    except Exception:
        return False

def eliminar_viaje_remoto(id_servicio):
    try:
        payload = {
            "action": "delete",
            "id_servicio": str(id_servicio)
        }
        r = requests.post(GSHEETS_URL, json=payload, timeout=12)
        return r.status_code == 200
    except Exception:
        return False

def actualizar_abono_remoto(id_servicio, estado, fecha_abono=""):
    try:
        payload = {
            "action": "update_abono",
            "id_servicio": str(id_servicio),
            "abono_enviado": int(estado),
            "fecha_hora_abono": str(fecha_abono)
        }
        r = requests.post(GSHEETS_URL, json=payload, timeout=12)
        return r.status_code == 200
    except Exception:
        return False

# --- BASE DE DATOS LOCAL (RÉPLICA Y PREASIGNADOS) ---
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS config_preasignados (
            id INTEGER PRIMARY KEY,
            pre1_monto REAL DEFAULT 0.0,
            pre1_estado TEXT DEFAULT 'Pendiente',
            pre2_monto REAL DEFAULT 0.0,
            pre2_estado TEXT DEFAULT 'Pendiente',
            pre3_monto REAL DEFAULT 0.0,
            pre3_estado TEXT DEFAULT 'Pendiente'
        )
    ''')
    c.execute("INSERT OR IGNORE INTO config_preasignados (id) VALUES (1)")
    conn.commit()
    conn.close()

init_db()

def get_preasignados():
    conn = sqlite3.connect("produccion_viajes.db")
    c = conn.cursor()
    c.execute("SELECT pre1_monto, pre1_estado, pre2_monto, pre2_estado, pre3_monto, pre3_estado FROM config_preasignados WHERE id = 1")
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "pre1_monto": float(row[0]), "pre1_estado": str(row[1]),
            "pre2_monto": float(row[2]), "pre2_estado": str(row[3]),
            "pre3_monto": float(row[4]), "pre3_estado": str(row[5])
        }
    return {
        "pre1_monto": 0.0, "pre1_estado": "Pendiente",
        "pre2_monto": 0.0, "pre2_estado": "Pendiente",
        "pre3_monto": 0.0, "pre3_estado": "Pendiente"
    }

def update_preasignado_monto(p1, p2, p3):
    conn = sqlite3.connect("produccion_viajes.db")
    c = conn.cursor()
    c.execute("""
        UPDATE config_preasignados 
        SET pre1_monto = ?, pre1_estado = CASE WHEN ? > 0 THEN 'Pendiente' ELSE pre1_estado END,
            pre2_monto = ?, pre2_estado = CASE WHEN ? > 0 THEN 'Pendiente' ELSE pre2_estado END,
            pre3_monto = ?, pre3_estado = CASE WHEN ? > 0 THEN 'Pendiente' ELSE pre3_estado END
        WHERE id = 1
    """, (p1, p1, p2, p2, p3, p3))
    conn.commit()
    conn.close()

def update_preasignado_estado(campo_estado, nuevo_estado):
    conn = sqlite3.connect("produccion_viajes.db")
    c = conn.cursor()
    c.execute(f"UPDATE config_preasignados SET {campo_estado} = ? WHERE id = 1", (nuevo_estado,))
    conn.commit()
    conn.close()

# --- HORA OFICIAL PERÚ (UTC-5) ---
def obtener_ahora_peru():
    return datetime.utcnow() - timedelta(hours=5)

# --- PROCESAMIENTO CON IA ---
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
        "dia": 23,
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

    modelos = ["gemini-3.8-flash"]
    ultimo_error = None

    for m in modelos:
        for _ in range(2):
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

    raise Exception(f"Servidor temporalmente ocupado: {ultimo_error}")

def calcular_fecha_operativa(dt, tipo_turno, hora_corte):
    if tipo_turno == "Nocturno" and dt.hour < hora_corte:
        return (dt - timedelta(days=1)).date()
    return dt.date()

def obtener_semana_operativa(fecha_op):
    return f"{fecha_op.isocalendar()[0]}-W{fecha_op.isocalendar()[1]:02d}"

# --- CARGA GENERAL DE VIAJES (NUBE CON FALLBACK LOCAL) ---
def cargar_viajes():
    df_remoto = obtener_viajes_remotos()
    if not df_remoto.empty and "id_servicio" in df_remoto.columns:
        return df_remoto
    conn = sqlite3.connect("produccion_viajes.db")
    df_local = pd.read_sql_query("SELECT * FROM viajes", conn)
    conn.close()
    return df_local

# --- ENCABEZADO ---
st.markdown('<div class="titulo-app">🚖 Copiloto de Metas y Guardia (Google Sheets ☁️)</div>', unsafe_allow_html=True)

st.sidebar.header("⚙️ Ajustes")
tipo_turno = st.sidebar.selectbox("Turno", ["Nocturno", "Diurno"])
hora_corte = st.sidebar.number_input("Corte Salida", min_value=1, max_value=12, value=5 if tipo_turno == "Nocturno" else 0)
regimen = st.sidebar.selectbox("Régimen", [5, 6], format_func=lambda x: f"{x}x{7-x}")

tab1, tab2, tab3, tab4 = st.tabs(["📊 Guardia & Metas", "📸 Registrar", "💰 Abonos", "📋 Historial"])

ahora_pe = obtener_ahora_peru()

if "datos_autocompletados" not in st.session_state:
    st.session_state["datos_autocompletados"] = {
        "id_servicio": "",
        "fecha": ahora_pe.date(),
        "hora": ahora_pe.time(),
        "tarifa_base": 0.0,
        "recargos": 0.0,
        "reembolsos": 0.0,
        "tipo_usuario": "Corporativo",
        "tipo_asignacion": "Directo",
        "metodo_pago": "Empresa"
    }

# --- TAB 1: GUARDIA & METAS ---
with tab1:
    df = cargar_viajes()
    pre_data = get_preasignados()

    if not df.empty and "semana_operativa" in df.columns:
        semanas_disp = sorted([str(s) for s in df["semana_operativa"].dropna().unique()], reverse=True)
        semana_sel = st.selectbox("Semana Operativa", semanas_disp)
        df_sem = df[df["semana_operativa"] == semana_sel].copy()

        df_sem["produccion_neta"] = pd.to_numeric(df_sem["produccion_neta"], errors="coerce").fillna(0.0)

        resumen_diario = df_sem.groupby("fecha_operativa").agg(
            viajes=('id_servicio', 'count'),
            produccion=('produccion_neta', 'sum')
        ).reset_index()

        ultimo_dia = resumen_diario["fecha_operativa"].max()
        prod_hoy = float(resumen_diario[resumen_diario["fecha_operativa"] == ultimo_dia]["produccion"].values[0])
        prod_acumulada_db = float(df_sem["produccion_neta"].sum())
        dias_trabajados = len(resumen_diario)
        
        meta_semanal_100 = regimen * 450.0
        
        preasig_activos = sum([
            pre_data[f"{p}_monto"] for p in ["pre1", "pre2", "pre3"] 
            if pre_data[f"{p}_estado"] == "Pendiente"
        ])

        total_guardia_proy = prod_hoy + preasig_activos
        acumulado_con_proyeccion = prod_acumulada_db + preasig_activos
        falta_semanal_proyectada = max(0.0, meta_semanal_100 - acumulado_con_proyeccion)

        dias_cerrados = resumen_diario[resumen_diario["fecha_operativa"] != ultimo_dia]
        alguna_falla_piso_cerrada = (dias_cerrados["produccion"] < 300.0).any() if not dias_cerrados.empty else False

        falta_piso_hoy = max(0.0, 300.0 - total_guardia_proy)
        piso_hoy_ok = (falta_piso_hoy == 0)

        falta_meta_hoy = max(0.0, 450.0 - total_guardia_proy)
        superavit_hoy = max(0.0, total_guardia_proy - 450.0)

        ritmo_esperado_acumulado = dias_trabajados * 450.0
        superavit_acumulado_global = acumulado_con_proyeccion - ritmo_esperado_acumulado

        dias_futuros = max(0, regimen - dias_trabajados)

        # Blindaje de piso: la meta restante nunca baja de 300
        if dias_futuros > 0:
            promedio_matematico = falta_semanal_proyectada / dias_futuros
            meta_real_proximos_dias = max(300.0, promedio_matematico)
        else:
            meta_real_proximos_dias = 0.0

        hora_pe = ahora_pe.hour

        if alguna_falla_piso_cerrada:
            clase_card = "hero-card-rojo"
            badge_estado = "🔴 BONO ANULADO (Falla en Piso Diario)"
            alerta_msg = "🚫 Un día anterior cerró con menos de S/ 300. La empresa anuló el bono de S/ 100 por incumplimiento de piso diario."
        elif not piso_hoy_ok:
            clase_card = "hero-card-ambar"
            badge_estado = f"⚠️ FALTA PISO (S/ {falta_piso_hoy:.2f})"
            if hora_pe >= 1 and hora_pe < 5:
                alerta_msg = f"🚨 URGENTE: Pasó la 1:00 AM y faltan S/ {falta_piso_hoy:.2f} para los S/ 300 obligatorios de hoy. ¡No cierres sin asegurar el piso!"
            else:
                alerta_msg = f"⚠️ Meta obligatoria inmediata: asegurar el piso de S/ 300. Te faltan S/ {falta_piso_hoy:.2f} antes de cerrar guardia."
        else:
            if falta_semanal_proyectada == 0:
                clase_card = "hero-card-verde"
                badge_estado = "🏆 VOLUMEN SEMANAL LOGRADO"
                if dias_futuros > 0:
                    alerta_msg = f"🏆 ¡Superaste los S/ {meta_semanal_100:.0f}! En tus {dias_futuros} día(s) restantes tu única meta obligatoria será cumplir el piso de S/ 300.00 por guardia."
                else:
                    alerta_msg = "🏆 ¡Misión semanal completada! Volumen y pisos asegurados. Bono de S/ 100 asegurado."
            elif falta_semanal_proyectada < 300.0 and dias_futuros > 0:
                clase_card = "hero-card-verde"
                badge_estado = "🟢 META CASI CERRADA"
                alerta_msg = (
                    f"🔥 Para la meta semanal solo faltan S/ {falta_semanal_proyectada:.2f}, pero tu meta OBLIGATORIA para los siguientes "
                    f"{dias_futuros} día(s) será cumplir el piso de S/ 300.00 por guardia para no anular el bono."
                )
            elif superavit_acumulado_global >= 0:
                clase_card = "hero-card-verde"
                badge_estado = "🟢 RITMO GANADOR"
                if dias_futuros > 0:
                    alerta_msg = (
                        f"🔥 ¡Superávit global de +S/ {superavit_acumulado_global:.2f}! Tu piso de hoy está asegurado. "
                        f"Tu meta requerida para tus siguientes {dias_futuros} día(s) es de S/ {meta_real_proximos_dias:.2f}/día. Si pasó la 1:00 AM, evalúa descansar."
                    )
                else:
                    alerta_msg = "🏆 ¡Semana asegurada! Bono listo."
            else:
                clase_card = "hero-card-verde" if preasig_activos > 0 else "hero-card-ambar"
                badge_estado = "🟢 PISO OK | DEFENDIENDO PROMEDIO"
                if hora_pe >= 1 and hora_pe < 5:
                    alerta_msg = (
                        f"🌙 Piso asegurado (proyectas S/ {total_guardia_proy:.2f}). Pasó la 1:00 AM y el flujo en aeropuerto es bajo. "
                        f"Si guardas ahora, tu meta para los siguientes {dias_futuros} día(s) queda en S/ {meta_real_proximos_dias:.2f}/día."
                    )
                else:
                    alerta_msg = (
                        f"💡 Piso asegurado (proyectas S/ {total_guardia_proy:.2f}). Te faltan S/ {falta_meta_hoy:.2f} para los S/ 450 de hoy. "
                        f"Aprovecha el flujo antes de la 1:00 AM. Tu meta para los siguientes {dias_futuros} día(s) es de S/ {meta_real_proximos_dias:.2f}/día."
                    )

        pct_semana = min(100.0, (acumulado_con_proyeccion / meta_semanal_100) * 100) if meta_semanal_100 > 0 else 0

        # RENDER HERO CARD
        st.markdown(f"""
        <div class="hero-card {clase_card}">
            <div class="hero-title">🚖 GUARDIA EN CURSO &nbsp;|&nbsp; {badge_estado}</div>
            <div class="hero-meta">S/ {acumulado_con_proyeccion:.2f} <span style="font-size:0.9rem; font-weight:400;">de S/ {meta_semanal_100:.0f} (Bono 100)</span></div>
            <div class="hero-sub">Pendiente semanal: <strong>S/ {falta_semanal_proyectada:.2f}</strong> ({pct_semana:.1f}% avance con preasignados)</div>
            <hr style="margin: 6px 0; border-color: rgba(255,255,255,0.2);">
            <div style="font-size:0.88rem; margin-top:4px;">
                <strong>Hoy Hecho:</strong> S/ {prod_hoy:.2f} &nbsp;|&nbsp; <strong>Preasig:</strong> S/ {preasig_activos:.2f} &nbsp;|&nbsp; <strong>Total Proyectado:</strong> S/ {total_guardia_proy:.2f}<br>
                <strong>Piso Hoy:</strong> {"✅ ASEGURADO" if piso_hoy_ok else f"<span style='color:#fca5a5;'>Faltan S/ {falta_piso_hoy:.2f}</span>"} &nbsp;|&nbsp;
                <strong>Meta Restante:</strong> S/ {meta_real_proximos_dias:.2f}/día
            </div>
            <div class="hero-alert">{alerta_msg}</div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("💼 Preasignados de la Guardia (Pre 1, 2, 3)", expanded=True):
            with st.form("form_preasignados"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    val_p1 = st.number_input("Pre 1 (S/)", min_value=0.0, value=float(pre_data["pre1_monto"]), step=5.0)
                with c2:
                    val_p2 = st.number_input("Pre 2 (S/)", min_value=0.0, value=float(pre_data["pre2_monto"]), step=5.0)
                with c3:
                    val_p3 = st.number_input("Pre 3 (S/)", min_value=0.0, value=float(pre_data["pre3_monto"]), step=5.0)
                
                if st.form_submit_button("💾 Fijar Preasignados", use_container_width=True):
                    update_preasignado_monto(val_p1, val_p2, val_p3)
                    st.rerun()

            st.caption("Gestionar Estado (al completarse o caerse):")
            for pid, label in [("pre1", "Pre 1"), ("pre2", "Pre 2"), ("pre3", "Pre 3")]:
                col_a, col_b, col_c = st.columns([1.5, 2, 2])
                with col_a:
                    st.write(f"**{label}** (S/ {pre_data[f'{pid}_monto']:.2f})")
                with col_b:
                    st.caption(f"Estado: {pre_data[f'{pid}_estado']}")
                with col_c:
                    if pre_data[f"{pid}_estado"] == "Pendiente":
                        c_b1, c_b2 = st.columns(2)
                        with c_b1:
                            if st.button("✅", key=f"ok_{pid}"):
                                update_preasignado_estado(f"{pid}_estado", "Hecho")
                                st.rerun()
                        with c_b2:
                            if st.button("❌", key=f"no_{pid}"):
                                update_preasignado_estado(f"{pid}_estado", "Caído")
                                st.rerun()
                    else:
                        if st.button("Reactivar", key=f"rst_{pid}"):
                            update_preasignado_estado(f"{pid}_estado", "Pendiente")
                            st.rerun()

        st.markdown("#### 📋 Detalle de la Semana")
        resumen_mostrar = resumen_diario.copy()
        resumen_mostrar.index = [f"Día {i+1}" for i in range(len(resumen_mostrar))]
        resumen_mostrar["Piso S/ 300"] = resumen_mostrar["produccion"].apply(lambda x: "✅ OK" if x >= 300.0 else "❌ Falla")
        st.dataframe(resumen_mostrar, use_container_width=True)
    else:
        st.info("Sin registros en Google Sheets ni localmente. Restaura tu archivo de respaldo en la pestaña '📋 Historial' para sincronizar la nube.")

# --- TAB 2: REGISTRAR VIAJE Y JORNADAS ESPECIALES ---
with tab2:
    st.markdown("#### 🌟 Jornadas Especiales (Meta Cumplida S/ 450)")
    c_esp1, c_esp2 = st.columns(2)
    with c_esp1:
        if st.button("🎓 Jornada Instructor / Guía", use_container_width=True):
            f_hoy = ahora_pe.date()
            sem_op = obtener_semana_operativa(f_hoy)
            id_inst = f"INST-{f_hoy.strftime('%Y%m%d')}"
            dict_inst = {
                "id_servicio": id_inst,
                "fecha_hora_viaje": ahora_pe.isoformat(),
                "fecha_operativa": f_hoy.isoformat(),
                "semana_operativa": sem_op,
                "tipo_asignacion": "Instructor / Guía",
                "tipo_usuario": "Empresa",
                "categoria_servicio": "Especial",
                "metodo_pago": "Empresa",
                "origen": "Sede",
                "destino": "Ruta Guía",
                "distancia_km": 0.0,
                "duracion_min": 0.0,
                "tarifa_base": 450.0,
                "monto_recargos": 0.0,
                "monto_reembolsos": 0.0,
                "produccion_neta": 450.0,
                "monto_total": 450.0,
                "abono_enviado": 0,
                "fecha_hora_abono": ""
            }
            guardar_viaje_remoto(dict_inst)
            st.success(f"✅ Jornada de Instructor guardada en Google Sheets (S/ 450.00).")
            st.rerun()

    with c_esp2:
        if st.button("🏨 Guardia en CDS (Hotel Costa del Sol)", use_container_width=True):
            f_hoy = ahora_pe.date()
            sem_op = obtener_semana_operativa(f_hoy)
            id_cds = f"CDS-{f_hoy.strftime('%Y%m%d')}"
            dict_cds = {
                "id_servicio": id_cds,
                "fecha_hora_viaje": ahora_pe.isoformat(),
                "fecha_operativa": f_hoy.isoformat(),
                "semana_operativa": sem_op,
                "tipo_asignacion": "CDS - Costa del Sol",
                "tipo_usuario": "Empresa",
                "categoria_servicio": "Especial",
                "metodo_pago": "Empresa",
                "origen": "Hotel Costa del Sol",
                "destino": "Aeropuerto / Sede",
                "distancia_km": 0.0,
                "duracion_min": 0.0,
                "tarifa_base": 450.0,
                "monto_recargos": 0.0,
                "monto_reembolsos": 0.0,
                "produccion_neta": 450.0,
                "monto_total": 450.0,
                "abono_enviado": 0,
                "fecha_hora_abono": ""
            }
            guardar_viaje_remoto(dict_cds)
            st.success(f"✅ Guardia en CDS guardada en Google Sheets (S/ 450.00).")
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
                        f_dt = date(int(datos.get("anio", 2026)), int(datos.get("mes", 10)), int(datos.get("dia", 23)))
                        h_dt = time(int(datos.get("hora", 0)), int(datos.get("minuto", 0)))
                    except Exception:
                        f_dt = ahora_pe.date()
                        h_dt = ahora_pe.time()

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

                datos_guardar = {
                    "id_servicio": str(id_servicio),
                    "fecha_hora_viaje": dt_viaje.isoformat(),
                    "fecha_operativa": str(f_op),
                    "semana_operativa": sem_op,
                    "tipo_asignacion": tipo_asignacion,
                    "tipo_usuario": "Corporativo",
                    "categoria_servicio": "Lite",
                    "metodo_pago": metodo_pago,
                    "origen": "",
                    "destino": "",
                    "distancia_km": 0.0,
                    "duracion_min": 0.0,
                    "tarifa_base": tarifa_base,
                    "monto_recargos": recargos,
                    "monto_reembolsos": reembolsos,
                    "produccion_neta": produccion_neta,
                    "monto_total": monto_total,
                    "abono_enviado": 0,
                    "fecha_hora_abono": ""
                }
                
                # Guardar remoto en Google Sheets
                ok = guardar_viaje_remoto(datos_guardar)
                if ok:
                    st.success("Guardado en Google Sheets con éxito.")
                else:
                    st.warning("Guardado local (sin conexión inmediata con Sheets).")
                st.rerun()

# --- TAB 3: RENDICIÓN EFECTIVO / YAPE ---
with tab3:
    st.markdown("#### 💰 Rendición Efectivo / Yape")
    df_actual = cargar_viajes()

    if not df_actual.empty and "metodo_pago" in df_actual.columns:
        df_liq = df_actual[df_actual["metodo_pago"].isin(["Efectivo", "Yape"])].copy()
        df_liq["monto_total"] = pd.to_numeric(df_liq["monto_total"], errors="coerce").fillna(0.0)
        df_liq["abono_enviado"] = pd.to_numeric(df_liq["abono_enviado"], errors="coerce").fillna(0).astype(int)

        if not df_liq.empty:
            pendientes = df_liq[df_liq["abono_enviado"] == 0]
            st.markdown(f"**Pendiente por rendir:** S/ {pendientes['monto_total'].sum():.2f} ({len(pendientes)} viajes)")
            st.divider()

            for _, row in df_liq.iterrows():
                id_serv = str(row["id_servicio"])
                formato_envio = f"{id_serv} - 2986"
                esta_enviado = bool(row["abono_enviado"])

                c1, c2 = st.columns([3, 2])
                with c1:
                    st.code(formato_envio, language=None)
                    st.caption(f"**S/ {row['monto_total']:.2f}** ({row['metodo_pago']})")
                    if esta_enviado:
                        st.success(f"Enviado: {row.get('fecha_hora_abono', '')}")
                with c2:
                    if not esta_enviado:
                        if st.button("Marcar Enviado", key=f"env_{id_serv}"):
                            ahora_str = ahora_pe.strftime("%d/%m/%y %H:%M")
                            actualizar_abono_remoto(id_serv, 1, ahora_str)
                            st.rerun()
                    else:
                        if st.button("Desmarcar", key=f"des_{id_serv}"):
                            actualizar_abono_remoto(id_serv, 0, "")
                            st.rerun()
        else:
            st.info("No hay viajes en Efectivo o Yape.")
    else:
        st.info("No hay viajes registrados.")

# --- TAB 4: HISTORIAL Y SINCRONIZACIÓN ---
with tab4:
    st.markdown("#### 🔄 Sincronización y Respaldo")
    
    with st.expander("📤 Migrar / Restaurar datos a Google Sheets desde CSV", expanded=False):
        csv_subido = st.file_uploader("Selecciona tu archivo .csv guardado", type=["csv"], key="uploader_backup")
        if csv_subido is not None:
            if st.button("⚡ Subir y Sincronizar en Google Sheets", use_container_width=True):
                try:
                    df_res = pd.read_csv(csv_subido)
                    
                    if "fecha_operativa" in df_res.columns and "produccion" in df_res.columns:
                        for _, r in df_res.iterrows():
                            f_op_str = str(r["fecha_operativa"])
                            f_op_dt = datetime.strptime(f_op_str, "%Y-%m-%d").date()
                            sem_op_str = obtener_semana_operativa(f_op_dt)
                            prod_val = float(r["produccion"])
                            id_rest = f"REC-{f_op_str.replace('-', '')}"
                            dict_item = {
                                "id_servicio": id_rest,
                                "fecha_hora_viaje": f"{f_op_str}T20:00:00",
                                "fecha_operativa": f_op_str,
                                "semana_operativa": sem_op_str,
                                "tipo_asignacion": "Directo",
                                "tipo_usuario": "Empresa",
                                "categoria_servicio": "Resumen",
                                "metodo_pago": "Empresa",
                                "origen": "Ruta",
                                "destino": "Destino",
                                "distancia_km": 0.0,
                                "duracion_min": 0.0,
                                "tarifa_base": prod_val,
                                "monto_recargos": 0.0,
                                "monto_reembolsos": 0.0,
                                "produccion_neta": prod_val,
                                "monto_total": prod_val,
                                "abono_enviado": 0,
                                "fecha_hora_abono": ""
                            }
                            guardar_viaje_remoto(dict_item)
                    elif "id_servicio" in df_res.columns:
                        for _, r in df_res.iterrows():
                            dict_item = r.to_dict()
                            guardar_viaje_remoto(dict_item)
                    st.success("¡Datos sincronizados directamente en tu Google Sheets!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al sincronizar: {e}")

    df_all = cargar_viajes()

    if not df_all.empty and "id_servicio" in df_all.columns:
        csv_data = df_all.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Descargar Copia Local de Seguridad (CSV)",
            data=csv_data,
            file_name=f"respaldo_viajes_{ahora_pe.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )

        st.markdown("#### 📋 Historial en Google Sheets")
        semanas_lista = sorted([str(s) for s in df_all["semana_operativa"].dropna().unique().tolist()], reverse=True)
        semana_actual = semanas_lista[0] if semanas_lista else ""
        
        opciones_filtro = [f"Esta semana ({semana_actual})"] if semana_actual else []
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

        st.caption(f"Mostrando {len(df_mostrar)} registros sincronizados.")
        st.dataframe(df_mostrar, use_container_width=True, hide_index=True)

        st.divider()
        lista_ids = [str(x) for x in df_mostrar["id_servicio"].tolist()]
        id_borrar = st.selectbox("Eliminar servicio de la nube:", lista_ids)
        if st.button("❌ Confirmar Eliminación"):
            eliminar_viaje_remoto(id_borrar)
            st.success("Registro eliminado de Google Sheets.")
            st.rerun()
    else:
        st.info("No hay registros en la nube.")
