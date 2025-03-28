# 1. Imports (Añadir gspread)
import streamlit as st
import json
import os
import math
from datetime import datetime, timedelta
import pandas as pd
import traceback
import gspread # <<< NUEVO
# from google.oauth2.service_account import Credentials # Para autenticación más robusta si es necesario
# from google.auth import exceptions # Para manejo de errores de autenticación

# --- Constantes ---
# (Igual que antes, pero ARCHIVO_DATOS ya no se usará directamente para guardar/cargar)
# ARCHIVO_DATOS = "stock_data_hist.json" # Ya no es la fuente principal
GOOGLE_SHEET_NAME = "MiAppStockSheet" # <<< NUEVO: Nombre exacto de tu Google Sheet
VENTAS_SHEET_NAME = "Ventas"          # <<< NUEVO: Nombre exacto de la pestaña de ventas

LEAD_TIME_FIJO = 3
DIAS_SEGURIDAD_FIJOS = 3
DIAS_PROMEDIO = 30
DIAS_HISTORIAL_MAX = 90 # Aún útil para limpieza conceptual

# --- Autenticación con gspread usando Secrets de Streamlit ---
def autenticar_gspread():
    """Autentica con Google Sheets usando credenciales desde Streamlit Secrets."""
    try:
        # Intenta obtener las credenciales desde los secrets de Streamlit
        # Asume que has creado un secret llamado "google_creds_json"
        # con el CONTENIDO COMPLETO de tu archivo JSON de credenciales.
        creds_json_str = st.secrets["google_creds_json"]
        creds_dict = json.loads(creds_json_str) # Convertir string JSON a diccionario
        gc = gspread.service_account_from_dict(creds_dict)
        # print("DEBUG: Autenticación gspread exitosa.") # Debug
        return gc
    except KeyError:
        st.error("Error: Secret 'google_creds_json' no encontrado. Configúralo en Streamlit.")
        return None
    except FileNotFoundError:
        st.error("Error: Archivo JSON de credenciales no encontrado localmente (si no usas secrets).")
        return None
    except json.JSONDecodeError:
         st.error("Error: El contenido del secret 'google_creds_json' no es un JSON válido.")
         return None
    except Exception as e:
        st.error(f"Error inesperado durante autenticación gspread: {e}")
        # st.code(traceback.format_exc()) # Más detalle si es necesario
        return None

# --- Funciones Auxiliares Modificadas ---

def cargar_datos_gsheet(gc, sheet_name, ventas_sheet_name):
    """Carga los datos desde Google Sheets y los estructura como el diccionario anterior."""
    if not gc: return {} # Si falla la autenticación

    productos_data = {}
    try:
        # Abrir la hoja de cálculo por nombre
        sh = gc.open(sheet_name)
        # Seleccionar la pestaña 'Ventas'
        try:
            worksheet = sh.worksheet(ventas_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
             st.error(f"Error: No se encontró la hoja '{ventas_sheet_name}' en '{sheet_name}'.")
             return {}

        # Obtener todos los registros como lista de diccionarios
        # get_all_records asume que la primera fila son encabezados
        try:
            ventas_records = worksheet.get_all_records()
            if not ventas_records: # Si la hoja está vacía (solo encabezados o nada)
                print("DEBUG: Hoja de ventas vacía o sin registros.") # Debug
                return {}
        except Exception as e:
             st.error(f"Error al leer registros de '{ventas_sheet_name}': {e}")
             return {}


        # Procesar los registros para reconstruir la estructura productos_data
        for record in ventas_records:
            # Asegurarse que las columnas esperadas existan y tengan valor
            nombre_prod = record.get('NombreProducto')
            fecha_str = record.get('Fecha')
            cantidad_val = record.get('Cantidad')

            # Validar datos leídos
            if not nombre_prod or not fecha_str or cantidad_val is None:
                # print(f"Advertencia: Fila ignorada por datos faltantes: {record}") # Debug
                continue # Saltar fila incompleta

            # Intentar convertir cantidad a número (puede ser string desde gsheets)
            try:
                 cantidad = int(cantidad_val) # O float() si puedes tener decimales
                 if cantidad < 0: continue # Ignorar cantidades negativas
            except (ValueError, TypeError):
                 # print(f"Advertencia: Cantidad inválida '{cantidad_val}' ignorada para {nombre_prod} en {fecha_str}") # Debug
                 continue # Ignorar si no es un número válido

            # Validar formato de fecha (esperamos YYYY-MM-DD, pero gsheets puede variar)
            try:
                 # Intentar parsear la fecha, ajustar formato si es necesario
                 fecha_obj = datetime.strptime(str(fecha_str), '%Y-%m-%d').date()
                 fecha_final_str = fecha_obj.strftime('%Y-%m-%d') # Asegurar formato estándar
            except (ValueError, TypeError):
                 # Podríamos intentar otros formatos comunes si falla el estándar
                 # print(f"Advertencia: Formato de fecha inválido '{fecha_str}' ignorado para {nombre_prod}") # Debug
                 continue

            # Agregar al diccionario productos_data
            if nombre_prod not in productos_data:
                productos_data[nombre_prod] = {"ventas_historico": []}

            # Añadir la venta al historial del producto
            # Evitar duplicados exactos si se lee el mismo dato varias veces
            venta_existente = False
            for v in productos_data[nombre_prod]["ventas_historico"]:
                 if v.get("fecha") == fecha_final_str and v.get("cantidad") == cantidad:
                      venta_existente = True; break
            if not venta_existente:
                 productos_data[nombre_prod]["ventas_historico"].append({
                     "fecha": fecha_final_str,
                     "cantidad": cantidad
                 })

        # Ordenar historiales después de cargar todo
        for nombre_prod in productos_data:
            productos_data[nombre_prod]["ventas_historico"].sort(key=lambda x: x.get("fecha", "0000-00-00"), reverse=True)

        print(f"DEBUG: Datos cargados desde GSheet para {len(productos_data)} productos.") # Debug
        return productos_data

    except gspread.exceptions.SpreadsheetNotFound:
        st.error(f"Error: No se encontró la Google Sheet llamada '{sheet_name}'.")
        return {}
    except Exception as e:
        st.error(f"Error inesperado al cargar datos de Google Sheet: {e}")
        # st.code(traceback.format_exc()) # Más detalle
        return {}


def guardar_datos_gsheet(gc, sheet_name, ventas_sheet_name, datos_actualizados):
    """Guarda TODOS los datos actuales en Google Sheets, SOBRESCRIBIENDO la hoja de ventas."""
    if not gc: return False

    try:
        sh = gc.open(sheet_name)
        try:
            worksheet = sh.worksheet(ventas_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
             st.error(f"Error: Hoja '{ventas_sheet_name}' no encontrada para guardar.")
             return False

        # --- Preparar datos para escribir ---
        # Convertir la estructura de diccionario anidado a una lista plana de filas
        filas_para_escribir = [['NombreProducto', 'Fecha', 'Cantidad']] # Encabezados
        nombres_productos = sorted(datos_actualizados.keys()) # Ordenar productos alfabéticamente

        for nombre_prod in nombres_productos:
            data_prod = datos_actualizados[nombre_prod]
            historial = data_prod.get("ventas_historico", [])
            if isinstance(historial, list):
                 # Ordenar historial por fecha descendente antes de escribir
                 historial.sort(key=lambda x: x.get("fecha", "0000-00-00"), reverse=True)
                 for venta in historial:
                     # Validar datos antes de añadir fila
                     if isinstance(venta, dict) and isinstance(venta.get("fecha"), str) and isinstance(venta.get("cantidad"), (int, float)):
                         # Limpieza conceptual de historial viejo (opcional aquí, podría hacerse antes)
                         try:
                             fecha_obj = datetime.strptime(venta["fecha"], "%Y-%m-%d").date()
                             if fecha_obj >= (datetime.now().date() - timedelta(days=DIAS_HISTORIAL_MAX)):
                                 filas_para_escribir.append([
                                     nombre_prod,
                                     venta["fecha"],
                                     venta["cantidad"]
                                 ])
                         except (ValueError, TypeError):
                             continue # Ignorar fechas inválidas al escribir

        # --- Escribir en la hoja ---
        if len(filas_para_escribir) > 1: # Solo escribir si hay datos además de encabezados
            worksheet.clear() # Borrar todo el contenido anterior
            worksheet.update(filas_para_escribir, value_input_option='USER_ENTERED')
            # 'USER_ENTERED' intenta interpretar tipos de datos como números/fechas
            print(f"DEBUG: Datos guardados en GSheet. {len(filas_para_escribir) - 1} filas de ventas escritas.") # Debug
            return True
        else:
            # Si no hay datos, solo limpiar y poner encabezados
            worksheet.clear()
            worksheet.update([filas_para_escribir[0]], value_input_option='USER_ENTERED')
            print("DEBUG: GSheet limpiada (sin datos de ventas para guardar).") # Debug
            return True

    except Exception as e:
        st.error(f"Error inesperado al guardar datos en Google Sheet: {e}")
        # st.code(traceback.format_exc()) # Más detalle
        return False


# calcular_promedio_ventas (SIN CAMBIOS de la versión anterior)
def calcular_promedio_ventas(historial, dias_ventana):
    # ... (exactamente igual que antes) ...
    if not historial or not isinstance(historial, list): return 0.0
    hoy = datetime.now().date()
    fecha_inicio_ventana = hoy - timedelta(days=dias_ventana)
    total_ventas_ventana = 0
    fechas_validas = []
    for venta in historial:
        if isinstance(venta, dict) and isinstance(venta.get("fecha"), str) and len(venta["fecha"]) == 10:
            try:
                fecha_venta = datetime.strptime(venta["fecha"], "%Y-%m-%d").date()
                fechas_validas.append(fecha_venta)
                if fecha_inicio_ventana <= fecha_venta <= hoy:
                    cantidad = venta.get("cantidad", 0)
                    if isinstance(cantidad, (int, float)) and cantidad >= 0:
                        total_ventas_ventana += cantidad
            except (ValueError, TypeError): continue
    if not fechas_validas: return 0.0
    primera_fecha_venta = min(fechas_validas)
    dias_desde_primera_venta = (hoy - primera_fecha_venta).days + 1
    denominador = min(dias_desde_primera_venta, dias_ventana)
    denominador = max(1, denominador)
    promedio_diario = total_ventas_ventana / denominador
    return promedio_diario

# --- Lógica de la Aplicación Streamlit (Adaptada para GSheet) ---

st.set_page_config(layout="wide", page_title="Stock Óptimo (GSheet)")
st.title("📊 Calculadora de Stock Óptimo (Google Sheets)")

# Autenticar UNA VEZ al inicio
gc = autenticar_gspread()

# Cargar datos y guardar en estado de sesión
if 'productos_data' not in st.session_state:
    if gc:
        # Usar la función de carga de GSheet
        st.session_state.productos_data = cargar_datos_gsheet(gc, GOOGLE_SHEET_NAME, VENTAS_SHEET_NAME)
    else:
        st.session_state.productos_data = {} # Empezar vacío si falla autenticación/carga

# Resto del estado de sesión (igual que antes)
if 'selected_product' not in st.session_state: st.session_state.selected_product = None
if 'show_create_form' not in st.session_state: st.session_state.show_create_form = False

# --- Barra Lateral (Sidebar) ---
with st.sidebar:
    st.header("📦 Productos")

    # Crear Nuevo Producto
    if st.button("➕ Crear Nuevo Producto", key="toggle_create"):
         st.session_state.show_create_form = not st.session_state.show_create_form
    if st.session_state.show_create_form:
        with st.form("create_form", clear_on_submit=True):
             new_prod_name_input = st.text_input("Nombre del Nuevo Producto:")
             submitted_create = st.form_submit_button("Crear y Seleccionar")
             if submitted_create:
                 new_prod_name = new_prod_name_input.strip()
                 if not new_prod_name: st.warning("Nombre vacío.")
                 elif new_prod_name in st.session_state.productos_data:
                     st.warning(f"'{new_prod_name}' ya existe. Seleccionado.")
                     st.session_state.selected_product = new_prod_name
                     st.session_state.show_create_form = False; st.rerun()
                 else:
                     # Añadir localmente y luego intentar guardar TODO
                     st.session_state.productos_data[new_prod_name] = {"ventas_historico": []}
                     if guardar_datos_gsheet(gc, GOOGLE_SHEET_NAME, VENTAS_SHEET_NAME, st.session_state.productos_data):
                         st.success(f"Producto '{new_prod_name}' creado.")
                         st.session_state.selected_product = new_prod_name
                         st.session_state.show_create_form = False; st.rerun()
                     else:
                          # Revertir si falla guardado
                          if new_prod_name in st.session_state.productos_data:
                               del st.session_state.productos_data[new_prod_name]
                          st.error("Error al guardar el nuevo producto en Google Sheets.")

    st.divider()
    # Selección de Producto Existente (igual que antes)
    lista_productos_sorted = sorted(st.session_state.productos_data.keys())
    options = ["-- Selecciona --"] + lista_productos_sorted
    current_selection_index = 0
    if st.session_state.selected_product and st.session_state.selected_product in options:
        try: current_selection_index = options.index(st.session_state.selected_product)
        except ValueError: st.session_state.selected_product = None
    selected = st.selectbox("Selecciona Existente:", options=options, index=current_selection_index, key="product_selector")
    if selected == "-- Selecciona --":
        if st.session_state.selected_product is not None: st.session_state.selected_product = None; st.rerun()
    elif selected != st.session_state.selected_product:
        st.session_state.selected_product = selected; st.rerun()

    # Gestión de Datos (Botón Descargar - Aún útil para backup)
    st.divider()
    st.subheader("💾 Gestión de Datos")
    if st.session_state.productos_data:
        try:
            json_string = json.dumps(st.session_state.productos_data, indent=4, ensure_ascii=False)
            st.download_button( label="📥 Descargar Backup (JSON)", data=json_string, file_name=f"stock_backup_{datetime.now().strftime('%Y%m%d')}.json", mime="application/json" )
        except Exception as e: st.error(f"Error preparando descarga: {e}")
    else: st.info("No hay datos para descargar.")

# --- Panel Principal ---
if st.session_state.selected_product:
    st.header(f"📈 Detalles: {st.session_state.selected_product}")

    # Asegurar datos en estado de sesión
    if st.session_state.selected_product not in st.session_state.productos_data:
         # Inicializar si falta (podría pasar si hubo error de carga inicial)
         st.session_state.productos_data[st.session_state.selected_product] = {"ventas_historico": []}
         st.warning("Datos del producto no encontrados inicialmente, inicializando historial.")
         # Podríamos intentar recargar aquí si fuera necesario, pero es complejo manejarlo bien

    producto_actual = st.session_state.productos_data[st.session_state.selected_product]
    historial_actual = producto_actual.get("ventas_historico", [])
    if not isinstance(historial_actual, list): historial_actual = []

    # Formulario Agregar Venta
    with st.form("venta_form"):
        st.subheader("➕ Agregar Venta")
        col1, col2 = st.columns([1, 2])
        with col1: input_fecha = st.date_input("Fecha Venta", value=datetime.now().date(), key="fecha_venta")
        with col2: input_cantidad = st.number_input("Cantidad Vendida", min_value=0, step=1, key="cantidad_venta")
        submitted_venta = st.form_submit_button("💾 Guardar Venta y Recalcular Stock")

        if submitted_venta:
            fecha_str = input_fecha.strftime('%Y-%m-%d')
            cantidad = input_cantidad
            entrada_modificada = False; indice_existente = -1
            for i, venta in enumerate(historial_actual):
                 if isinstance(venta, dict) and venta.get("fecha") == fecha_str: indice_existente = i; break
            if indice_existente != -1:
                 if historial_actual[indice_existente].get("cantidad") != cantidad:
                     historial_actual[indice_existente]["cantidad"] = cantidad
                     st.info(f"Venta del {fecha_str} actualizada a {cantidad} uds.")
                     entrada_modificada = True
                 else: st.info(f"Venta para {fecha_str} ya registrada (sin cambios)."); entrada_modificada = True # O False?
            else:
                 historial_actual.append({"fecha": fecha_str, "cantidad": cantidad})
                 historial_actual.sort(key=lambda x: x.get("fecha", "0000-00-00"), reverse=True)
                 st.success(f"Venta del {fecha_str} ({cantidad} uds) agregada.")
                 entrada_modificada = True

            if entrada_modificada:
                 # Actualizar el estado de sesión
                 st.session_state.productos_data[st.session_state.selected_product]["ventas_historico"] = historial_actual
                 # Guardar TODOS los datos actualizados en GSheet
                 if guardar_datos_gsheet(gc, GOOGLE_SHEET_NAME, VENTAS_SHEET_NAME, st.session_state.productos_data):
                      st.rerun() # Rerun para refrescar cálculos y visualización
                 else:
                      st.error("¡Error Crítico! No se pudo guardar en Google Sheets.")
                      # Considerar revertir el cambio en historial_actual?

    st.divider()
    # Mostrar Resultados (Igual que antes)
    st.subheader("📊 Recomendaciones de Stock")
    promedio = calcular_promedio_ventas(historial_actual, DIAS_PROMEDIO)
    demanda_lt = promedio * LEAD_TIME_FIJO; stock_seg = promedio * DIAS_SEGURIDAD_FIJOS
    optimo = math.ceil(demanda_lt + stock_seg); pedido = math.ceil(demanda_lt + stock_seg)
    col_res1, col_res2, col_res3 = st.columns(3)
    with col_res1: st.metric(label=f"Prom. Diario ({DIAS_PROMEDIO}d)", value=f"{promedio:.2f}")
    with col_res2: st.metric(label="Stock Óptimo Sugerido", value=f"{optimo}")
    with col_res3: st.metric(label="Punto de Pedido", value=f"{pedido}")
    st.caption(f"Cálculos basados en Lead Time={LEAD_TIME_FIJO}d y Seguridad={DIAS_SEGURIDAD_FIJOS}d.")
    st.divider()
    # Mostrar Historial (Igual que antes)
    st.subheader("📜 Historial Reciente")
    if not historial_actual: st.info("No hay ventas registradas.")
    else:
        try:
            df_historial = pd.DataFrame(historial_actual)
            df_historial['cantidad'] = pd.to_numeric(df_historial['cantidad'], errors='coerce').fillna(0).astype(int)
            df_historial['fecha'] = pd.to_datetime(df_historial['fecha'])
            df_historial = df_historial.sort_values(by='fecha', ascending=False)
            df_historial['fecha'] = df_historial['fecha'].dt.strftime('%Y-%m-%d')
            st.dataframe(df_historial[['fecha', 'cantidad']].head(30), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Error al mostrar historial: {e}")
            hist_texto = "\n".join([f"{v.get('fecha', '??')}: {v.get('cantidad', '??')} uds" for v in historial_actual[:30]])
            st.text_area("Ventas", hist_texto, height=200, disabled=True)
else:
    st.info("⬅️ Selecciona un producto o crea uno nuevo para empezar.")

# --- Bloque Final Opcional ---
# (Puede quedar comentado)
# try: pass
# except Exception as e: st.error(f"¡ERROR FATAL!\n{e}"); st.code(traceback.format_exc())
