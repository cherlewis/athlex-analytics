import streamlit as st
import fitdecode
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import pytz
import requests
import json
import time

# Configuración de página
st.set_page_config(page_title="COROS Pace 4 Analytics", layout="wide", page_icon="🏊")

st.title("🏃🏊 COROS Pace 4 - Analizador de Atletismo & Natación")

# -----------------------------------------------------------------------------
# DICCIONARIOS Y PARSERS DE NATACIÓN Y DEPORTES FIT
# -----------------------------------------------------------------------------

def parse_sport_name(sport_val):
    """Mapea códigos numéricos o texto del protocolo FIT al nombre del deporte."""
    if sport_val is None:
        return "Desconocido"
    if isinstance(sport_val, int):
        mapping = {1: "Carrera", 2: "Ciclismo", 5: "Natación", 11: "Transición", 17: "Gimnasio"}
        return mapping.get(sport_val, f"Deporte_{sport_val}")
    s_str = str(sport_val).lower()
    if "swim" in s_str or "natacion" in s_str or "piscina" in s_str:
        return "Natación"
    elif "run" in s_str or "carrera" in s_str:
        return "Carrera"
    elif "cycl" in s_str or "bici" in s_str or "bike" in s_str:
        return "Ciclismo"
    return s_str.capitalize()

def parse_swim_stroke(stroke_val):
    """Convierte el tipo de estilo de nado FIT a texto legible en español."""
    if stroke_val is None:
        return "Libre / Crawl"
    if isinstance(stroke_val, int):
        strokes = {
            0: "Libre / Crawl", 1: "Espalda", 2: "Braza", 
            3: "Mariposa", 4: "Técnica (Drill)", 5: "Mixto / Estilos", 6: "Estilos (IM)"
        }
        return strokes.get(stroke_val, "Libre / Crawl")
    s_str = str(stroke_val).lower()
    if "free" in s_str: return "Libre / Crawl"
    if "back" in s_str: return "Espalda"
    if "breast" in s_str: return "Braza"
    if "fly" in s_str or "butterfly" in s_str: return "Mariposa"
    if "drill" in s_str: return "Técnica"
    if "mixed" in s_str or "im" in s_str: return "Estilos"
    return str(stroke_val).capitalize()

def convert_to_madrid_time(dt):
    """Convierte cualquier fecha/hora UTC a la zona horaria de Madrid."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(pytz.timezone('Europe/Madrid'))

# -----------------------------------------------------------------------------
# FUNCIONES DE PROCESAMIENTO DE TELEMETRÍA Y FIT
# -----------------------------------------------------------------------------

def procesar_telemetria(df, es_natacion=False):
    """Limpia, convierte unidades y calcula métricas según el deporte (Carrera vs Natación)."""
    df = df.copy()
    
    # 1. Zona Horaria Madrid y Tiempo Relativo
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Europe/Madrid')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Europe/Madrid')
            
        tiempo_inicio = df['timestamp'].iloc[0]
        df['Tiempo_Segundos'] = (df['timestamp'] - tiempo_inicio).dt.total_seconds()
        
        def formato_tiempo(segs):
            horas = int(segs // 3600)
            mins = int((segs % 3600) // 60)
            segs_rest = int(segs % 60)
            if horas > 0:
                return f"{horas:02d}:{mins:02d}:{segs_rest:02d}"
            return f"{mins:02d}:{segs_rest:02d}"
            
        df['Tiempo_Formato'] = df['Tiempo_Segundos'].apply(formato_tiempo)

    # 2. Cálculo de Velocidad y Ritmo (Diferenciando Natación min/100m vs Carrera min/km)
    if 'speed' in df.columns:
        df['speed_kmh'] = df['speed'] * 3.6
        
        if es_natacion:
            df['ritmo_decimal'] = np.where(df['speed'] > 0.05, 1.66667 / df['speed'], np.nan)
        else:
            df['ritmo_decimal'] = np.where(df['speed'] > 0.5, 16.6667 / df['speed'], np.nan)
        
        df['ritmo_suavizado'] = df['ritmo_decimal'].rolling(window=10, min_periods=1).mean()
        
        def decimal_a_ritmo_texto(val):
            if pd.isna(val) or val > 30:
                return None
            mins = int(val)
            segs = int((val - mins) * 60)
            return f"{mins}:{segs:02d}"
            
        df['Ritmo_Texto'] = df['ritmo_suavizado'].apply(decimal_a_ritmo_texto)

    # 3. Frecuencia Cardíaca (Suavizada)
    if 'heart_rate' in df.columns:
        df['heart_rate_raw'] = df['heart_rate']
        df['heart_rate'] = df['heart_rate'].rolling(window=5, min_periods=1).mean()

    # 4. Relación Ritmo y FC / Coste Cardíaco
    if 'ritmo_suavizado' in df.columns and 'heart_rate' in df.columns:
        df['latidos_por_km'] = df['heart_rate'] * df['ritmo_suavizado']
        df['latidos_por_km_suavizado'] = df['latidos_por_km'].rolling(window=15, min_periods=1).mean()

    # 5. Coordenadas GPS (Solo si existen)
    if 'position_lat' in df.columns and 'position_long' in df.columns:
        df_valid_gps = df.dropna(subset=['position_lat', 'position_long'])
        if not df_valid_gps.empty:
            df['lat'] = df['position_lat'] * (180 / 2**31)
            df['lon'] = df['position_long'] * (180 / 2**31)

    # 6. Cadencia (Pasos en Carrera vs Brazadas en Piscina)
    if 'cadence' in df.columns:
        if es_natacion:
            df['cadence_spm'] = df['cadence']
        else:
            df['cadence_spm'] = np.where(df['cadence'] < 120, df['cadence'] * 2, df['cadence'])

    return df

def leer_fichero_fit(file_bytes, file_name):
    """Extrae records, laps, lengths y metadatos con soporte completo para Natación."""
    records = []
    laps = []
    lengths = []
    metadata = {
        'nombre_archivo': file_name,
        'deporte': 'Carrera',
        'es_natacion': False,
        'fecha_inicio': None,
        'duracion_total': 0,
        'distancia_total': 0,
        'calorias_totales': 0,
        'fc_media': 0,
        'temperatura_media': None,
        'swolf_medio': None,
        'largos_totales': 0,
        'estilo_principal': 'Libre / Crawl'
    }
    
    with fitdecode.FitReader(file_bytes) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage):
                if frame.name == 'session':
                    for field in frame.fields:
                        if field.name == 'sport' and field.value is not None:
                            metadata['deporte'] = parse_sport_name(field.value)
                            if metadata['deporte'] == 'Natación':
                                metadata['es_natacion'] = True
                        elif field.name == 'start_time' and field.value:
                            metadata['fecha_inicio'] = convert_to_madrid_time(field.value)
                        elif field.name == 'total_elapsed_time' and field.value:
                            metadata['duracion_total'] = field.value
                        elif field.name == 'total_distance' and field.value:
                            metadata['distancia_total'] = field.value
                        elif field.name == 'total_calories' and field.value:
                            metadata['calorias_totales'] = field.value
                        elif field.name == 'avg_heart_rate' and field.value:
                            metadata['fc_media'] = field.value
                        elif field.name == 'avg_temperature' and field.value:
                            metadata['temperatura_media'] = field.value
                        elif field.name == 'avg_swolf' and field.value:
                            metadata['swolf_medio'] = field.value
                        elif field.name == 'num_lengths' and field.value:
                            metadata['largos_totales'] = field.value

                elif frame.name == 'lap':
                    lap_data = {}
                    for field in frame.fields:
                        if field.value is not None:
                            lap_data[field.name] = field.value
                    laps.append(lap_data)

                elif frame.name == 'length':
                    len_data = {}
                    for field in frame.fields:
                        if field.value is not None:
                            len_data[field.name] = field.value
                    lengths.append(len_data)

                elif frame.name == 'record':
                    data = {}
                    for field in frame.fields:
                        if field.value is not None:
                            data[field.name] = field.value
                    records.append(data)

    df_records = pd.DataFrame(records)
    if not df_records.empty:
        df_records = procesar_telemetria(df_records, es_natacion=metadata['es_natacion'])
        if metadata['fecha_inicio'] is None and 'timestamp' in df_records.columns:
            metadata['fecha_inicio'] = df_records['timestamp'].iloc[0]

    df_laps = pd.DataFrame(laps)
    df_lengths = pd.DataFrame(lengths)
    
    if not metadata['es_natacion']:
        metadata['distancia_total'] = metadata['distancia_total'] / 1000.0

    return df_records, df_laps, df_lengths, metadata

# -----------------------------------------------------------------------------
# CÁLCULOS FISIOLÓGICOS Y DIAGNÓSTICO
# -----------------------------------------------------------------------------

def calcular_factor_eficiencia(df, es_natacion=False):
    """Calcula el Efficiency Factor (EF) = Velocidad (m/min) / FC Promedio."""
    if df.empty or 'speed' not in df.columns or 'heart_rate' not in df.columns:
        return None, None
    
    min_vel = 0.05 if es_natacion else 0.5
    df_val = df.dropna(subset=['speed', 'heart_rate'])
    df_val = df_val[df_val['speed'] > min_vel]
    
    if df_val.empty:
        return None, None
    
    velocidad_m_min = df_val['speed'].mean() * 60
    fc_media = df_val['heart_rate'].mean()
    
    ef = velocidad_m_min / fc_media if fc_media > 0 else 0
    
    mitad = len(df_val) // 2
    df_h1 = df_val.iloc[:mitad]
    df_h2 = df_val.iloc[mitad:]
    
    ef_h1 = (df_h1['speed'].mean() * 60) / df_h1['heart_rate'].mean() if not df_h1.empty and df_h1['heart_rate'].mean() > 0 else 0
    ef_h2 = (df_h2['speed'].mean() * 60) / df_h2['heart_rate'].mean() if not df_h2.empty and df_h2['heart_rate'].mean() > 0 else 0
    
    deriva_porcentaje = ((ef_h1 - ef_h2) / ef_h1) * 100 if ef_h1 > 0 else 0
    
    return round(ef, 3), round(deriva_porcentaje, 1)

def diagnosticar_comparativa(data1, data2):
    """Genera reglas explícitas comparando dos entrenamientos."""
    df1, meta1 = data1['df'], data1['meta']
    df2, meta2 = data2['df'], data2['meta']
    es_nat = meta1['es_natacion'] or meta2['es_natacion']
    
    ef1, drift1 = calcular_factor_eficiencia(df1, es_natacion=es_nat)
    ef2, drift2 = calcular_factor_eficiencia(df2, es_natacion=es_nat)
    
    diagnosticos = []
    
    if ef1 and ef2:
        diff_ef = ((ef2 - ef1) / ef1) * 100
        if diff_ef < -2.0:
            diagnosticos.append(f"⚠️ **Caída de Eficiencia Aeróbica:** Tu Factor de Eficiencia ($EF$) cayó un **{abs(diff_ef):.1f}%** (de {ef1} a {ef2}). Produces menos velocidad por latido.")
        elif diff_ef > 2.0:
            diagnosticos.append(f"🎉 **Mejora de Eficiencia:** Tu Factor de Eficiencia ($EF$) subió un **{diff_ef:.1f}%**. ¡Mayor eficiencia metabólica!")

    swolf1, swolf2 = meta1.get('swolf_medio'), meta2.get('swolf_medio')
    if swolf1 and swolf2:
        diff_swolf = swolf2 - swolf1
        if diff_swolf >= 2:
            diagnosticos.append(f"🏊 **Empeoramiento de SWOLF (+{diff_swolf} ptos):** Necesitaste más tiempo o más brazadas por largo. Tu técnica fue menos eficaz.")
        elif diff_swolf <= -2:
            diagnosticos.append(f"🏊 **Mejora de SWOLF ({diff_swolf} ptos):** ¡Excelente técnica! Has nadado más deslizante reduciendo tiempo/brazadas.")

    temp1 = meta1.get('temperatura_media') or (df1['temperature'].mean() if 'temperature' in df1.columns else None)
    temp2 = meta2.get('temperatura_media') or (df2['temperature'].mean() if 'temperature' in df2.columns else None)
    if temp1 is not None and temp2 is not None:
        diff_temp = temp2 - temp1
        if abs(diff_temp) >= 2.5:
            diagnosticos.append(f"🌡️ **Diferencia Térmica ({diff_temp:+.1f}°C):** Cambios de temperatura afectan al gasto cardíaco.")

    if drift2 is not None and drift2 > 5.0:
        diagnosticos.append(f"🔥 **Desacople Aeróbico Elevado ({drift2}%):** En la 2ª mitad del entrenamiento tu pulso subió en relación al ritmo.")

    if not diagnosticos:
        diagnosticos.append("💤 **Fatiga Acumulada:** Condiciones similares, la variación en el rendimiento suele responder a la carga de fatiga previa o descanso.")

    return diagnosticos, ef1, ef2, drift1, drift2

def consultar_gemini_coach(prompt_texto):
    """Petición a la API de Gemini 2.5 Flash con retroceso exponencial."""
    api_key = ""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt_texto}]}],
        "systemInstruction": {
            "parts": [{
                "text": "Eres un entrenador de atletismo y natación de alto rendimiento experto en métricas de COROS y Garmin. Responde con rigor científico y tono motivador."
            }]
        }
    }
    
    delays = [1, 2, 4, 8, 16]
    for delay in delays:
        try:
            response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if response.status_code == 200:
                result = response.json()
                return result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', "No se pudo generar respuesta.")
        except Exception:
            pass
        time.sleep(delay)
    return "Error al conectar con el servidor del Entrenador IA. Inténtalo de nuevo más tarde."

# -----------------------------------------------------------------------------
# INTERFAZ PRINCIPAL DE STREAMLIT
# -----------------------------------------------------------------------------

uploaded_files = st.file_uploader("Arrastra aquí tus archivos .FIT de COROS (Carrera, Bici o Natación)", type=["fit"], accept_multiple_files=True)

if uploaded_files:
    datos_cargados = []
    
    for file in uploaded_files:
        try:
            bytes_data = file.read()
            df_rec, df_laps, df_lengths, meta = leer_fichero_fit(bytes_data, file.name)
            if not df_rec.empty or not df_laps.empty:
                datos_cargados.append({'df': df_rec, 'laps': df_laps, 'lengths': df_lengths, 'meta': meta})
        except Exception as e:
            st.error(f"Error al procesar {file.name}: {e}")

    if datos_cargados:
        st.success(f"¡Se han procesado {len(datos_cargados)} archivo(s) correctamente!")
        
        tab_ind, tab_comp, tab_diag = st.tabs(["📊 Sesión Individual", "📈 Comparativa Superpuesta", "🧠 Diagnóstico e IA Coach"])

        # =====================================================================
        # PESTAÑA 1: ANÁLISIS INDIVIDUAL
        # =====================================================================
        with tab_ind:
            opciones = [f"{'🏊' if d['meta']['es_natacion'] else '🏃'} {d['meta']['deporte']} - {d['meta']['fecha_inicio'].strftime('%d/%m/%Y %H:%M') if d['meta']['fecha_inicio'] else d['meta']['nombre_archivo']}" for d in datos_cargados]
            idx_sel = st.selectbox("Selecciona la actividad a inspeccionar:", range(len(opciones)), format_func=lambda x: opciones[x])
            
            sel = datos_cargados[idx_sel]
            df = sel['df']
            meta = sel['meta']
            laps = sel['laps']
            lengths = sel['lengths']
            es_nat = meta['es_natacion']

            # Cálculo de Eficiencia para la sesión individual
            ef_valor, drift_valor = calcular_factor_eficiencia(df, es_natacion=es_nat)

            # Indicador de Deporte
            if es_nat:
                st.info("🏊 **Actividad de Natación Detectada:** Las métricas y gráficos se han adaptado automáticamente a **min/100m**, **SWOLF** y **brazadas**.")
            
            # Métricas Clave
            st.markdown(f"### 📍 {meta['deporte']} - {meta['fecha_inicio'].strftime('%d de %B, %Y a las %H:%M') if meta['fecha_inicio'] else ''}")
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            
            if es_nat:
                col1.metric("Distancia", f"{int(meta['distancia_total'])} m")
            else:
                col1.metric("Distancia", f"{meta['distancia_total']:.2f} km")
                
            dur_mins = int(meta['duracion_total'] // 60)
            dur_segs = int(meta['duracion_total'] % 60)
            col2.metric("Duración", f"{dur_mins}m {dur_segs}s")
            col3.metric("FC Promedio", f"{int(meta['fc_media'])} ppm" if meta['fc_media'] else "N/A")
            col4.metric("EF (Eficiencia)", f"{ef_valor}" if ef_valor is not None else "N/A", help="Factor de Eficiencia: Velocidad (m/min) / FC Media")
            
            if es_nat:
                col5.metric("SWOLF Medio", f"{meta['swolf_medio']}" if meta['swolf_medio'] else "N/A")
                col6.metric("Largos Totales", f"{meta['largos_totales']}" if meta['largos_totales'] else "N/A")
            else:
                col5.metric("Calorías", f"{int(meta['calorias_totales'])} kcal")
                col6.metric("Temperatura", f"{meta['temperatura_media']} °C" if meta['temperatura_media'] is not None else "N/A")

            # Marcadores de Vueltas (Laps)
            vueltas_tiempos = []
            if not laps.empty and 'start_time' in laps.columns and not df.empty and 'timestamp' in df.columns:
                inicio_act = df['timestamp'].iloc[0]
                for _, lap in laps.iterrows():
                    if 'start_time' in lap and pd.notna(lap['start_time']):
                        t_lap = convert_to_madrid_time(lap['start_time'])
                        segs = (t_lap - inicio_act).total_seconds()
                        if segs > 0:
                            vueltas_tiempos.append(segs)

            # Gráfica Principal: Ritmo y FC
            if not df.empty and 'ritmo_suavizado' in df.columns:
                etiqueta_ritmo = "Ritmo (min/100m)" if es_nat else "Ritmo (min/km)"
                st.subheader(f"📉 {etiqueta_ritmo} y Frecuencia Cardíaca (ppm)")
                
                fig = go.Figure()

                fig.add_trace(go.Scatter(
                    x=df['Tiempo_Segundos'], y=df['ritmo_suavizado'],
                    mode='lines', name=etiqueta_ritmo,
                    line=dict(color='#00CC96', width=2),
                    customdata=df['Ritmo_Texto'],
                    hovertemplate=f"Tiempo: %{{text}}<br>{etiqueta_ritmo}: %{{customdata}}<extra></extra>"
                ))

                if 'heart_rate' in df.columns and not df['heart_rate'].isna().all():
                    fig.add_trace(go.Scatter(
                        x=df['Tiempo_Segundos'], y=df['heart_rate'],
                        mode='lines', name='FC (ppm)', yaxis='y2',
                        line=dict(color='#EF553B', width=2),
                        hovertemplate="FC: %{y:.0f} ppm<extra></extra>"
                    ))

                for v_seg in vueltas_tiempos:
                    fig.add_vline(x=v_seg, line_width=1, line_dash="dash", line_color="gray")

                paso = max(1, len(df) // 8)
                tick_vals = df['Tiempo_Segundos'].iloc[::paso].tolist()
                tick_texts = df['Tiempo_Formato'].iloc[::paso].tolist()

                fig.update_layout(
                    xaxis=dict(title="Tiempo de Actividad", tickvals=tick_vals, ticktext=tick_texts),
                    yaxis=dict(title=etiqueta_ritmo, autorange="reversed"),
                    yaxis2=dict(title="FC (ppm)", overlaying='y', side='right'),
                    hovermode="x unified", legend=dict(orientation="h", y=1.1)
                )
                st.plotly_chart(fig, use_container_width=True)

            # RELACIÓN RITMO - FRECUENCIA CARDÍACA (Coste Cardíaco & Dispersión)
            if not es_nat and 'latidos_por_km_suavizado' in df.columns:
                st.markdown("---")
                st.subheader("❤️⚡ Relación Ritmo / Frecuencia Cardíaca (Eficiencia en Carrera)")
                
                col_ef1, col_ef2 = st.columns(2)
                
                with col_ef1:
                    st.markdown("**Coste Cardíaco en el Tiempo (Latidos por Kilómetro)**")
                    fig_lpk = go.Figure()
                    fig_lpk.add_trace(go.Scatter(
                        x=df['Tiempo_Segundos'], y=df['latidos_por_km_suavizado'],
                        mode='lines', name='Coste (latidos/km)',
                        line=dict(color='#FFA15A', width=2),
                        hovertemplate="Coste: %{y:.0f} latidos/km<extra></extra>"
                    ))
                    for v_seg in vueltas_tiempos:
                        fig_lpk.add_vline(x=v_seg, line_width=1, line_dash="dash", line_color="gray")
                    fig_lpk.update_layout(
                        xaxis=dict(title="Tiempo", tickvals=tick_vals, ticktext=tick_texts),
                        yaxis=dict(title="Latidos consumidos / km"),
                        showlegend=False
                    )
                    st.plotly_chart(fig_lpk, use_container_width=True)

                with col_ef2:
                    st.markdown("**Dispersión: Ritmo vs Frecuencia Cardíaca**")
                    df_scatter = df.dropna(subset=['ritmo_suavizado', 'heart_rate'])
                    df_scatter = df_scatter[(df_scatter['ritmo_suavizado'] >= 3.0) & (df_scatter['ritmo_suavizado'] <= 12.0)]
                    
                    fig_scat = px.scatter(
                        df_scatter, x='heart_rate', y='ritmo_suavizado',
                        color='Tiempo_Segundos',
                        labels={'heart_rate': 'Frecuencia Cardíaca (ppm)', 'ritmo_suavizado': 'Ritmo (min/km)', 'Tiempo_Segundos': 'Segundos'},
                        color_continuous_scale='Viridis'
                    )
                    fig_scat.update_layout(yaxis=dict(autorange="reversed"))
                    st.plotly_chart(fig_scat, use_container_width=True)

            # Cadencia de Pasos/Brazadas
            if not df.empty and 'cadence_spm' in df.columns and not df['cadence_spm'].isna().all():
                lbl_cad = "🏊 Cadencia de Brazadas (br/min)" if es_nat else "👟 Cadencia de Zancada (ppm)"
                st.subheader(lbl_cad)
                fig_cad = px.line(df, x='Tiempo_Segundos', y='cadence_spm', color_discrete_sequence=['#AB63FA'])
                fig_cad.update_layout(xaxis_title="Tiempo", yaxis_title="Frecuencia")
                st.plotly_chart(fig_cad, use_container_width=True)

            # Mapa GPS Ocultable (Solo si hay coordenadas de exterior)
            if 'lat' in df.columns and 'lon' in df.columns and not df['lat'].dropna().empty:
                st.subheader("🗺️ Trazado de Ruta GPS")
                if st.toggle("Mostrar Mapa de Ruta", value=True, key=f"tog_map_{idx_sel}"):
                    df_mapa = df.dropna(subset=['lat', 'lon'])
                    fig_map = px.line_mapbox(df_mapa, lat="lat", lon="lon", zoom=13, height=400)
                    fig_map.update_traces(line=dict(width=3, color="red"))
                    fig_map.update_layout(mapbox_style="open-street-map", margin={"r":0,"t":0,"l":0,"b":0})
                    st.plotly_chart(fig_map, use_container_width=True)

            # Tabla de Vueltas / Desglose por Km o Largos
            if not laps.empty:
                st.subheader("⏱️ Desglose por Vueltas / Intervalos")
                cols_mostrar = [c for c in ['lap_index', 'total_elapsed_time', 'total_distance', 'avg_speed', 'avg_heart_rate', 'avg_swolf'] if c in laps.columns]
                df_laps_show = laps[cols_mostrar].copy()
                
                if 'total_distance' in df_laps_show.columns:
                    if es_nat:
                        df_laps_show['Distancia (m)'] = df_laps_show['total_distance'].round(0)
                    else:
                        df_laps_show['Distancia (km)'] = (df_laps_show['total_distance'] / 1000.0).round(2)
                        
                if 'avg_speed' in df_laps_show.columns:
                    if es_nat:
                        df_laps_show['Ritmo (min/100m)'] = df_laps_show['avg_speed'].apply(
                            lambda x: f"{int(1.66667/x)}:{int(((1.66667/x)%1)*60):02d}" if x > 0.05 else "-"
                        )
                    else:
                        df_laps_show['Ritmo (min/km)'] = df_laps_show['avg_speed'].apply(
                            lambda x: f"{int(16.6667/x)}:{int(((16.6667/x)%1)*60):02d}" if x > 0.5 else "-"
                        )
                st.dataframe(df_laps_show, use_container_width=True)

        # =====================================================================
        # PESTAÑA 2: COMPARATIVA MULTI-SESIÓN
        # =====================================================================
        with tab_comp:
            st.subheader("🔀 Superposición de Entrenamientos")
            if len(datos_cargados) < 2:
                st.info("Sube 2 o más archivos .FIT para poder compararlos en la misma gráfica.")
            else:
                fig_comp_ritmo = go.Figure()
                fig_comp_fc = go.Figure()

                for i, d in enumerate(datos_cargados):
                    nombre = d['meta']['fecha_inicio'].strftime('%d/%m %H:%M') if d['meta']['fecha_inicio'] else d['meta']['nombre_archivo']
                    lbl_nat = " (Piscina)" if d['meta']['es_natacion'] else ""
                    
                    if 'ritmo_suavizado' in d['df'].columns and not d['df']['ritmo_suavizado'].isna().all():
                        fig_comp_ritmo.add_trace(go.Scatter(
                            x=d['df']['Tiempo_Segundos'], y=d['df']['ritmo_suavizado'],
                            mode='lines', name=f"Ritmo{lbl_nat}: {nombre}"
                        ))
                    if 'heart_rate' in d['df'].columns and not d['df']['heart_rate'].isna().all():
                        fig_comp_fc.add_trace(go.Scatter(
                            x=d['df']['Tiempo_Segundos'], y=d['df']['heart_rate'],
                            mode='lines', name=f"FC: {nombre}"
                        ))

                fig_comp_ritmo.update_layout(title="Comparativa de Ritmos", yaxis=dict(autorange="reversed"))
                fig_comp_fc.update_layout(title="Comparativa de Pulsaciones (ppm)")

                st.plotly_chart(fig_comp_ritmo, use_container_width=True)
                st.plotly_chart(fig_comp_fc, use_container_width=True)

        # =====================================================================
        # PESTAÑA 3: DIAGNÓSTICO DE EFICIENCIA E IA COACH
        # =====================================================================
        with tab_diag:
            st.subheader("🧠 Análisis Fisiológico y Razonamiento de Rendimiento")
            
            if len(datos_cargados) < 2:
                st.warning("Para comparar dos entrenamientos, debes subir **al menos 2 archivos .FIT**.")
                if len(datos_cargados) == 1:
                    d1 = datos_cargados[0]
                    ef, drift = calcular_factor_eficiencia(d1['df'], es_natacion=d1['meta']['es_natacion'])
                    st.info(f"**Factor de Eficiencia ($EF$):** `{ef}` | **Deriva Cardíaca ($Pw:HR$):** `{drift}%`")
            else:
                c1, c2 = st.columns(2)
                with c1:
                    s1_idx = st.selectbox("Sesión de Referencia:", range(len(opciones)), index=0, key="s1_diag")
                with c2:
                    s2_idx = st.selectbox("Sesión a Analizar:", range(len(opciones)), index=1 if len(opciones)>1 else 0, key="s2_diag")

                data1 = datos_cargados[s1_idx]
                data2 = datos_cargados[s2_idx]

                diagnosticos, ef1, ef2, drift1, drift2 = diagnosticar_comparativa(data1, data2)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("EF Sesión Ref.", f"{ef1}")
                m2.metric("EF Sesión Analizada", f"{ef2}", delta=f"{round(((ef2-ef1)/ef1)*100, 1)}%" if ef1 and ef2 else None)
                m3.metric("Deriva Cardíaca S1", f"{drift1}%")
                m4.metric("Deriva Cardíaca S2", f"{drift2}%")

                st.markdown("---")
                st.markdown("#### 📋 Informe de Diagnóstico Automático")
                for diag in diagnosticos:
                    st.markdown(f"- {diag}")

                st.markdown("---")
                st.markdown("#### 🤖 Entrenador IA Personalizado (Google Gemini)")
                
                if st.button("✨ Preguntar al Entrenador IA Gemini"):
                    with st.spinner("Analizando la telemetría, técnica de nado/carrera y derivadas de esfuerzo..."):
                        prompt = f"""
                        Analiza estas dos sesiones .FIT del atleta:
                        
                        SESIÓN 1 (Referencia):
                        - Deporte: {data1['meta']['deporte']}
                        - Fecha: {data1['meta']['fecha_inicio']}
                        - Distancia: {data1['meta']['distancia_total']}
                        - FC Media: {data1['meta']['fc_media']} ppm
                        - Factor de Eficiencia (EF): {ef1}
                        - Deriva Cardíaca: {drift1}%
                        - SWOLF: {data1['meta'].get('swolf_medio')}
                        
                        SESIÓN 2 (A analizar):
                        - Deporte: {data2['meta']['deporte']}
                        - Fecha: {data2['meta']['fecha_inicio']}
                        - Distancia: {data2['meta']['distancia_total']}
                        - FC Media: {data2['meta']['fc_media']} ppm
                        - Factor de Eficiencia (EF): {ef2}
                        - Deriva Cardíaca: {drift2}%
                        - SWOLF: {data2['meta'].get('swolf_medio')}
                        
                        Diagnósticos automáticos:
                        {json.dumps(diagnosticos, ensure_ascii=False)}
                        
                        Resume qué ha ocurrido a nivel de rendimiento, técnica o respuesta cardíaca y da recomendaciones.
                        """
                        
                        respuesta_ia = consultar_gemini_coach(prompt)
                        st.markdown(f"```\n{respuesta_ia}\n```")
                        
