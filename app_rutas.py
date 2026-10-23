import streamlit as st
import pandas as pd
import urllib.parse
import requests
import re
from datetime import datetime, timedelta, time
from fpdf import FPDF
import io

st.set_page_config(page_title="Optimizador de Rutas, Tiempos y Carga LIFO", layout="wide")

st.title("🚚 Optimizador Automático de Rutas, Ventanas Horarias y Carga LIFO")
st.markdown("Planificación inteligente con optimización de paradas, respeto de horarios de recepción y retorno a Depósito (Lanús).")

# ID de la planilla de Google Sheets
SPREADSHEET_ID = "1trxZGEEPmnSmXoYDXi30NBnzx5Z4IWnkHsSSpauJYc4"
PESTAÑAS_CHOFERES = ["Nicolas", "Miguel", "Marcelo"]

# Dirección fija del depósito de partida y llegada
DEPOT_DIRECCION = "Vicente Damonte 1247"
DEPOT_LOCALIDAD = "Lanús"

# Función para cargar y unificar todas las solapas
@st.cache_data(ttl=60)
def cargar_todos_los_clientes():
    dfs = []
    errores = {}
    for sheet in PESTAÑAS_CHOFERES:
        sheet_encoded = urllib.parse.quote(sheet.strip())
        url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={sheet_encoded}"
        try:
            df = pd.read_csv(url, header=None)
            df = df.dropna(how='all', axis=1)
            if not df.empty:
                cols_estandar = {
                    0: 'Nro', 
                    1: 'Cliente', 
                    2: 'Fantasia', 
                    3: 'Direccion', 
                    4: 'Localidad', 
                    5: 'Telefono', 
                    6: 'Cobranza', 
                    7: 'Pedido', 
                    8: 'Observaciones'
                }
                df = df.rename(columns=cols_estandar)
                df['Chofer_Origen'] = sheet
                dfs.append(df)
            else:
                errores[sheet] = "La pestaña está vacía."
        except Exception as e:
            errores[sheet] = str(e)
            
    if dfs:
        return pd.concat(dfs, ignore_index=True, sort=False), errores
    return pd.DataFrame(), errores

# Función para obtener coordenadas usando OpenStreetMap (Nominatim)
@st.cache_data(ttl=3600)
def obtener_coordenadas(direccion, localidad):
    if not isinstance(direccion, str) or not isinstance(localidad, str):
        return None, None
    query = f"{direccion}, {localidad}, Buenos Aires, Argentina"
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1"
    headers = {'User-Agent': 'OptimizadorRutasLogistica/1.0'}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None, None

# Función para extraer el texto literal de observaciones
def extraer_ventana_horaria(texto):
    if not isinstance(texto, str) or not texto.strip() or texto.lower() == 'nan':
        return None, None, "-"
    
    match = re.search(r'(\d{1,2}(?::\d{2})?)\s*(?:a|-)\s*(\d{1,2}(?::\d{2})?)', texto, re.IGNORECASE)
    t_ini, t_fin = None, None
    if match:
        s_ini, s_fin = match.groups()
        if ':' not in s_ini: s_ini += ":00"
        if ':' not in s_fin: s_fin += ":00"
        try:
            t_ini = datetime.strptime(s_ini, "%H:%M").time()
            t_fin = datetime.strptime(s_fin, "%H:%M").time()
        except ValueError:
            pass
            
    return t_ini, t_fin, texto.strip()

# Algoritmo de Optimización Automática (TSP - Vecino Más Cercano)
def optimizar_orden_paradas(depot_coord, clientes_coords):
    todos = [depot_coord] + clientes_coords + [depot_coord]
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in todos])
    url = f"http://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=duration"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("code") == "Ok":
            matrix = data['durations']
            n = len(todos)
            unvisited = set(range(1, n - 1))
            current = 0
            path = []
            
            while unvisited:
                next_stop = min(unvisited, key=lambda x: matrix[current][x])
                path.append(next_stop - 1)
                current = next_stop
                unvisited.remove(next_stop)
            return path
    except Exception:
        pass
    return list(range(len(clientes_coords)))

# Función para calcular ruta completa y tramos con OSRM
def calcular_ruta_osrm(coordenadas):
    if len(coordenadas) < 2:
        return 0, 0, []
    
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coordenadas])
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=false&steps=true"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("code") == "Ok":
            route = data['routes'][0]
            distancia_km = route['distance'] / 1000.0
            duracion_min = route['duration'] / 60.0
            
            tramos_min = []
            for leg in route.get('legs', []):
                tramos_min.append(leg['duration'] / 60.0)
                
            return round(distancia_km, 2), round(duracion_min, 1), tramos_min
    except Exception:
        pass
    return None, None, []

# Generador de PDF profesional de 2 páginas
def generar_pdf_hoja_ruta(ruta_df, cronograma_df, total_km, hora_salida_str, regreso_estimado, chofer_nombre):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    
    # ==========================================
    # PÁGINA 1: CRONOGRAMA Y VENTANAS HORARIAS
    # ==========================================
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font('Arial', 'B', 15)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(110, 7, 'Hoja de Ruta y Ventanas Horarias', 0, 0, 'L')
    
    pdf.set_font('Arial', '', 8)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(80, 4, f'Fecha: {datetime.now().strftime("%d/%m/%Y")}', 0, 1, 'R')
    pdf.cell(190, 4, f'Deposito: {DEPOT_DIRECCION}, {DEPOT_LOCALIDAD}', 0, 1, 'R')
    pdf.cell(190, 4, f'Chofer: {chofer_nombre}', 0, 1, 'R')
    
    pdf.set_xy(10, pdf.get_y() + 2)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 5, 'Ruta optimizada contemplando tiempos y restricciones horarias', 0, 1, 'L')
    pdf.ln(3)
    
    card_w = 44
    card_h = 16
    y_start = pdf.get_y()
    total_descarga_min = len(ruta_df) * 30
    metrics = [
        ("SALIDA DE DEPOSITO", f"{hora_salida_str} hs"),
        ("DISTANCIA RUTA", f"{total_km} km"),
        ("TIEMPO DESCARGA", f"{total_descarga_min} min ({len(ruta_df)} paradas)"),
        ("RETORNO LANUS", f"{regreso_estimado} hs")
    ]
    
    x_start = 10
    for i, (title, val) in enumerate(metrics):
        current_x = x_start + i * 49
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(current_x, y_start, card_w, card_h, 'DF')
        
        pdf.set_xy(current_x + 2, y_start + 2)
        pdf.set_font('Arial', 'B', 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(card_w - 4, 3, title, 0, 1, 'L')
        
        pdf.set_xy(current_x + 2, y_start + 7)
        pdf.set_font('Arial', 'B', 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(card_w - 4, 6, val, 0, 1, 'L')
        
    pdf.set_y(y_start + card_h + 6)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, '1. Cronograma Secuencial y Ventanas Horarias', 0, 1, 'L')
    pdf.ln(1)
    
    # Anchos ajustados: Cliente más corto (35mm), Horario más ancho (34mm) -> Total 190mm
    pdf.set_font('Arial', 'B', 8)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(15, 6, 'ETAPA', 1, 0, 'C', fill=True)
    pdf.cell(18, 6, 'N° CLI', 1, 0, 'C', fill=True)
    pdf.cell(35, 6, 'CLIENTE / FANTASIA', 1, 0, 'L', fill=True)
    pdf.cell(48, 6, 'DIRECCION Y LOCALIDAD', 1, 0, 'L', fill=True)
    pdf.cell(34, 6, 'HORARIO', 1, 0, 'C', fill=True)
    pdf.cell(20, 6, 'LLEGADA', 1, 0, 'C', fill=True)
    pdf.cell(20, 6, 'SALIDA', 1, 1, 'C', fill=True)
    
    pdf.set_font('Arial', '', 8)
    pdf.set_text_color(15, 23, 42)
    
    for idx, row in cronograma_df.iterrows():
        punto = str(row['Punto de Ruta'])
        dire = str(row['Dirección'])
        llegada = str(row['Llegada'])
        salida = str(row['Partida'])
        horario_str = str(row.get('Horario', '-'))
        
        if "Salida" in punto:
            etapa = "INICIO"
            n_cli = "-"
            nombre = "Depósito Lanús"
            horario_str = "-"
        elif "Regreso" in punto:
            etapa = "FIN"
            n_cli = "-"
            nombre = "Depósito Lanús"
            horario_str = "-"
        else:
            etapa = str(idx)
            row_idx = idx - 1
            if 0 <= row_idx < len(ruta_df):
                n_cli = str(ruta_df.iloc[row_idx].get('Nro', '')).replace('.0', '')
            else:
                n_cli = "-"
            nombre = str(row['Punto de Ruta']).split(":", 1)[-1].strip() if ":" in str(row['Punto de Ruta']) else str(row['Punto de Ruta'])
            
        pdf.cell(15, 5, etapa, 1, 0, 'C')
        pdf.cell(18, 5, n_cli, 1, 0, 'C')
        pdf.cell(35, 5, nombre[:18], 1, 0, 'L')  # Tronco ajustado para el ancho de 35mm
        pdf.cell(48, 5, dire[:30], 1, 0, 'L')
        pdf.cell(34, 5, horario_str[:22], 1, 0, 'C') # Ancho de 34mm para ver los horarios completos
        pdf.cell(20, 5, llegada, 1, 0, 'C')
        pdf.cell(20, 5, salida, 1, 1, 'C')
        
    pdf.ln(4)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, '2. Orden de Carga en Camion (LIFO)', 0, 1, 'L')
    pdf.set_font('Arial', 'I', 7)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 4, 'Ultimo en entregarse va al fondo del camion; primero en entregarse va cerca de la puerta.', 0, 1, 'L')
    pdf.ln(1)
    
    pdf.set_font('Arial', 'B', 8)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(15, 6, 'POS.', 1, 0, 'C', fill=True)
    pdf.cell(22, 6, 'N° CLIENTE', 1, 0, 'C', fill=True)
    pdf.cell(65, 6, 'CLIENTE / NOMBRE FANTASIA', 1, 0, 'L', fill=True)
    pdf.cell(88, 6, 'DIRECCION Y LOCALIDAD', 1, 1, 'L', fill=True)
    
    pdf.set_font('Arial', '', 8)
    pdf.set_text_color(15, 23, 42)
    lifo_pdf_df = ruta_df.iloc[::-1].reset_index(drop=True)
    for idx, row in lifo_pdf_df.iterrows():
        pos = str(idx + 1)
        nro = str(row.get('Nro', '')).replace('.0', '')
        cli = str(row.get('Cliente', ''))[:35]
        dir_loc = f"{row.get('Direccion', '')}, {row.get('Localidad', '')}"[:55]
        
        pdf.cell(15, 5, pos, 1, 0, 'C')
        pdf.cell(22, 5, nro, 1, 0, 'C')
        pdf.cell(65, 5, cli, 1, 0, 'L')
        pdf.cell(88, 5, dir_loc, 1, 1, 'L')

    # ==========================================
    # PÁGINA 2: TIRA DE REPARTO
    # ==========================================
    pdf.add_page()
    
    pdf.set_font('Arial', 'B', 14)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 7, 'TIRA DE REPARTO', 0, 1, 'C')
    pdf.ln(3)
    
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(30, 6, 'Chofer:', 0, 0, 'L')
    pdf.set_font('Arial', '', 9)
    pdf.cell(65, 6, '', 'B', 0, 'L')
    
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(30, 6, 'Fecha:', 0, 0, 'L')
    pdf.set_font('Arial', '', 9)
    pdf.cell(65, 6, datetime.now().strftime("%d/%m/%Y"), 'B', 1, 'L')
    
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(30, 6, 'Acompañante:', 0, 0, 'L')
    pdf.set_font('Arial', '', 9)
    pdf.cell(65, 6, '', 'B', 0, 'L')
    
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(30, 6, 'Camion:', 0, 0, 'L')
    pdf.set_font('Arial', '', 9)
    pdf.cell(65, 6, '', 'B', 1, 'L')
    pdf.ln(6)
    
    pdf.set_font('Arial', 'B', 8)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    
    pdf.cell(18, 7, 'Factura', 1, 0, 'C', fill=True)
    pdf.cell(48, 7, 'Cliente', 1, 0, 'L', fill=True)
    pdf.cell(25, 7, 'Horario', 1, 0, 'C', fill=True)
    pdf.cell(8, 7, 'C', 1, 0, 'C', fill=True)
    pdf.cell(8, 7, 'F', 1, 0, 'C', fill=True)
    pdf.cell(20, 7, 'Transporte', 1, 0, 'C', fill=True)
    pdf.cell(20, 7, 'H. Llegada', 1, 0, 'C', fill=True)
    pdf.cell(20, 7, 'H. Salida', 1, 0, 'C', fill=True)
    pdf.cell(23, 7, 'Observ.', 1, 1, 'C', fill=True)
    
    pdf.set_font('Arial', '', 8)
    pdf.set_text_color(15, 23, 42)
    
    for idx, row in ruta_df.iterrows():
        nombre_cliente = str(row.get('Cliente', ''))[:28]
        obs_texto = str(row.get('Observaciones', ''))
        _, _, horario_str = extraer_ventana_horaria(obs_texto)
        
        pdf.cell(18, 6, '', 1, 0, 'C')
        pdf.cell(48, 6, nombre_cliente, 1, 0, 'L')
        pdf.cell(25, 6, horario_str[:15], 1, 0, 'C')
        pdf.cell(8, 6, '', 1, 0, 'C')
        pdf.cell(8, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(23, 6, '', 1, 1, 'C')
        
    for _ in range(6):
        pdf.cell(18, 6, '', 1, 0, 'C')
        pdf.cell(48, 6, '', 1, 0, 'L')
        pdf.cell(25, 6, '', 1, 0, 'C')
        pdf.cell(8, 6, '', 1, 0, 'C')
        pdf.cell(8, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(20, 6, '', 1, 0, 'C')
        pdf.cell(23, 6, '', 1, 1, 'C')

    pdf_bytes = pdf.output()
    pdf_buffer = io.BytesIO(pdf_bytes)
    pdf_buffer.seek(0)
    return pdf_buffer

st.sidebar.header("⚙️ Configuración del Viaje")
hora_salida = st.sidebar.time_input("⏰ Hora de salida del camión:", value=time(8, 0))

if st.sidebar.button("🔄 Actualizar Datos desde Google Sheets"):
    st.cache_data.clear()
    st.sidebar.success("¡Datos actualizados correctamente!")

df, errores_carga = cargar_todos_los_clientes()

if errores_carga:
    with st.expander("⚠️ Ver estado técnico de carga por pestaña"):
        for pestaña, err in errores_carga.items():
            st.warning(f"Pestaña **{pestaña}**: {err}")

if df is not None and not df.empty:
    st.success(f"¡Base de datos unificada con éxito! ({len(df)} clientes totales cargados).")
    
    with st.expander("👀 Ver tabla completa unificada de clientes"):
        st.dataframe(df)

    st.subheader("📌 Selección de Clientes para el Viaje")

    def format_opcion(x):
        row = df.loc[x]
        chofer = row.get('Chofer_Origen', '')
        nro = str(row.get('Nro', '')).replace('.0', '')
        nombre = str(row.get('Cliente', ''))
        obs = str(row.get('Observaciones', ''))
        return f"[{chofer}] {nro} - {nombre} ({obs[:30]})"

    clientes_seleccionados = st.multiselect(
        "Busca por número de cliente, nombre u horario en observaciones:",
        options=df.index.tolist(),
        format_func=format_opcion
    )

    if clientes_seleccionados:
        ruta_df_inicial = df.loc[clientes_seleccionados].reset_index(drop=True)
        cols_mostrar = [c for c in ['Nro', 'Cliente', 'Fantasia', 'Direccion', 'Localidad', 'Observaciones', 'Chofer_Origen'] if c in ruta_df_inicial.columns]

        st.markdown("---")
        st.subheader("📊 Métricas, Optimización y Control de Horarios")
        
        TIEMPO_DESCARGA_MIN = 30
        col_dir = 'Direccion'
        col_loc = 'Localidad'

        if col_dir in df.columns and col_loc in df.columns:
            if st.button("🚀 Calcular Ruta Óptima y Validar Horarios"):
                with st.spinner("Geolocalizando, optimizando ruta y analizando ventanas horarias..."):
                    lat_depot, lon_depot = obtener_coordenadas(DEPOT_DIRECCION, DEPOT_LOCALIDAD)
                    
                    if not lat_depot or not lon_depot:
                        st.error("No se pudo geolocalizar el depósito de salida en Lanús.")
                    else:
                        clientes_coords = []
                        validos_indices = []
                        for idx, row in ruta_df_inicial.iterrows():
                            lat, lon = obtener_coordenadas(row[col_dir], row[col_loc])
                            if lat and lon:
                                clientes_coords.append((lat, lon))
                                validos_indices.append(idx)
                        
                        if len(clientes_coords) >= 1:
                            orden_optimizado = optimizar_orden_paradas((lat_depot, lon_depot), clientes_coords)
                            
                            indices_reordenados = [validos_indices[i] for i in orden_optimizado]
                            ruta_df = ruta_df_inicial.loc[indices_reordenados].reset_index(drop=True)
                            
                            coordenadas_ruta = [(lat_depot, lon_depot)]
                            for idx, row in ruta_df.iterrows():
                                lat, lon = obtener_coordenadas(row[col_dir], row[col_loc])
                                coordenadas_ruta.append((lat, lon))
                            coordenadas_ruta.append((lat_depot, lon_depot))
                            
                            km, duracion_conduccion_min, tramos = calcular_ruta_osrm(coordenadas_ruta)
                            
                            if km is not None:
                                dt_salida = datetime.combine(datetime.today(), hora_salida)
                                
                                cronograma_data = []
                                tiempo_acumulado = dt_salida
                                
                                cronograma_data.append({
                                    "Punto de Ruta": "Salida: Depósito Lanús",
                                    "Dirección": f"{DEPOT_DIRECCION}, {DEPOT_LOCALIDAD}",
                                    "Horario": "-",
                                    "Llegada": dt_salida.strftime("%H:%M"),
                                    "Actividad": "Salida del camión",
                                    "Partida": dt_salida.strftime("%H:%M")
                                })
                                
                                total_espera_min = 0
                                for i, (idx, row) in enumerate(ruta_df.iterrows()):
                                    tramo_min = tramos[i] if i < len(tramos) else 0
                                    tiempo_acumulado += timedelta(minutes=tramo_min)
                                    llegada_cliente = tiempo_acumulado
                                    
                                    obs_texto = str(row.get('Observaciones', ''))
                                    t_ini, t_fin, horario_str = extraer_ventana_horaria(obs_texto)
                                    
                                    actividad_txt = f"Descarga ({TIEMPO_DESCARGA_MIN} min)"
                                    if t_ini and t_fin:
                                        llegada_time = llegada_cliente.time()
                                        if llegada_time < t_ini:
                                            apertura_dt = datetime.combine(llegada_cliente.date(), t_ini)
                                            espera_min = (apertura_dt - llegada_cliente).total_seconds() / 60.0
                                            total_espera_min += espera_min
                                            tiempo_acumulado = apertura_dt
                                            llegada_cliente = apertura_dt
                                            actividad_txt += f" [Esperó apertura {t_ini.strftime('%H:%M')}]"
                                        elif llegada_time > t_fin:
                                            actividad_txt += f" ⚠️ TARDE (Cierra {t_fin.strftime('%H:%M')})"
                                    
                                    tiempo_acumulado += timedelta(minutes=TIEMPO_DESCARGA_MIN)
                                    partida_cliente = tiempo_acumulado
                                    
                                    nombre_cli = str(row.get('Cliente', f"Cliente {i+1}"))
                                    dir_cli = str(row[col_dir])
                                    
                                    cronograma_data.append({
                                        "Punto de Ruta": f"Parada {i+1}: {nombre_cli}",
                                        "Dirección": f"{dir_cli}",
                                        "Horario": horario_str,
                                        "Llegada": llegada_cliente.strftime("%H:%M"),
                                        "Actividad": actividad_txt,
                                        "Partida": partida_cliente.strftime("%H:%M")
                                    })
                                
                                if len(tramos) >= len(ruta_df) + 1:
                                    tramo_regreso = tramos[len(ruta_df)]
                                    tiempo_acumulado += timedelta(minutes=tramo_regreso)
                                    
                                cronograma_data.append({
                                    "Punto de Ruta": "Regreso: Depósito Lanús",
                                    "Dirección": f"{DEPOT_DIRECCION}, {DEPOT_LOCALIDAD}",
                                    "Horario": "-",
                                    "Llegada": tiempo_acumulado.strftime("%H:%M"),
                                    "Actividad": "Fin de recorrido / Retorno",
                                    "Partida": "-"
                                })
                                
                                tiempo_total_viaje_min = (tiempo_acumulado - dt_salida).total_seconds() / 60.0
                                total_descarga_min = len(ruta_df) * TIEMPO_DESCARGA_MIN
                                
                                m1, m2, m3, m4 = st.columns(4)
                                m1.metric("Distancia Total", f"{km} km")
                                m2.metric("Conducción + Esperas", f"{round(duracion_conduccion_min + total_espera_min)} min")
                                m3.metric("Tiempo Descarga", f"{total_descarga_min} min")
                                m4.metric("Regreso a Lanús", tiempo_acumulado.strftime("%H:%M hs"))
                                
                                st.markdown("---")
                                col1, col2 = st.columns(2)
                                with col1:
                                    st.subheader("🗺️ Orden de Entrega Óptimo")
                                    hoja_ruta_display = ruta_df[cols_mostrar].copy()
                                    hoja_ruta_display.index = hoja_ruta_display.index + 1
                                    st.dataframe(hoja_ruta_display)

                                with col2:
                                    st.subheader("📦 Orden de Carga en Camión (LIFO)")
                                    st.caption("Organizado automáticamente del fondo a la puerta según la ruta óptima.")
                                    lifo_df = ruta_df.iloc[::-1].reset_index(drop=True)
                                    lifo_display = lifo_df[cols_mostrar].copy()
                                    lifo_display.index = lifo_display.index + 1
                                    st.dataframe(lifo_display)
                                
                                st.markdown("### 🕒 Cronograma Secuencial y Control de Horarios")
                                cron_df = pd.DataFrame(cronograma_data)
                                st.dataframe(cron_df, use_container_width=True)
                                
                                choferes_unicos = ruta_df['Chofer_Origen'].unique()
                                chofer_str = ", ".join(choferes_unicos) if len(choferes_unicos) <= 2 else "Mixto / Varios"
                                
                                pdf_data = generar_pdf_hoja_ruta(ruta_df, cron_df, km, hora_salida.strftime("%H:%M"), tiempo_acumulado.strftime("%H:%M"), chofer_str)
                                st.download_button(
                                    label="📄 Descargar Hoja de Ruta, LIFO y Tira de Reparto en PDF",
                                    data=pdf_data,
                                    file_name=f"Tira_de_Reparto_Horarios_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                                    mime="application/pdf"
                                )
                            else:
                                st.error("No se pudo calcular la ruta óptima con el motor de ruteo.")
                        else:
                            st.warning("⚠️ No se pudieron geolocalizar suficientes direcciones para optimizar el circuito.")
        else:
            st.info("💡 Asegúrate de tener columnas de 'Dirección' y 'Localidad' reconocibles en tu planilla.")
else:
    st.warning("⚠️ No se pudieron cargar los datos.")
