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
st.set_page_config(page_title="COROS Pace 4 Analytics", layout="wide", page_icon="🏃")

st.title("🏃 COROS Pace 4 - Analizador de Entrenamiento & Inteligencia Aeróbica")

# -----------------------------------------------------------------------------
# FUNCIONES AUXILIARES DE PROCESAMIENTO FIT Y TELEMETRÍA
# -----------------------------------------------------------------------------

def convert_to_madrid_time(dt):
    """Convierte cualquier fecha/hora UTC a la zona horaria de Madrid."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(pytz.timezone('Europe/Madrid'))

def procesar_telemetria(df):
    """Limpia, convierte unidades, ajusta horas y calcula métricas de ritmo y suavizado."""
    df = df.copy()
    
    # 1. Zona Horaria Madrid
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Europe/Madrid')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Europe/Madrid')
            
        tiempo_inicio = df['timestamp'].iloc[0]
        df['Tiempo_Segundos'] = (df['timestamp'] - tiempo_inicio).dt.total_seconds()
        
        # Formato de tiempo relativo (MM:SS o HH:MM:SS)
        def formato_tiempo(segs):
            horas = int(segs // 3600)
            mins = int((segs % 3600) // 60)
            segs_rest = int(segs % 60)
            if horas > 0:
                return f"{horas:02d}:{mins:02d}:{segs_rest:02d}"
            return f"{mins:02d}:{segs_rest:02d}"
            
        df['Tiempo_Formato'] = df['Tiempo_Segundos'].apply(formato_tiempo)

    # 2. Velocidad (m/s) -> Ritmo (min/km) y Velocidad (km/h)
    if 'speed' in df.columns:
        df['speed_kmh'] = df['speed'] * 3.6
        # Evitar división por cero
        df['ritmo_decimal'] = np.where(df['speed'] > 0.5, 16.6667 / df['speed'], np.nan)
        
        # Suavizado de ritmo (Ventana de 10 segundos)
        df['ritmo_suavizado'] = df['ritmo_decimal'].rolling(window=10, min_periods=1).mean()
        
        def decimal_a_min_km(val):
            if pd.isna(val) or val > 20: # Filtrar ritmos demasiado lentos o paradas
                return None
            mins = int(val)
            segs = int((val - mins) * 60)
            return f"{mins}:{segs:02d}"
            
        df['Ritmo_Texto'] = df['ritmo_suavizado'].apply(decimal_a_min_km)

    # 3. Suavizado de Frecuencia Cardíaca (Ventana de 5 segundos)
    if 'heart_rate' in df.columns:
        df['heart_rate_raw'] = df['heart_rate']
        df['heart_rate'] = df['heart_rate'].rolling(window=5, min_periods=1).mean()

    # 4. Ajuste de Coordenadas GPS
    if 'position_lat' in df.columns and 'position_long' in df.columns:
        df['lat'] = df['position_lat'] * (180 / 2**31)
        df['lon'] = df['position_long'] * (180 / 2**31)

    # 5. Cadencia
    if 'cadence' in df.columns:
        # Algunos archivos graban medio paso, ajustamos si es necesario
        df['cadence_spm'] = np.where(df['cadence'] < 120, df['cadence'] * 2, df['cadence'])

    return df

def leer_fichero_fit(file_bytes, file_name):
    """Extrae records, laps y metadatos utilizando fitdecode."""
    records = []
    laps = []
    metadata = {
        'nombre_archivo': file_name,
        'deporte': 'Carrera',
        'fecha_inicio': None,
        'duracion_total': 0,
        'distancia_total': 0,
        'calorias_totales': 0,
        'fc_media': 0,
        'temperatura_media': None
    }
    
    with fitdecode.FitReader(file_bytes) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage):
                # Extraer Sesión General
                if frame.name == 'session':
                    for field in frame.fields:
                        if field.name == 'sport' and field.value:
                            metadata['deporte'] = str(field.value).capitalize()
                        elif field.name == 'start_time' and field.value:
                            metadata['fecha_inicio'] = convert_to_madrid_time(field.value)
                        elif field.name == 'total_elapsed_time' and field.value:
                            metadata['duracion_total'] = field.value
                        elif field.name == 'total_distance' and field.value:
                            metadata['distancia_total'] = field.value / 1000.0
                        elif field.name == 'total_calories' and field.value:
                            metadata['calorias_totales'] = field.value
                        elif field.name == 'avg_heart_rate' and field.value:
                            metadata['fc_media'] = field.value
                        elif field.name == 'avg_temperature' and field.value:
                            metadata['temperatura_media'] = field.value
                
                # Extraer Vueltas / Fases (Laps)
                elif frame.name == 'lap':
                    lap_data = {}
                    for field in frame.fields:
                        if field.value is not None:
                            lap_data[field.name] = field.value
                    laps.append(lap_data)

                # Extraer Puntos de Telemetría (Records)
                elif frame.name == 'record':
                    data = {}
                    for field in frame.fields:
                        if field.value is not None:
                            data[field.name] = field.value
                    records.append(data)

    df_records = pd.DataFrame(records)
    if not df_records.empty:
        df_records = procesar_telemetria(df_records)
        if metadata['fecha_inicio'] is None and 'timestamp' in df_records.columns:
            metadata['fecha_inicio'] = df_records['timestamp'].iloc[0]

    df_laps = pd.DataFrame(laps)
    return df_records, df_laps, metadata

# -----------------------------------------------------------------------------
# CÁLCULOS FISIOLÓGICOS Y DIAGNÓSTICO
# -----------------------------------------------------------------------------

def calcular_factor_eficiencia(df):
    """Calcula el Efficiency Factor (EF) = Velocidad (m/min) / FC Promedio."""
    if 'speed' not in df.columns or 'heart_rate' not in df.columns:
        return None, None
    
    df_val = df.dropna(subset=['speed', 'heart_rate'])
    df_val = df_val[df_val['speed'] > 0.5] # Filtrar paradas
    
    if df_val.empty:
        return None, None
    
    velocidad_m_min = df_val['speed'].mean() * 60
    fc_media = df_val['heart_rate'].mean()
    
    ef = velocidad_m_min / fc_media if fc_media > 0 else 0
    
    # Calcular Deriva Cardíaca (1ª mitad vs 2ª mitad)
    mitad = len(df_val) // 2
    df_h1 = df_val.iloc[:mitad]
    df_h2 = df_val.iloc[mitad:]
    
    ef_h1 = (df_h1['speed'].mean() * 60) / df_h1['heart_rate'].mean()
    ef_h2 = (df_h2['speed'].mean() * 60) / df_h2['heart_rate'].mean()
    
    deriva_porcentaje = ((ef_h1 - ef_h2) / ef_h1) * 100 if ef_h1 > 0 else 0
    
    return round(ef, 3), round(deriva_porcentaje, 1)

def diagnosticar_comparativa(data1, data2):
    """Genera reglas explícitas comparando dos entrenamientos."""
    df1, meta1 = data1['df'], data1['meta']
    df2, meta2 = data2['df'], data2['meta']
    
    ef1, drift1 = calcular_factor_eficiencia(df1)
    ef2, drift2 = calcular_factor_eficiencia(df2)
    
    diagnosticos = []
    
    # Detección de pérdida de eficiencia
    if ef1 and ef2:
        diff_ef = ((ef2 - ef1) / ef1) * 100
        if diff_ef < -2.0:
            diagnosticos.append(f"⚠️ **Caída de Eficiencia Aeróbica:** Tu Factor de Eficiencia ($EF$) cayó un **{abs(diff_ef):.1f}%** (de {ef1} a {ef2}). Produces menos velocidad por cada latido.")
        elif diff_ef > 2.0:
            diagnosticos.append(f"🎉 **Mejora de Eficiencia:** Tu Factor de Eficiencia ($EF$) subió un **{diff_ef:.1f}%**. ¡Estás más en forma o mejor recuperado!")

    # Factor 1: Temperatura
    temp1 = meta1.get('temperatura_media') or (df1['temperature'].mean() if 'temperature' in df1.columns else None)
    temp2 = meta2.get('temperatura_media') or (df2['temperature'].mean() if 'temperature' in df2.columns else None)
    if temp1 is not None and temp2 is not None:
        diff_temp = temp2 - temp1
        if diff_temp >= 3.0:
            diagnosticos.append(f"🌡️ **Estrés Térmico:** La sesión 2 fue **{diff_temp:.1f}°C más calurosa**. El calor aumenta la vasodilatación cutánea y eleva el pulso entre 3 y 8 ppm sin aumentar el ritmo.")

    # Factor 2: Desnivel Acumulado
    if 'altitude' in df1.columns and 'altitude' in df2.columns:
        desnivel1 = df1['altitude'].diff().clip(lower=0).sum()
        desnivel2 = df2['altitude'].diff().clip(lower=0).sum()
        diff_desnivel = desnivel2 - desnivel1
        if diff_desnivel > 25:
            diagnosticos.append(f"🏔️ **Terreno con mayor Desnivel:** La sesión 2 acumuló **+{int(diff_desnivel)}m de subida**. Las pendientes incrementan el coste metabólico y bajan el ritmo promedio.")

    # Factor 3: Cadencia de Zancada
    if 'cadence_spm' in df1.columns and 'cadence_spm' in df2.columns:
        cad1 = df1['cadence_spm'].mean()
        cad2 = df2['cadence_spm'].mean()
        diff_cad = cad2 - cad1
        if diff_cad <= -3.0:
            diagnosticos.append(f"👟 **Caída de Cadencia Biomecánica:** Bajaste tu cadencia en **{abs(int(diff_cad))} ppm**. Una zancada más lenta/pesada aumenta la fuerza de impacto y fatiga los músculos antes.")

    # Factor 4: Deriva Cardíaca en la 2ª mitad
    if drift2 is not None and drift2 > 5.0:
        diagnosticos.append(f"🔥 **Desacople Aeróbico Elevado ({drift2}%):** En la 2ª mitad de la sesión 2 tu pulso subió en relación al ritmo. Típico síntoma de deshidratación o agotamiento del glucógeno.")

    # Factor 5: Fatiga acumulada
    if not diagnosticos or (ef1 and ef2 and ef2 < ef1 and len(diagnosticos) <= 1):
        diagnosticos.append("💤 **Fatiga Sistema Nervioso / Carga Acumulada:** Como la temperatura y terreno son similares, la pérdida de eficiencia indica **acumulación de fatiga de días previos**, estrés laboral, mala calidad de sueño o nutrición inadecuada pre-entreno.")

    return diagnosticos, ef1, ef2, drift1, drift2

def consultar_gemini_coach(prompt_texto):
    """Realiza la petición a la API de Gemini 2.5 Flash con reintentos y retroceso exponencial."""
    api_key = "" # Proporcionada en tiempo de ejecución por el entorno
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt_texto}]}],
        "systemInstruction": {
            "parts": [{
                "text": "Eres un entrenador de atletismo y fisiólogo deportivo de alto rendimiento experto en métricas de Stryd, COROS y Garmin. Analiza con rigor científico pero con tono motivador e inteligible para el corredor."
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

uploaded_files = st.file_uploader("Arrastra aquí tus archivos .FIT de COROS", type=["fit"], accept_multiple_files=True)

if uploaded_files:
    datos_cargados = []
    
    for file in uploaded_files:
        try:
            bytes_data = file.read()
            df_rec, df_laps, meta = leer_fichero_fit(bytes_data, file.name)
            if not df_rec.empty:
                datos_cargados.append({'df': df_rec, 'laps': df_laps, 'meta': meta})
        except Exception as e:
            st.error(f"Error al leer {file.name}: {e}")

    if datos_cargados:
        st.success(f"¡Se han procesado {len(datos_cargados)} archivo(s) correctamente!")
        
        # TABLA DE NAVEGACIÓN POR PESTAÑAS
        tab_ind, tab_comp, tab_diag = st.tabs(["📊 Sesión Individual", "📈 Comparativa Superpuesta", "🧠 Diagnóstico e IA Coach"])

        # =====================================================================
        # PESTAÑA 1: ANÁLISIS INDIVIDUAL
        # =====================================================================
        with tab_ind:
            opciones = [f"{d['meta']['deporte']} - {d['meta']['fecha_inicio'].strftime('%d/%m/%Y %H:%M') if d['meta']['fecha_inicio'] else d['meta']['nombre_archivo']}" for d in datos_cargados]
            idx_sel = st.selectbox("Selecciona la actividad a inspeccionar:", range(len(opciones)), format_func=lambda x: opciones[x])
            
            sel = datos_cargados[idx_sel]
            df = sel['df']
            meta = sel['meta']
            laps = sel['laps']

            # Métricas Clave
            st.markdown(f"### 📍 {meta['deporte']} - {meta['fecha_inicio'].strftime('%d de %B, %Y a las %H:%M') if meta['fecha_inicio'] else ''}")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Distancia", f"{meta['distancia_total']:.2f} km")
            dur_mins = int(meta['duracion_total'] // 60)
            dur_segs = int(meta['duracion_total'] % 60)
            col2.metric("Duración", f"{dur_mins}m {dur_segs}s")
            col3.metric("FC Promedio", f"{int(meta['fc_media'])} ppm" if meta['fc_media'] else "N/A")
            col4.metric("Calorías", f"{int(meta['calorias_totales'])} kcal")
            col5.metric("Temperatura", f"{meta['temperatura_media']} °C" if meta['temperatura_media'] is not None else "N/A")

            # Marcadores de Vueltas (Laps)
            vueltas_tiempos = []
            if not laps.empty and 'start_time' in laps.columns and 'timestamp' in df.columns:
                inicio_act = df['timestamp'].iloc[0]
                for _, lap in laps.iterrows():
                    if 'start_time' in lap and pd.notna(lap['start_time']):
                        t_lap = convert_to_madrid_time(lap['start_time'])
                        segs = (t_lap - inicio_act).total_seconds()
                        if segs > 0:
                            vueltas_tiempos.append(segs)

            # Gráfica de Ritmo y Pulso
            if 'ritmo_suavizado' in df.columns:
                st.subheader("📉 Ritmo (min/km) y Frecuencia Cardíaca (ppm)")
                fig = go.Figure()

                # Línea de Ritmo
                fig.add_trace(go.Scatter(
                    x=df['Tiempo_Segundos'], y=df['ritmo_suavizado'],
                    mode='lines', name='Ritmo (min/km)',
                    line=dict(color='#00CC96', width=2),
                    customdata=df['Ritmo_Texto'],
                    hovertemplate="Tiempo: %{text}<br>Ritmo: %{customdata} min/km<extra></extra>"
                ))

                # Línea de FC
                if 'heart_rate' in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df['Tiempo_Segundos'], y=df['heart_rate'],
                        mode='lines', name='FC (ppm)', yaxis='y2',
                        line=dict(color='#EF553B', width=2),
                        hovertemplate="FC: %{y:.0f} ppm<extra></extra>"
                    ))

                # Añadir líneas verticales de cada Vuelta/Fase
                for v_seg in vueltas_tiempos:
                    fig.add_vline(x=v_seg, line_width=1, line_dash="dash", line_color="gray")

                # Layout eje Y invertido para ritmo
                fig.update_layout(
                    xaxis=dict(title="Tiempo de Actividad", tickval=df['Tiempo_Segundos'][::len(df)//8], ticktext=df['Tiempo_Formato'][::len(df)//8]),
                    yaxis=dict(title="Ritmo (min/km)", autorange="reversed"),
                    yaxis2=dict(title="FC (ppm)", overlaying='y', side='right'),
                    hovermode="x unified", legend=dict(orientation="h", y=1.1)
                )
                st.plotly_chart(fig, use_container_width=True)

            # Cadencia, Altitud y Temperatura
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                if 'cadence_spm' in df.columns:
                    st.subheader("👟 Cadencia de Zancada (ppm)")
                    fig_cad = px.line(df, x='Tiempo_Segundos', y='cadence_spm', color_discrete_sequence=['#AB63FA'])
                    fig_cad.update_layout(xaxis_title="Tiempo", yaxis_title="Pasos por minuto")
                    st.plotly_chart(fig_cad, use_container_width=True)
            
            with col_g2:
                if 'altitude' in df.columns:
                    st.subheader("🏔️ Perfil de Altitud (m)")
                    fig_alt = px.area(df, x='Tiempo_Segundos', y='altitude', color_discrete_sequence=['#636EFA'])
                    fig_alt.update_layout(xaxis_title="Tiempo", yaxis_title="Altitud (m)")
                    st.plotly_chart(fig_alt, use_container_width=True)

            # Mapa GPS Ocultable
            if 'lat' in df.columns and 'lon' in df.columns:
                st.subheader("🗺️ Trazado de Ruta GPS")
                if st.toggle("Mostrar Mapa de Ruta", value=True, key=f"tog_map_{idx_sel}"):
                    df_mapa = df.dropna(subset=['lat', 'lon'])
                    fig_map = px.line_mapbox(df_mapa, lat="lat", lon="lon", zoom=13, height=400)
                    fig_map.update_traces(line=dict(width=3, color="red"))
                    fig_map.update_layout(mapbox_style="open-street-map", margin={"r":0,"t":0,"l":0,"b":0})
                    st.plotly_chart(fig_map, use_container_width=True)

            # Tabla de Vueltas / Desglose por Km
            if not laps.empty:
                st.subheader("⏱️ Desglose por Vueltas / Fases")
                cols_mostrar = [c for c in ['lap_index', 'total_elapsed_time', 'total_distance', 'avg_speed', 'avg_heart_rate'] if c in laps.columns]
                df_laps_show = laps[cols_mostrar].copy()
                if 'total_distance' in df_laps_show.columns:
                    df_laps_show['Distancia (km)'] = (df_laps_show['total_distance'] / 1000.0).round(2)
                if 'avg_speed' in df_laps_show.columns:
                    df_laps_show['Ritmo Medio'] = df_laps_show['avg_speed'].apply(lambda x: f"{int(16.6667/x)}:{int(((16.6667/x)%1)*60):02d}" if x > 0 else "-")
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
                    
                    # Graficar Ritmo
                    if 'ritmo_suavizado' in d['df'].columns:
                        fig_comp_ritmo.add_trace(go.Scatter(
                            x=d['df']['Tiempo_Segundos'], y=d['df']['ritmo_suavizado'],
                            mode='lines', name=f"Ritmo: {nombre}"
                        ))
                    # Graficar FC
                    if 'heart_rate' in d['df'].columns:
                        fig_comp_fc.add_trace(go.Scatter(
                            x=d['df']['Tiempo_Segundos'], y=d['df']['heart_rate'],
                            mode='lines', name=f"FC: {nombre}"
                        ))

                fig_comp_ritmo.update_layout(title="Comparativa de Ritmos (min/km)", yaxis=dict(autorange="reversed"))
                fig_comp_fc.update_layout(title="Comparativa de Pulsaciones (ppm)")

                st.plotly_chart(fig_comp_ritmo, use_container_width=True)
                st.plotly_chart(fig_comp_fc, use_container_width=True)

        # =====================================================================
        # PESTAÑA 3: DIAGNÓSTICO DE EFICIENCIA E IA COACH
        # =====================================================================
        with tab_diag:
            st.subheader("🧠 Análisis Fisiológico y Razonamiento de Rendimiento")
            
            if len(datos_cargados) < 2:
                st.warning("Para comparar por qué ha cambiado tu ritmo a las mismas pulsaciones, debes subir **al menos 2 archivos .FIT**.")
                
                # Análisis de sesión única
                if len(datos_cargados) == 1:
                    d1 = datos_cargados[0]
                    ef, drift = calcular_factor_eficiencia(d1['df'])
                    st.info(f"**Factor de Eficiencia ($EF$):** `{ef}` | **Deriva Cardíaca ($Pw:HR$):** `{drift}%`")
                    if drift and drift > 5.0:
                        st.write("⚠️ Existe un desacople aeróbico notable entre la 1ª y la 2ª mitad del entrenamiento. Tu corazón se fatigó al final.")
            else:
                c1, c2 = st.columns(2)
                with c1:
                    s1_idx = st.selectbox("Sesión de Referencia (Ej: La semana pasada / Mejor día):", range(len(opciones)), index=0, key="s1_diag")
                with c2:
                    s2_idx = st.selectbox("Sesión a Analizar (Ej: Esta semana / Peor día):", range(len(opciones)), index=1 if len(opciones)>1 else 0, key="s2_diag")

                data1 = datos_cargados[s1_idx]
                data2 = datos_cargados[s2_idx]

                diagnosticos, ef1, ef2, drift1, drift2 = diagnosticar_comparativa(data1, data2)

                # Tarjetas de resumen métrico
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
                st.write("¿Quieres una explicación fisiológica completa redactada por el modelo de IA?")

                if st.button("✨ Preguntar al Entrenador IA Gemini"):
                    with st.spinner("Analizando la biomecánica, condiciones ambientales y derivadas cardíacas..."):
                        prompt = f"""
                        Analiza estas dos sesiones de entrenamiento .FIT del corredor:
                        
                        SESIÓN 1 (Referencia):
                        - Fecha: {data1['meta']['fecha_inicio']}
                        - Distancia: {data1['meta']['distancia_total']:.2f} km
                        - Duración: {data1['meta']['duracion_total']/60:.1f} min
                        - FC Media: {data1['meta']['fc_media']} ppm
                        - Factor de Eficiencia (EF): {ef1}
                        - Deriva Cardíaca: {drift1}%
                        - Temperatura: {data1['meta'].get('temperatura_media')} °C
                        
                        SESIÓN 2 (A analizar):
                        - Fecha: {data2['meta']['fecha_inicio']}
                        - Distancia: {data2['meta']['distancia_total']:.2f} km
                        - Duración: {data2['meta']['duracion_total']/60:.1f} min
                        - FC Media: {data2['meta']['fc_media']} ppm
                        - Factor de Eficiencia (EF): {ef2}
                        - Deriva Cardíaca: {drift2}%
                        - Temperatura: {data2['meta'].get('temperatura_media')} °C
                        
                        Diagnósticos automáticos detectados por el sistema:
                        {json.dumps(diagnosticos, ensure_ascii=False)}
                        
                        Explícale al corredor con detalle por qué su ritmo ha cambiado mantenido las pulsaciones o viceversa, qué factores externos o de fatiga han intervenido y qué consejos debe seguir esta semana para recuperarse o ajustar sus ritmos en Zona 2.
                        """
                        
                        respuesta_ia = consultar_gemini_coach(prompt)
                        st.markdown(f"```\n{respuesta_ia}\n```")

