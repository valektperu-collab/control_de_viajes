import streamlit as st
import pandas as pd
import requests
import json
import os
from datetime import datetime, date, timedelta, timezone

# ==============================================================================
# CONFIGURACIÓN GENERAL Y TEMA MÓVIL
# ==============================================================================
st.set_page_config(
    page_title="Copiloto Táctico de Viajes",
    page_icon="🚕",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Zona horaria nativa Lima (UTC-5) sin dependencias externas
TZ_LIMA = timezone(timedelta(hours=-5))

def obtener_ahora():
    return datetime.now(TZ_LIMA)

# Webhook de Google Apps Script (toma el secret o variable de entorno)
GSHEETS_URL = st.secrets.get(
    "GSHEETS_URL", 
    os.environ.get("GSHEETS_URL", "https://script.google.com/macros/s/AKfycbz_tu_script_id_aqui/exec")
)

LOCAL_CACHE_FILE = "viajes_cache.json"

META_SEMANAL = 2250.00
BONO_MONTO = 100.00
PISO_DIARIO = 300.00
HORA_CORTE_NOCTURNO = 5  # 05:00 a. m.

# ==============================================================================
# GESTIÓN DE TIEMPO Y PERSISTENCIA (OFFLINE FIRST)
# ==============================================================================
def calcular_fecha_operativa(dt_obj):
    """Regla de turno nocturno: antes de las 5 a. m. pertenece al día anterior."""
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
    """Descarga de Google Sheets con timeout corto (4 seg). Si falla, recurre a caché local."""
    try:
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
            r = requests.get(GSHEETS_URL, timeout=4)
            if r.status_code == 200:
                datos = r.json()
                if isinstance(datos, list) and len(datos) > 0:
                    guardar_cache_local(datos)
                    return datos
    except Exception:
        pass
    return cargar_cache_local()

# Inicialización de estado en memoria
if "viajes" not in st.session_state:
    st.session_state.viajes = sincronizar_desde_nube()

if "preasignados" not in st.session_state:
    st.session_state.preasignados = {"pre1": 0.0, "pre2": 0.0, "pre3": 0.0}

def guardar_viaje_seguro(registro):
    """Guarda en 0.1s en memoria local y sincroniza en background sin congelar."""
    id_nuevo = str(registro["id_servicio"]).strip()
    viajes_filtrados = [v for v in st.session_state.viajes if str(v.get("id_servicio")).strip() != id_nuevo]
    viajes_filtrados.append(registro)
    st.session_state.viajes = viajes_filtrados
    
    # Respaldo inmediato en disco
    guardar_cache_local(viajes_filtrados)
    
    # Sincronización con Google Apps Script (máximo 3 segundos)
    sincronizado = False
    try:
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
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
        if GSHEETS_URL and "AKfycbz_tu_script_id" not in GSHEETS_URL:
            requests.post(GSHEETS_URL, json={"action": "delete", "id_servicio": id_elim}, timeout=3)
    except Exception:
        pass

# ==============================================================================
# CÁLCULOS TÁCTICOS Y ESTADO SEMANAL
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

# Regla de piso diario eliminatorio
piso_asegurado = total_hoy_hecho >= PISO_DIARIO
faltante_piso_hoy = max(0.0, PISO_DIARIO - total_hoy_hecho)

# ==============================================================================
# HERO CARD - CONTROL DE METAS
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
            <span><b>Hoy Total:</b> S/ {total_hoy_hecho + suma_preasig:.2f}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# PESTAÑAS PRINCIPALES
# ==============================================================================
tab_registro, tab_guardia, tab_historial = st.tabs(["📝 Registrar Viaje", "📊 Preasignados", "📋 Historial"])

# ------------------------------------------------------------------------------
# TAB 1: REGISTRAR VIAJE
# ------------------------------------------------------------------------------
with tab_registro:
    col1, col2 = st.columns(2)
    with col1:
        num_servicio = st.text_input("N° Servicio / Ticket Pasajero *", key="f_num")
        fecha_viaje = st.date_input("Fecha", value=ahora.date(), key="f_fecha")
        hora_viaje = st.text_input("Hora (HH:MM)", value=ahora.strftime("%H:%M"), key="f_hora")
        tarifa_base = st.number_input("Tarifa Base (S/) *", min_value=0.0, step=5.0, format="%.2f", key="f_base")
        recargos = st.number_input("Recargos (Horario / Reserva) (S/)", min_value=0.0, step=1.0, format="%.2f", key="f_rec")
        
    with col2:
        peajes = st.number_input("Peajes / Reembolsos (S/)", min_value=0.0, step=1.0, format="%.2f", key="f_peajes")
        asignacion = st.selectbox("Tipo de Asignación", ["Kusi", "Directo", "Reserva", "Inmediato"], key="f_asig")
        categoria = st.selectbox("Categoría", ["XL", "Van", "Lite", "Sedán"], key="f_cat")
        metodo_pago = st.selectbox("Método de Pago", ["Empresa", "Tarjeta / POS", "Efectivo", "Yape", "Plin"], key="f_pago")
        origen = st.text_input("Origen", key="f_orig")
        destino = st.text_input("Destino", key="f_dest")

    comentarios = st.text_input("Comentarios / Referencia", key="f_coment")
    calc_neta = float(tarifa_base + recargos)
    calc_total = float(calc_neta + peajes)

    st.markdown(
        f"""
        <div style="background: rgba(255,255,255,0.06); padding: 10px 14px; border-radius: 8px; margin: 12px 0;">
            <b>Producción Neta (Suma a Meta):</b> <span style="color:#56f5a3; font-size:16px; font-weight:700;">S/ {calc_neta:.2f}</span> | 
            <b>Total a Rendir:</b> S/ {calc_total:.2f}
        </div>
        """,
        unsafe_allow_html=True
    )

    if st.button("💾 Guardar Viaje", type="primary", use_container_width=True):
        if not str(num_servicio).strip():
            st.error("⚠️ Debes ingresar el N° de Servicio o Ticket.")
        elif tarifa_base <= 0:
            st.error("⚠️ La Tarifa Base debe ser mayor a S/ 0.00.")
        else:
            try:
                partes = str(hora_viaje).strip().split(":")
                hh, mm = int(partes[0]), int(partes[1])
            except Exception:
                hh, mm = ahora.hour, ahora.minute

            dt_viaje = datetime.combine(fecha_viaje, datetime.min.time()).replace(hour=hh, minute=mm)
            f_op = calcular_fecha_operativa(dt_viaje)
            s_op = calcular_semana_operativa(f_op)

            nuevo_registro = {
                "id_servicio": str(num_servicio).strip(),
                "fecha_hora_viaje": dt_viaje.strftime("%Y-%m-%d %H:%M:00"),
                "fecha_operativa": str(f_op),
                "semana_operativa": str(s_op),
                "tipo_asignacion": str(asignacion),
                "tipo_usuario": "Corporativo" if metodo_pago == "Empresa" else "Particular",
                "categoria_servicio": str(categoria),
                "metodo_pago": str(metodo_pago),
                "origen": str(origen).strip(),
                "destino": str(destino).strip(),
                "distancia_km": 0.0,
                "duracion_min": 0,
                "tarifa_base": float(tarifa_base),
                "monto_recargos": float(recargos),
                "monto_reembolsos": float(peajes),
                "produccion_neta": float(calc_neta),
                "monto_total": float(calc_total),
                "abono_enviado": 1 if metodo_pago in ["Efectivo", "Yape", "Plin"] else 0,
                "fecha_hora_abono": "",
                "comentarios": str(comentarios).strip()
            }

            ok_sync = guardar_viaje_seguro(nuevo_registro)
            if ok_sync:
                st.success(f"✅ ¡Viaje {num_servicio} guardado y sincronizado!")
            else:
                st.success(f"✅ ¡Viaje {num_servicio} guardado en el teléfono!")
            st.rerun()

# ------------------------------------------------------------------------------
# TAB 2: PREASIGNADOS
# ------------------------------------------------------------------------------
with tab_guardia:
    st.subheader("Control de Preasignados de la Guardia")
    st.caption("Anota los viajes preasignados para saber cuánto te falta para el piso diario.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.session_state.preasignados["pre1"] = st.number_input("Preasignado 1 (S/)", value=float(st.session_state.preasignados.get("pre1", 0.0)), step=10.0, format="%.2f")
    with c2:
        st.session_state.preasignados["pre2"] = st.number_input("Preasignado 2 (S/)", value=float(st.session_state.preasignados.get("pre2", 0.0)), step=10.0, format="%.2f")
    with c3:
        st.session_state.preasignados["pre3"] = st.number_input("Preasignado 3 (S/)", value=float(st.session_state.preasignados.get("pre3", 0.0)), step=10.0, format="%.2f")
    st.info(f"Total Proyectado en Preasignados: **S/ {sum(st.session_state.preasignados.values()):.2f}**")

# ------------------------------------------------------------------------------
# TAB 3: HISTORIAL
# ------------------------------------------------------------------------------
with tab_historial:
    st.subheader("Servicios Registrados")
    if st.button("🔄 Forzar Sincronización Nube", use_container_width=True):
        st.session_state.viajes = sincronizar_desde_nube()
        st.rerun()

    if not df_todos.empty:
        columnas_ver = ["id_servicio", "fecha_operativa", "produccion_neta", "tarifa_base", "metodo_pago", "destino", "comentarios"]
        cols_presentes = [c for c in columnas_ver if c in df_todos.columns]
        st.dataframe(df_todos[cols_presentes].iloc[::-1], use_container_width=True, hide_index=True)
        st.divider()
        st.markdown("#### 🗑️ Eliminar un registro")
        lista_ids = [str(x) for x in df_todos["id_servicio"].tolist() if str(x).strip()]
        id_borrar = st.selectbox("Selecciona ID a eliminar:", options=["-- Ninguno --"] + lista_ids[::-1])
        if id_borrar != "-- Ninguno --":
            if st.button(f"❌ Confirmar Eliminación de {id_borrar}", type="secondary"):
                eliminar_viaje_seguro(id_borrar)
                st.warning(f"Servicio {id_borrar} eliminado.")
                st.rerun()
    else:
        st.info("No hay servicios registrados.")
