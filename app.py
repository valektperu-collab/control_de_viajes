import streamlit as st
import pandas as pd
import requests
import json
import os
from datetime import datetime, date, timedelta
import pytz
from PIL import Image
import google.generativeai as genai

# ==============================================================================
# CONFIGURACIÓN GENERAL Y ESTILOS MÓVILES
# ==============================================================================
st.set_page_config(
    page_title="Copiloto Táctico de Viajes",
    page_icon="🚕",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Zona horaria oficial Lima (UTC-5)
TZ_LIMA = pytz.timezone("America/Lima")

def obtener_ahora():
    return datetime.now(TZ_LIMA)

# ==============================================================================
# SECRETOS Y CONSTANTES
# ==============================================================================
# Configurar API Key de Gemini
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# URL del Webhook de Google Apps Script
GSHEETS_URL = st.secrets.get(
    "GSHEETS_URL", 
    os.environ.get("GSHEETS_URL", "https://script.google.com/macros/s/AKfycbx_TU_SCRIPT_ID/exec")
)

LOCAL_CACHE_FILE = "viajes_cache.json"

META_SEMANAL = 2250.00
BONO_MONTO = 100.00
PISO_DIARIO = 300.00
HORA_CORTE_NOCTURNO = 5  # 05:00 a. m.

# ==============================================================================
# FUNCIONES DE FECHAS Y PERSISTENCIA (OFFLINE FIRST)
# ==============================================================================
def calcular_fecha_operativa(dt_obj):
    """Regla de corte nocturno: antes de las 5 a. m. pertenece a la guardia previa."""
    if dt_obj.hour < HORA_CORTE_NOCTURNO:
        return (dt_obj - timedelta(days=1)).date()
    return dt_obj.date()

def calcular_semana_operativa(fecha_op):
    cal = fecha_op.isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"

def cargar_cache_local():
    if os.path.exists(LOCAL_CACHE_FILE):
        try:
            with open(LOCAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def guardar_cache_local(lista_viajes):
    try:
        with open(LOCAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(lista_viajes, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def sincronizar_desde_nube():
    """Descarga datos de Google Sheets; si hay demora o timeout, usa el respaldo local."""
    try:
        r = requests.get(GSHEETS_URL, timeout=4)
        if r.status_code == 200:
            datos = r.json()
            if isinstance(datos, list) and len(datos) > 0:
                guardar_cache_local(datos)
                return datos
    except Exception:
        pass
    return cargar_cache_local()

# Inicialización de variables de sesión
if "viajes" not in st.session_state:
    st.session_state.viajes = sincronizar_desde_nube()

if "preasignados" not in st.session_state:
    st.session_state.preasignados = {"pre1": 0.0, "pre2": 0.0, "pre3": 0.0}

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "form_datos" not in st.session_state:
    st.session_state.form_datos = {
        "num_servicio": "",
        "fecha": obtener_ahora().date(),
        "hora": obtener_ahora().strftime("%H:%M"),
        "tarifa_base": 0.0,
        "recargos": 0.0,
        "peajes": 0.0,
        "asignacion": "Kusi",
        "categoria": "XL",
        "pago": "Empresa",
        "origen": "",
        "destino": "",
        "comentarios": ""
    }

def guardar_viaje_seguro(registro):
    """Guarda al instante en local (0.1 seg) y sincroniza con Google en background."""
    id_nuevo = str(registro["id_servicio"]).strip()
    viajes_filtrados = [v for v in st.session_state.viajes if str(v.get("id_servicio")).strip() != id_nuevo]
    viajes_filtrados.append(registro)
    st.session_state.viajes = viajes_filtrados
    
    # Respaldo inmediato en archivo JSON
    guardar_cache_local(viajes_filtrados)
    
    # Sincronización con Google Apps Script sin bloquear el hilo principal
    sincronizado = False
    try:
        if GSHEETS_URL and "AKfycbx_TU_SCRIPT_ID" not in GSHEETS_URL:
            res = requests.post(GSHEETS_URL, json=registro, timeout=3)
            if res.status_code == 200:
                sincronizado = True
    except Exception:
        sincronizado = False
        
    return sincronizado

def eliminar_viaje_seguro(id_servicio):
    id_elim = str(id_servicio).strip()
    st.session_state.viajes = [v for v in st.session_state.viajes if str(v.get("id_servicio")).strip() != id_elim]
    guardar_cache_local(st.session_state.viajes)
    try:
        if GSHEETS_URL and "AKfycbx_TU_SCRIPT_ID" not in GSHEETS_URL:
            requests.post(GSHEETS_URL, json={"action": "delete", "id_servicio": id_elim}, timeout=3)
    except Exception:
        pass

# ==============================================================================
# EXTRACCIÓN OCR CON INTELIGENCIA ARTIFICIAL (GEMINI VISION)
# ==============================================================================
def extraer_datos_con_ia(imagen_file):
    if not GEMINI_API_KEY:
        st.error("⚠️ Configura GEMINI_API_KEY en Secrets para habilitar la extracción por IA.")
        return None
    
    try:
        img = Image.open(imagen_file)
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = """
        Eres un asistente experto analizando tickets y capturas de viajes de taxi corporativo y aplicativos.
        Extrae los siguientes datos con máxima precisión y responde estrictamente en formato JSON válido:
        {
          "num_servicio": "código o ID numérico del servicio o ticket (ej: 5938086 o 562055)",
          "fecha": "YYYY-MM-DD (fecha del viaje; si no tiene año asume 2026)",
          "hora": "HH:MM (formato 24 horas)",
          "tarifa_base": 0.0,
          "recargos": 0.0 (sumar recargos como horario especial, cargo reserva, etc.),
          "peajes": 0.0 (monto de peajes si los hubiere),
          "asignacion": "Kusi / Directo / Reserva / Inmediato",
          "categoria": "XL / Van / Lite / Sedán",
          "metodo_pago": "Empresa / Efectivo / Tarjeta / Yape / Plin",
          "origen": "punto de partida si figura",
          "destino": "punto de destino si figura",
          "comentarios": "cualquier detalle adicional relevante"
        }
        Solo devuelve el JSON sin bloques de formato Markdown ni texto extra.
        """
        response = model.generate_content([prompt, img])
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(texto_limpio)
    except Exception as e:
        st.error(f"Error procesando la imagen con IA: {e}")
        return None

# ==============================================================================
# CÁLCULOS TÁCTICOS Y METAS
# ==============================================================================
ahora = obtener_ahora()
fecha_op_hoy = calcular_fecha_operativa(ahora)
semana_op_hoy = calcular_semana_operativa(fecha_op_hoy)

df_todos = pd.DataFrame(st.session_state.viajes) if st.session_state.viajes else pd.DataFrame()

if not df_todos.empty:
    for col in ["produccion_neta", "tarifa_base", "monto_recargos", "monto_reembolsos", "monto_total"]:
        if col in df_todos.columns:
            df_todos[col] = pd.to_numeric(df_todos[col], errors="coerce").fillna(0.0)
    if "fecha_operativa" not in df_todos.columns:
        df_todos["fecha_operativa"] = str(fecha_op_hoy)
    if "semana_operativa" not in df_todos.columns:
        df_todos["semana_operativa"] = str(semana_op_hoy)
        
    df_semana = df_todos[df_todos["semana_operativa"] == semana_op_hoy]
    df_hoy = df_todos[df_todos["fecha_operativa"] == str(fecha_op_hoy)]
else:
    df_semana = pd.DataFrame()
    df_hoy = pd.DataFrame()

acumulado_semana = df_semana["produccion_neta"].sum() if not df_semana.empty else 0.0
total_hoy_hecho = df_hoy["produccion_neta"].sum() if not df_hoy.empty else 0.0

suma_preasig = sum(st.session_state.preasignados.values())
acumulado_con_preasig = acumulado_semana + suma_preasig
faltante_semana = max(0.0, META_SEMANAL - acumulado_con_preasig)

# Regla de piso eliminatorio
piso_asegurado = total_hoy_hecho >= PISO_DIARIO
faltante_piso_hoy = max(0.0, PISO_DIARIO - total_hoy_hecho)

# ==============================================================================
# HERO CARD - COPILOTO TÁCTICO
# ==============================================================================
st.markdown("### 🚖 Copiloto Táctico de Guardia")

if piso_asegurado:
    badge_piso = "✅ PISO ASEGURADO"
else:
    badge_piso = f"🚨 PISO EN RIESGO: Falta S/ {faltante_piso_hoy:.2f}"

if acumulado_con_preasig >= META_SEMANAL:
    color_card = "#1b4d3e"
    estado_meta = f"🟢 META SEMANAL SUPERADA (+S/ {acumulado_con_preasig - META_SEMANAL:.2f}) | Bono S/ {BONO_MONTO:.0f} Ganado"
else:
    color_card = "#162b32"
    estado_meta = f"🟡 EN RUTA AL BONO | Falta S/ {faltante_semana:.2f} para los S/ {META_SEMANAL:.0f}"

st.markdown(
    f"""
    <div style="background-color: {color_card}; padding: 16px; border-radius: 12px; border: 1px solid #325a66; margin-bottom: 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <span style="font-weight: 700; font-size: 13px; color: #a4e5d9;">Semana {semana_op_hoy}</span>
            <span style="background: rgba(255,255,255,0.18); padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 700; color: #ffffff;">{badge_piso}</span>
        </div>
        <div style="font-size: 28px; font-weight: 800; color: #ffffff; margin-bottom: 4px;">
            S/ {acumulado_con_preasig:.2f} <span style="font-size: 15px; font-weight: 500; color: #cadbd8;">/ S/ {META_SEMANAL:.2f}</span>
        </div>
        <div style="font-size: 13px; color: #b0d4cc; margin-bottom: 10px;">
            {estado_meta}
        </div>
        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.15); margin: 8px 0;">
        <div style="display: flex; justify-content: space-between; font-size: 13px; color: #ffffff;">
            <span><b>Hoy Hecho:</b> S/ {total_hoy_hecho:.2f}</span>
            <span><b>Preasig:</b> S/ {suma_preasig:.2f}</span>
            <span><b>Hoy Proyectado:</b> S/ {total_hoy_hecho + suma_preasig:.2f}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# PESTAÑAS PRINCIPALES
# ==============================================================================
tab_registro, tab_guardia, tab_historial = st.tabs(["📝 Registrar Viaje", "📊 Guardia & Metas", "📋 Historial"])

# ------------------------------------------------------------------------------
# TAB 1: REGISTRAR VIAJE
# ------------------------------------------------------------------------------
with tab_registro:
    st.markdown("#### Captura o Ticket del Viaje")
    archivo_foto = st.file_uploader(
        "Sube o toma una foto del ticket/servicio:", 
        type=["png", "jpg", "jpeg"],
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    if archivo_foto is not None:
        if st.button("⚡ Extraer Rápido con IA", use_container_width=True):
            with st.spinner("Leyendo datos del ticket con IA..."):
                datos_ia = extraer_datos_con_ia(archivo_foto)
                if datos_ia:
                    st.session_state.form_datos["num_servicio"] = str(datos_ia.get("num_servicio", "")).strip()
                    try:
                        st.session_state.form_datos["fecha"] = datetime.strptime(datos_ia.get("fecha"), "%Y-%m-%d").date()
                    except Exception:
                        pass
                    st.session_state.form_datos["hora"] = str(datos_ia.get("hora", ahora.strftime("%H:%M")))
                    st.session_state.form_datos["tarifa_base"] = float(datos_ia.get("tarifa_base", 0.0))
                    st.session_state.form_datos["recargos"] = float(datos_ia.get("recargos", 0.0))
                    st.session_state.form_datos["peajes"] = float(datos_ia.get("peajes", 0.0))
                    st.session_state.form_datos["origen"] = str(datos_ia.get("origen", ""))
                    st.session_state.form_datos["destino"] = str(datos_ia.get("destino", ""))
                    st.session_state.form_datos["comentarios"] = str(datos_ia.get("comentarios", ""))
                    
                    # Normalizar selección de combos
                    asig_val = str(datos_ia.get("asignacion", "Kusi")).capitalize()
                    st.session_state.form_datos["asignacion"] = asig_val if asig_val in ["Kusi", "Directo", "Reserva", "Inmediato"] else "Directo"
                    
                    pago_val = str(datos_ia.get("metodo_pago", "Empresa"))
                    st.session_state.form_datos["pago"] = pago_val if pago_val in ["Empresa", "Tarjeta / POS", "Efectivo", "Yape", "Plin"] else "Empresa"
                    
                    st.success("¡Datos listos! Revisa abajo y presiona Guardar.")

    st.divider()
    
    # Formulario de entrada
    col1, col2 = st.columns(2)
    with col1:
        inp_num = st.text_input("N° Servicio / Ticket Pasajero *", value=st.session_state.form_datos["num_servicio"])
        inp_fecha = st.date_input("Fecha", value=st.session_state.form_datos["fecha"])
        inp_hora = st.text_input("Hora (HH:MM)", value=st.session_state.form_datos["hora"])
        inp_base = st.number_input("Tarifa Base (S/) *", min_value=0.0, value=float(st.session_state.form_datos["tarifa_base"]), step=5.0, format="%.2f")
        inp_rec = st.number_input("Recargos (Horario / Reserva) (S/)", min_value=0.0, value=float(st.session_state.form_datos["recargos"]), step=1.0, format="%.2f")
    
    with col2:
        inp_peajes = st.number_input("Peajes / Reembolsos (S/)", min_value=0.0, value=float(st.session_state.form_datos["peajes"]), step=1.0, format="%.2f")
        
        lista_asig = ["Kusi", "Directo", "Reserva", "Inmediato"]
        idx_asig = lista_asig.index(st.session_state.form_datos["asignacion"]) if st.session_state.form_datos["asignacion"] in lista_asig else 0
        inp_asig = st.selectbox("Tipo de Asignación", lista_asig, index=idx_asig)
        
        lista_cat = ["XL", "Van", "Lite", "Sedán"]
        idx_cat = lista_cat.index(st.session_state.form_datos["categoria"]) if st.session_state.form_datos["categoria"] in lista_cat else 0
        inp_cat = st.selectbox("Categoría", lista_cat, index=idx_cat)
        
        lista_pago = ["Empresa", "Tarjeta / POS", "Efectivo", "Yape", "Plin"]
        idx_pago = lista_pago.index(st.session_state.form_datos["pago"]) if st.session_state.form_datos["pago"] in lista_pago else 0
        inp_pago = st.selectbox("Método de Pago", lista_pago, index=idx_pago)
        
        inp_origen = st.text_input("Origen", value=st.session_state.form_datos["origen"])
        inp_dest = st.text_input("Destino", value=st.session_state.form_datos["destino"])

    inp_coment = st.text_input("Comentarios / Referencia", value=st.session_state.form_datos["comentarios"])

    calc_neta = float(inp_base + inp_rec)
    calc_total = float(calc_neta + inp_peajes)

    st.markdown(
        f"""
        <div style="background: rgba(255,255,255,0.06); padding: 10px 14px; border-radius: 8px; margin: 12px 0;">
            <b>Producción Neta (Meta):</b> <span style="color:#56f5a3; font-size:16px; font-weight:700;">S/ {calc_neta:.2f}</span> | 
            <b>Total a Rendir:</b> S/ {calc_total:.2f}
        </div>
        """,
        unsafe_allow_html=True
    )

    # BOTÓN DE GUARDADO ANTIBLOQUEOS
    if st.button("💾 Guardar Viaje", type="primary", use_container_width=True):
        if not str(inp_num).strip():
            st.error("⚠️ Debes ingresar el N° de Servicio o Ticket.")
        elif inp_base <= 0:
            st.error("⚠️ La Tarifa Base debe ser mayor a S/ 0.00.")
        else:
            try:
                hora_partes = str(inp_hora).strip().split(":")
                hh = int(hora_partes[0])
                mm = int(hora_partes[1])
            except Exception:
                hh, mm = ahora.hour, ahora.minute

            dt_viaje = datetime.combine(inp_fecha, datetime.min.time()).replace(hour=hh, minute=mm)
            f_operativa = calcular_fecha_operativa(dt_viaje)
            s_operativa = calcular_semana_operativa(f_operativa)

            nuevo_registro = {
                "id_servicio": str(inp_num).strip(),
                "fecha_hora_viaje": dt_viaje.strftime("%Y-%m-%d %H:%M:00"),
                "fecha_operativa": str(f_operativa),
                "semana_operativa": str(s_operativa),
                "tipo_asignacion": str(inp_asig),
                "tipo_usuario": "Corporativo" if inp_pago == "Empresa" else "Particular",
                "categoria_servicio": str(inp_cat),
                "metodo_pago": str(inp_pago),
                "origen": str(inp_origen).strip(),
                "destino": str(inp_dest).strip(),
                "distancia_km": 0.0,
                "duracion_min": 0,
                "tarifa_base": float(inp_base),
                "monto_recargos": float(inp_rec),
                "monto_reembolsos": float(inp_peajes),
                "produccion_neta": float(calc_neta),
                "monto_total": float(calc_total),
                "abono_enviado": 1 if inp_pago in ["Efectivo", "Yape", "Plin"] else 0,
                "fecha_hora_abono": "",
                "comentarios": str(inp_coment).strip()
            }

            sincronizado_ok = guardar_viaje_seguro(nuevo_registro)

            # Resetear formulario y cargador de archivos para el siguiente servicio
            st.session_state.uploader_key += 1
            st.session_state.form_datos = {
                "num_servicio": "",
                "fecha": ahora.date(),
                "hora": ahora.strftime("%H:%M"),
                "tarifa_base": 0.0,
                "recargos": 0.0,
                "peajes": 0.0,
                "asignacion": "Kusi",
                "categoria": "XL",
                "pago": "Empresa",
                "origen": "",
                "destino": "",
                "comentarios": ""
            }

            if sincronizado_ok:
                st.success(f"✅ ¡Viaje {inp_num} guardado y sincronizado con Google Sheets!")
            else:
                st.success(f"✅ ¡Viaje {inp_num} guardado en el teléfono! (Quedó respaldado en memoria).")

            st.rerun()

# ------------------------------------------------------------------------------
# TAB 2: GUARDIA & METAS (PREASIGNADOS)
# ------------------------------------------------------------------------------
with tab_guardia:
    st.subheader("Control de Preasignados de la Guardia")
    st.caption("Anota aquí los servicios que tienes asignados antes de cobrarlos para proyectar tu piso.")

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.session_state.preasignados["pre1"] = st.number_input(
            "Preasignado 1 (S/)", 
            value=float(st.session_state.preasignados.get("pre1", 0.0)), 
            step=10.0, 
            format="%.2f"
        )
    with col_p2:
        st.session_state.preasignados["pre2"] = st.number_input(
            "Preasignado 2 (S/)", 
            value=float(st.session_state.preasignados.get("pre2", 0.0)), 
            step=10.0, 
            format="%.2f"
        )
    with col_p3:
        st.session_state.preasignados["pre3"] = st.number_input(
            "Preasignado 3 (S/)", 
            value=float(st.session_state.preasignados.get("pre3", 0.0)), 
            step=10.0, 
            format="%.2f"
        )

    st.info(f"Total Proyectado en Preasignados: **S/ {sum(st.session_state.preasignados.values()):.2f}**")

# ------------------------------------------------------------------------------
# TAB 3: HISTORIAL DE SERVICIOS
# ------------------------------------------------------------------------------
with tab_historial:
    st.subheader("Servicios Registrados")

    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        if st.button("🔄 Forzar Sincronización Nube", use_container_width=True):
            st.session_state.viajes = sincronizar_desde_nube()
            st.rerun()

    if not df_todos.empty:
        columnas_ver = ["id_servicio", "fecha_operativa", "produccion_neta", "tarifa_base", "metodo_pago", "destino", "comentarios"]
        cols_presentes = [c for c in columnas_ver if c in df_todos.columns]
        
        df_mostrar = df_todos[cols_presentes].iloc[::-1]
        st.dataframe(df_mostrar, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("#### 🗑️ Eliminar un registro erróneo")
        lista_ids = [str(x) for x in df_todos["id_servicio"].tolist() if str(x).strip()]
        id_a_borrar = st.selectbox("Selecciona ID a eliminar:", options=["-- Ninguno --"] + lista_ids[::-1])

        if id_a_borrar != "-- Ninguno --":
            if st.button(f"❌ Confirmar Eliminación de {id_a_borrar}", type="secondary"):
                eliminar_viaje_seguro(id_a_borrar)
                st.warning(f"Servicio {id_a_borrar} eliminado.")
                st.rerun()
    else:
        st.info("No hay servicios registrados en la base de datos.")
