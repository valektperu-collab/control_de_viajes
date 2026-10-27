import streamlit as st
import pandas as pd
import requests
import json
import os
import io
from datetime import datetime, date, timedelta, timezone
from PIL import Image, ImageOps

# Importación segura de IA para evitar caídas de servidor
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# ==============================================================================
# CONFIGURACIÓN GENERAL Y TEMA MÓVIL
# ==============================================================================
st.set_page_config(
    page_title="Control Táctico de Viajes",
    page_icon="🚕",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Zona horaria nativa Lima (UTC-5)
TZ_LIMA = timezone(timedelta(hours=-5))

def obtener_ahora():
    return datetime.now(TZ_LIMA)

ahora_pe = obtener_ahora()

# Clave de Gemini y Webhook de Google Apps Script
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if GEMINI_API_KEY and genai is not None:
    genai.configure(api_key=GEMINI_API_KEY)

GSHEETS_URL = "https://script.google.com/macros/s/AKfycbzbnRBVGJC3AhvoM1gbE7V1p6we8DxpfRyhQfxP0zIJ1G9xCG1YOA6TLtrOwfHh588/exec"
LOCAL_CACHE_FILE = "viajes_cache.json"

META_SEMANAL = 2250.00
BONO_MONTO = 100.00
PISO_DIARIO = 300.00
META_DIARIA_TOP = 450.00
HORA_CORTE_GUARDIA = 17  # 17:00 (5:00 p. m.): inicio formal de la nueva guardia

# ==============================================================================
# GESTIÓN DE TIEMPO Y PERSISTENCIA (OFFLINE FIRST)
# ==============================================================================
def calcular_fecha_operativa(dt_obj):
    """
    Regla de turno nocturno:
    Antes de las 17:00 (5:00 p. m.) sigue perteneciendo a la guardia iniciada la tarde anterior.
    A las 17:00 en punto arranca formalmente la nueva guardia.
    """
    if dt_obj.hour < HORA_CORTE_GUARDIA:
        return (dt_obj - timedelta(days=1)).date()
    return dt_obj.date()

def obtener_semana_operativa(fecha_op):
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

def cargar_viajes():
    """Descarga de Google Sheets; si hay demora o fallo de señal, usa el respaldo local."""
    try:
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
            r = requests.get(GSHEETS_URL, timeout=4)
            if r.status_code == 200:
                datos = r.json()
                if isinstance(datos, list) and len(datos) > 0:
                    guardar_cache_local(datos)
                    return pd.DataFrame(datos)
    except Exception:
        pass
    local = cargar_cache_local()
    return pd.DataFrame(local) if local else pd.DataFrame()

def guardar_viaje_remoto(dict_item):
    """Guarda en memoria local y sincroniza en segundo plano sin congelar la app."""
    if "viajes" not in st.session_state:
        st.session_state.viajes = []
    
    id_actual = str(dict_item.get("id_servicio", "")).strip()
    st.session_state.viajes = [v for v in st.session_state.viajes if str(v.get("id_servicio", "")).strip() != id_actual]
    st.session_state.viajes.append(dict_item)
    guardar_cache_local(st.session_state.viajes)
    
    sincronizado = False
    try:
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
            res = requests.post(GSHEETS_URL, json=dict_item, timeout=4)
            if res.status_code == 200:
                sincronizado = True
    except Exception:
        sincronizado = False
    return sincronizado

def eliminar_viaje_remoto(id_servicio):
    id_elim = str(id_servicio).strip()
    if "viajes" in st.session_state:
        st.session_state.viajes = [v for v in st.session_state.viajes if str(v.get("id_servicio", "")).strip() != id_elim]
        guardar_cache_local(st.session_state.viajes)
    try:
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
            requests.post(GSHEETS_URL, json={"action": "delete", "id_servicio": id_elim}, timeout=4)
    except Exception:
        pass

# ==============================================================================
# EXTRACCIÓN OCR CON INTELIGENCIA ARTIFICIAL (OPTIMIZADA)
# ==============================================================================
def optimizar_imagen(imagen_file):
    """Reorienta y redimensiona la captura a un tamaño ligero para subida ultra rápida."""
    img = Image.open(imagen_file)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    max_ancho = 1024
    if img.width > max_ancho:
        proporcion = max_ancho / float(img.width)
        alto_calc = int(float(img.height) * float(proporcion))
        img = img.resize((max_ancho, alto_calc), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    buffer.seek(0)
    return Image.open(buffer)

def extraer_datos_con_ia(imagen_file):
    if not GEMINI_API_KEY or genai is None:
        st.error("⚠️ Configura GEMINI_API_KEY en Secrets para habilitar la extracción por IA.")
        return None
    try:
        img_optimizada = optimizar_imagen(imagen_file)
        
        prompt = """
        Eres un asistente experto analizando tickets y capturas de viajes de taxi corporativo y aplicativos en Perú.
        Extrae los siguientes datos con máxima precisión y responde estrictamente en formato JSON válido:
        {
          "num_servicio": "código o ID numérico del servicio o ticket (ej: 5938086 o 562055)",
          "fecha": "YYYY-MM-DD",
          "hora": "HH:MM",
          "tarifa_base": 0.0,
          "recargos": 0.0,
          "peajes": 0.0,
          "asignacion": "Kusi / Directo / Reserva / Inmediato",
          "categoria": "XL / Van / Lite / Sedán",
          "metodo_pago": "Empresa / Efectivo / Tarjeta / Yape / Plin",
          "origen": "punto de partida si figura",
          "destino": "punto de destino si figura",
          "comentarios": "información relevante adicional"
        }
        Solo devuelve el JSON sin bloques Markdown adicionales ni explicaciones.
        """
        
        modelos = ["gemini-3.8-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-2.0-flash"]
        response = None
        for nombre_mod in modelos:
            try:
                model = genai.GenerativeModel(nombre_mod)
                response = model.generate_content([prompt, img_optimizada])
                if response and response.text:
                    break
            except Exception:
                continue
                
        if not response or not response.text:
            st.error("No se pudo obtener respuesta del modelo de visión.")
            return None

        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(texto_limpio)
    except Exception as e:
        st.error(f"Error procesando imagen: {e}")
        return None

# ==============================================================================
# ESTADO DE SESIÓN
# ==============================================================================
if "viajes" not in st.session_state:
    df_init = cargar_viajes()
    st.session_state.viajes = df_init.to_dict(orient="records") if not df_init.empty else []

if "preasignados" not in st.session_state:
    st.session_state.preasignados = {"pre1": 0.0, "pre2": 0.0, "pre3": 0.0}

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "form_datos" not in st.session_state:
    st.session_state.form_datos = {
        "num_servicio": "",
        "fecha": ahora_pe.date(),
        "hora": ahora_pe.strftime("%H:%M"),
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

# ==============================================================================
# CARGA DE DATOS, SELECTOR DE SEMANA Y FILTRADO DIARIO
# ==============================================================================
fecha_op_hoy = calcular_fecha_operativa(ahora_pe)
semana_op_hoy = obtener_semana_operativa(fecha_op_hoy)
fecha_hoy_str = str(fecha_op_hoy)[:10]

if "viajes" in st.session_state and st.session_state.viajes:
    df_all = pd.DataFrame(st.session_state.viajes)
else:
    df_all = cargar_viajes()

if not df_all.empty:
    for col in ["produccion_neta", "tarifa_base", "monto_recargos", "monto_reembolsos", "monto_total", "abono_enviado"]:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors="coerce").fillna(0.0)
    if "semana_operativa" not in df_all.columns:
        df_all["semana_operativa"] = semana_op_hoy
    if "fecha_operativa" not in df_all.columns:
        df_all["fecha_operativa"] = fecha_hoy_str

    semanas_disponibles = sorted([str(s) for s in df_all["semana_operativa"].dropna().unique().tolist()], reverse=True)
    if semana_op_hoy not in semanas_disponibles:
        semanas_disponibles.insert(0, semana_op_hoy)
else:
    semanas_disponibles = [semana_op_hoy]

st.markdown("#### Semana Operativa")
semana_seleccionada = st.selectbox("", semanas_disponibles, index=0, label_visibility="collapsed")

# Filtrado por semana seleccionada
if not df_all.empty and "semana_operativa" in df_all.columns:
    df_semana = df_all[df_all["semana_operativa"] == semana_seleccionada]
    acumulado_semana = float(df_semana["produccion_neta"].sum()) if not df_semana.empty else 0.0
else:
    acumulado_semana = 0.0

# Filtrado limpio de la fecha operativa actual (los 10 primeros caracteres YYYY-MM-DD)
if not df_all.empty and "fecha_operativa" in df_all.columns:
    df_hoy = df_all[df_all["fecha_operativa"].astype(str).str.slice(0, 10) == fecha_hoy_str]
    total_hoy_hecho = float(df_hoy["produccion_neta"].sum()) if not df_hoy.empty else 0.0
else:
    df_hoy = pd.DataFrame()
    total_hoy_hecho = 0.0

suma_preasig = sum(st.session_state.preasignados.values()) if "preasignados" in st.session_state else 0.0
acumulado_con_preasig = acumulado_semana + suma_preasig
faltante_semana = max(0.0, META_SEMANAL - acumulado_con_preasig)
porcentaje_avance = min(100.0, (acumulado_con_preasig / META_SEMANAL) * 100.0) if META_SEMANAL > 0 else 0.0

produccion_guardia = total_hoy_hecho + suma_preasig

# Hitos del ciclo 5x2 (1: 450, 2: 900, 3: 1350, 4: 1800, 5: 2250)
HITOS_ACUMULADOS = {
    1: 450.00,
    2: 900.00,
    3: 1350.00,
    4: 1800.00,
    5: 2250.00
}

dia_ciclo = min(5, max(1, fecha_op_hoy.isoweekday()))
hito_hoy = HITOS_ACUMULADOS.get(dia_ciclo, 2250.00)

# ==============================================================================
# HERO CARD TÁCTICA - SEMÁFORO DIARIO Y RITMO 5x2 (ÚNICA INSTANCIA)
# ==============================================================================
if (produccion_guardia >= META_DIARIA_TOP) or (acumulado_con_preasig >= hito_hoy) or (acumulado_con_preasig >= META_SEMANAL):
    bg_card = "#103820"
    border_card = "#2e7d32"
    badge_color = "#81c784"
    if acumulado_con_preasig >= META_SEMANAL:
        estado_meta_txt = "🟢 META SEMANAL CUBIERTA"
    elif acumulado_con_preasig >= hito_hoy and produccion_guardia < META_DIARIA_TOP:
        estado_meta_txt = f"🟢 RITMO TOP (Supera hito Día {dia_ciclo}: S/ {hito_hoy:.0f})"
    else:
        estado_meta_txt = "🟢 META DIARIA CUMPLIDA (S/ 450+)"
elif produccion_guardia >= PISO_DIARIO:
    bg_card = "#3d2b05"
    border_card = "#f57f17"
    badge_color = "#ffd54f"
    falta_top = META_DIARIA_TOP - produccion_guardia
    estado_meta_txt = f"🟡 PISO ASEGURADO (Falta S/ {falta_top:.2f} para S/ 450)"
else:
    bg_card = "#3b1111"
    border_card = "#c62828"
    badge_color = "#ef9a9a"
    falta_piso = PISO_DIARIO - produccion_guardia
    estado_meta_txt = f"🔴 PISO EN RIESGO (Falta S/ {falta_piso:.2f} para S/ 300)"

txt_piso_hoy = "Piso Guardia: ✅ ASEGURADO" if produccion_guardia >= PISO_DIARIO else f"Piso Guardia: 🚨 EN RIESGO (Falta S/ {max(0.0, PISO_DIARIO - produccion_guardia):.2f})"

st.markdown(
    f"""
    <div style="background-color: {bg_card}; padding: 16px; border-radius: 12px; border: 2px solid {border_card}; margin-bottom: 14px;">
        <div style="font-size: 13px; font-weight: 700; color: {badge_color}; margin-bottom: 6px;">
            🚖 GUARDIA EN CURSO | {estado_meta_txt}
        </div>
        <div style="font-size: 26px; font-weight: 800; color: #ffffff; margin-bottom: 2px;">
            S/ {acumulado_con_preasig:.2f} <span style="font-size: 15px; font-weight: 500; color: #e0e0e0;">de S/ {META_SEMANAL:.0f} (Bono {BONO_MONTO:.0f})</span>
        </div>
        <div style="font-size: 12px; color: {badge_color}; margin-bottom: 8px;">
            Pendiente semanal: S/ {faltante_semana:.2f} ({porcentaje_avance:.1f}% avance) | Día {dia_ciclo} (Hito: S/ {hito_hoy:.0f})
        </div>
        <div style="font-size: 13px; color: #ffffff; margin-bottom: 4px;">
            Hoy Hecho: S/ {total_hoy_hecho:.2f} | Preasig: S/ {suma_preasig:.2f} | Total Hoy Proyectado: <b>S/ {produccion_guardia:.2f}</b>
        </div>
        <div style="font-size: 12px; font-weight: 600; color: #ffffff;">
            {txt_piso_hoy} | Objetivo Diario Óptimo: S/ {META_DIARIA_TOP:.2f}
        </div>
        <div style="background: rgba(0,0,0,0.3); padding: 8px 10px; border-radius: 6px; margin-top: 10px; font-size: 11px; color: #ffe082;">
            🎯 <b>Escala 5x2:</b> D1 S/ 450 | D2 S/ 900 | D3 S/ 1,350 | D4 S/ 1,800 | D5 S/ 2,250
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# PESTAÑAS PRINCIPALES
# ==============================================================================
tab_reg, tab_abonos, tab_guardia, tab_hist = st.tabs(["📝 Registro", "💵 Abonos", "📊 Guardia & Metas", "📋 Historial"])

# ------------------------------------------------------------------------------
# PESTAÑA 1: REGISTRO (CON OCR Y FORMULARIO ORIGINAL)
# ------------------------------------------------------------------------------
with tab_reg:
    st.markdown("#### Captura(s) del viaje")
    archivo_foto = st.file_uploader(
        "Subir ticket o comprobante:", 
        type=["png", "jpg", "jpeg"],
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    if archivo_foto is not None:
        if st.button("⚡ Extraer Rápido con IA", use_container_width=True):
            with st.spinner("Leyendo comprobante con IA..."):
                datos_ia = extraer_datos_con_ia(archivo_foto)
                if datos_ia:
                    st.session_state.form_datos["num_servicio"] = str(datos_ia.get("num_servicio", "")).strip()
                    try:
                        st.session_state.form_datos["fecha"] = datetime.strptime(datos_ia.get("fecha"), "%Y-%m-%d").date()
                    except Exception:
                        pass
                    st.session_state.form_datos["hora"] = str(datos_ia.get("hora", ahora_pe.strftime("%H:%M")))
                    st.session_state.form_datos["tarifa_base"] = float(datos_ia.get("tarifa_base", 0.0))
                    st.session_state.form_datos["recargos"] = float(datos_ia.get("recargos", 0.0))
                    st.session_state.form_datos["peajes"] = float(datos_ia.get("peajes", 0.0))
                    st.session_state.form_datos["origen"] = str(datos_ia.get("origen", ""))
                    st.session_state.form_datos["destino"] = str(datos_ia.get("destino", ""))
                    st.session_state.form_datos["comentarios"] = str(datos_ia.get("comentarios", ""))
                    
                    asig_val = str(datos_ia.get("asignacion", "Kusi")).capitalize()
                    st.session_state.form_datos["asignacion"] = asig_val if asig_val in ["Kusi", "Directo", "Reserva", "Inmediato"] else "Directo"
                    
                    pago_val = str(datos_ia.get("metodo_pago", "Empresa"))
                    st.session_state.form_datos["pago"] = pago_val if pago_val in ["Empresa", "Tarjeta / POS", "Efectivo", "Yape", "Plin"] else "Empresa"
                    
                    st.success("¡Datos listos!")

    with st.form("form_registro_viaje"):
        c1, c2 = st.columns(2)
        with c1:
            id_servicio = st.text_input("N° Servicio / Ticket Pasajero *", value=st.session_state.form_datos["num_servicio"])
            fecha_input = st.date_input("Fecha", value=st.session_state.form_datos["fecha"])
            tarifa_base = st.number_input("Tarifa Base (S/) *", min_value=0.0, value=float(st.session_state.form_datos["tarifa_base"]), step=5.0, format="%.2f")
            recargos = st.number_input("Recargos (S/)", min_value=0.0, value=float(st.session_state.form_datos["recargos"]), step=1.0, format="%.2f")
            categoria_servicio = st.selectbox("Categoría", ["XL", "Van", "Lite", "Sedán"], index=0)
            origen = st.text_input("Origen", value=st.session_state.form_datos["origen"])

        with c2:
            hora_str = st.text_input("Hora (HH:MM)", value=st.session_state.form_datos["hora"])
            reembolsos = st.number_input("Peajes / Reembolsos (S/)", min_value=0.0, value=float(st.session_state.form_datos["peajes"]), step=1.0, format="%.2f")
            
            opciones_asig = ["Directo", "Kusi", "Reserva", "Inmediato"]
            idx_asig = opciones_asig.index(st.session_state.form_datos["asignacion"]) if st.session_state.form_datos["asignacion"] in opciones_asig else 0
            tipo_asignacion = st.selectbox("Tipo de Asignación", opciones_asig, index=idx_asig)
            
            opciones_pago = ["Empresa", "Tarjeta / POS", "Efectivo", "Yape", "Plin"]
            idx_pago = opciones_pago.index(st.session_state.form_datos["pago"]) if st.session_state.form_datos["pago"] in opciones_pago else 0
            metodo_pago = st.selectbox("Método de Pago", opciones_pago, index=idx_pago)
            
            destino = st.text_input("Destino", value=st.session_state.form_datos["destino"])

        comentarios = st.text_input("Comentarios / Pasajero", value=st.session_state.form_datos["comentarios"])

        produccion_neta = tarifa_base + recargos
        monto_total = produccion_neta + reembolsos
        st.markdown(f"**Neta:** S/ {produccion_neta:.2f} | **Total a Rendir:** S/ {monto_total:.2f}")

        submit_guardar = st.form_submit_button("💾 Guardar Viaje")
        if submit_guardar:
            if not id_servicio:
                st.error("Ingresa N° de ticket o servicio.")
            elif tarifa_base <= 0:
                st.error("La tarifa base debe ser mayor a S/ 0.00.")
            else:
                try:
                    partes = str(hora_str).strip().split(":")
                    hh, mm = int(partes[0]), int(partes[1])
                except Exception:
                    hh, mm = ahora_pe.hour, ahora_pe.minute

                dt_viaje = datetime.combine(fecha_input, datetime.min.time()).replace(hour=hh, minute=mm)
                f_op = calcular_fecha_operativa(dt_viaje)
                sem_op = obtener_semana_operativa(f_op)

                datos_guardar = {
                    "id_servicio": str(id_servicio).strip(),
                    "fecha_hora_viaje": dt_viaje.strftime("%Y-%m-%d %H:%M:00"),
                    "fecha_operativa": str(f_op),
                    "semana_operativa": str(sem_op),
                    "tipo_asignacion": str(tipo_asignacion),
                    "tipo_usuario": "Empresa" if metodo_pago == "Empresa" else "Particular",
                    "categoria_servicio": str(categoria_servicio),
                    "metodo_pago": str(metodo_pago),
                    "origen": str(origen).strip(),
                    "destino": str(destino).strip(),
                    "distancia_km": 0.0,
                    "duracion_min": 0.0,
                    "tarifa_base": float(tarifa_base),
                    "monto_recargos": float(recargos),
                    "monto_reembolsos": float(reembolsos),
                    "produccion_neta": float(produccion_neta),
                    "monto_total": float(monto_total),
                    "abono_enviado": 1 if metodo_pago in ["Efectivo", "Yape", "Plin"] else 0,
                    "fecha_hora_abono": "",
                    "comentarios": str(comentarios).strip()
                }

                guardar_viaje_remoto(datos_guardar)
                st.session_state.uploader_key += 1
                st.session_state.form_datos = {
                    "num_servicio": "",
                    "fecha": ahora_pe.date(),
                    "hora": ahora_pe.strftime("%H:%M"),
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
                st.success("¡Viaje guardado exitosamente!")
                st.rerun()

# ------------------------------------------------------------------------------
# PESTAÑA 2: ABONOS (CON FORMATO RÁPIDO PARA COPIAR: SERVICIO - MÓVIL)
# ------------------------------------------------------------------------------
with tab_abonos:
    st.subheader("💵 Control de Abonos y Cobranzas en Ruta")
    st.caption("Seguimiento de efectivo/billeteras y generación de texto para constancia de abono.")
    
    # Número de móvil predeterminado (puedes editarlo o dejarlo fijo)
    NUMERO_MOVIL_DEFAULT = "2986"

    if not df_all.empty:
        df_cobranzas = df_all[df_all["metodo_pago"].isin(["Efectivo", "Yape", "Plin"])].copy()
        if not df_cobranzas.empty:
            total_cobrado_mano = df_cobranzas["monto_total"].sum()
            total_abonado = df_cobranzas[df_cobranzas["abono_enviado"] == 1]["monto_total"].sum()
            saldo_pendiente = total_cobrado_mano - total_abonado
            
            c_ab1, c_ab2, c_ab3 = st.columns(3)
            c_ab1.metric("Total Cobrado en Mano", f"S/ {total_cobrado_mano:.2f}")
            c_ab2.metric("Total Rendido / Abonado", f"S/ {total_abonado:.2f}")
            c_ab3.metric("Saldo por Rendir", f"S/ {saldo_pendiente:.2f}")

            st.divider()

            # --- BLOQUE DE COPIA RÁPIDA: SERVICIO - MÓVIL ---
            st.markdown("##### 📋 Copiar Formato de Abono (Servicio - Móvil)")
            col_sel_serv, col_movil = st.columns([2, 1])
            
            with col_sel_serv:
                lista_pendientes = df_cobranzas[df_cobranzas["abono_enviado"] == 0]["id_servicio"].dropna().astype(str).tolist()
                lista_opciones = lista_pendientes if lista_pendientes else df_cobranzas["id_servicio"].dropna().astype(str).tolist()
                servicio_sel = st.selectbox("Selecciona Servicio a Abonar:", lista_opciones, index=0)
            
            with col_movil:
                nro_movil = st.text_input("N° Móvil:", value=NUMERO_MOVIL_DEFAULT)

            if servicio_sel:
                texto_abono = f"{servicio_sel.strip()} - {nro_movil.strip()}"
                st.markdown("**Texto generado para comprobante:**")
                # st.code muestra el botón nativo de copia al portapapeles en Streamlit
                st.code(texto_abono, language="text")
                st.caption("👉 Toca el icono de copiar a la derecha del cuadro para pegarlo directamente en Yape/Plin/Transferencia.")
            
            st.divider()
            
            st.markdown("##### Detalle de Servicios con Cobro Directo")
            cols_ab = ["id_servicio", "fecha_operativa", "metodo_pago", "monto_total", "abono_enviado"]
            st.dataframe(df_cobranzas[[c for c in cols_ab if c in df_cobranzas.columns]].iloc[::-1], use_container_width=True, hide_index=True)
        else:
            st.info("No hay servicios cobrados en Efectivo o Billeteras Digitales registrados.")
    else:
        st.info("Sin registros de servicios.")



# ------------------------------------------------------------------------------
# PESTAÑA 3: GUARDIA & METAS (PREASIGNADOS)
# ------------------------------------------------------------------------------
with tab_guardia:
    st.subheader("Control de Preasignados de la Guardia")
    st.caption("Anota aquí los servicios programados que aún no has cobrado para proyectar el cumplimiento del piso.")
    
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.session_state.preasignados["pre1"] = st.number_input("Preasignado 1 (S/)", value=float(st.session_state.preasignados.get("pre1", 0.0)), step=10.0, format="%.2f")
    with col_p2:
        st.session_state.preasignados["pre2"] = st.number_input("Preasignado 2 (S/)", value=float(st.session_state.preasignados.get("pre2", 0.0)), step=10.0, format="%.2f")
    with col_p3:
        st.session_state.preasignados["pre3"] = st.number_input("Preasignado 3 (S/)", value=float(st.session_state.preasignados.get("pre3", 0.0)), step=10.0, format="%.2f")
        
    st.info(f"Total Proyectado en Preasignados: **S/ {sum(st.session_state.preasignados.values()):.2f}**")

# ------------------------------------------------------------------------------
# PESTAÑA 4: HISTORIAL
# ------------------------------------------------------------------------------
with tab_hist:
    with st.expander("📥 Importar o Sincronizar Resúmenes Masivos"):
        resumen_file = st.file_uploader("Subir CSV o Excel con producción por fecha:", type=["csv", "xlsx"])
        if resumen_file is not None:
            if st.button("Procesar y Sincronizar a Google Sheets"):
                try:
                    df_res = pd.read_csv(resumen_file) if resumen_file.name.endswith(".csv") else pd.read_excel(resumen_file)
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
        lista_ids = [str(x) for x in df_mostrar["id_servicio"].tolist() if str(x).strip()]
        if lista_ids:
            id_borrar = st.selectbox("Eliminar servicio de la nube:", lista_ids)
            if st.button("❌ Confirmar Eliminación"):
                eliminar_viaje_remoto(id_borrar)
                st.success("Registro eliminado de Google Sheets.")
                st.rerun()
    else:
        st.info("No hay registros en la nube.")
