import streamlit as st
import fitdecode
import pandas as pd
import numpy as np
import plotly.express as px
import io

st.set_page_config(
    page_title="Analizador FIT - COROS Pace 4",
    page_icon="🏃",
    layout="wide"
)

st.title("🏃 Analizador Avanzado - COROS Pace 4")
st.write("Visualiza métricas, compara entrenamientos y analiza tu ritmo, pulso y temperatura.")

archivos_subidos = st.file_uploader(
    "Arrastra o selecciona tus archivos .fit", 
    type=["fit", "FIT"], 
    accept_multiple_files=True
)

def decodificar_fit(bytes_archivo):
    """Extrae el resumen, los puntos de telemetría y las fases (laps) del archivo binario .FIT."""
    resumen = {}
    puntos = []
    laps = []    
    
    with fitdecode.FitReader(io.BytesIO(bytes_archivo)) as fit:
        for frame in fit:
            # Comprobamos estrictamente que sea un bloque de datos para evitar errores de cabecera
            if isinstance(frame, fitdecode.FitDataMessage):
                if frame.name == 'session':
                    for field in frame.fields:
                        if field.value is not None:
                            resumen[field.name] = field.value
                elif frame.name == 'record':
                    datos_punto = {}
                    for field in frame.fields:
                        if field.value is not None:
                            datos_punto[field.name] = field.value
                    puntos.append(datos_punto)
                elif frame.name == 'lap':
                    datos_lap = {}
                    for field in frame.fields:
                        if field.value is not None:
                            datos_lap[field.name] = field.value
                    laps.append(datos_lap)
                    
    df = pd.DataFrame(puntos)
    df_laps = pd.DataFrame(laps)
    return resumen, df, df_laps

def procesar_telemetria(df, df_laps):
    """Limpia los datos, calcula el tiempo relativo, suaviza métricas y prepara el mapa."""
    if df.empty or 'timestamp' not in df.columns:
        return df, df_laps
        
    # 1. Tiempo relativo (Minutos desde el inicio en vez de hora absoluta)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    inicio = df['timestamp'].min()
    df['Segundos_Transcurridos'] = (df['timestamp'] - inicio).dt.total_seconds()
    df['Minutos'] = df['Segundos_Transcurridos'] / 60.0
    
    # Formato MM:SS para mostrar al pasar el ratón
    df['Tiempo_Formato'] = df['Segundos_Transcurridos'].apply(
        lambda x: f"{int(x//60)}:{int(x%60):02d}" if pd.notna(x) else "0:00"
    )

    # 2. Conversión a Ritmo (min/km) y suavizado
    speed_col = 'enhanced_speed' if 'enhanced_speed' in df.columns else 'speed' if 'speed' in df.columns else None
    if speed_col:
        df['Ritmo_Crudo'] = np.where(df[speed_col] > 0.8, (1000 / df[speed_col]) / 60.0, np.nan)
        # Suavizamos el ritmo usando un promedio de los últimos 10 segundos
        df['Ritmo (min/km)'] = df['Ritmo_Crudo'].rolling(window=10, min_periods=1).mean()
        df['Ritmo_Formato'] = df['Ritmo (min/km)'].apply(
            lambda x: f"{int(x)}:{int((x-int(x))*60):02d}" if pd.notna(x) else "N/A"
        )

    # 3. Suavizado de Frecuencia Cardíaca (rolling average de 10 segundos)
    if 'heart_rate' in df.columns:
        df['heart_rate'] = df['heart_rate'].rolling(window=10, min_periods=1).mean()

    # 4. Coordenadas para el Mapa
    if 'position_lat' in df.columns and 'position_long' in df.columns:
        df['lat'] = df['position_lat'] * (180.0 / (2**31))
        df['lon'] = df['position_long'] * (180.0 / (2**31))
        
    # 5. Procesando los tiempos de las fases (vueltas/laps)
    if not df_laps.empty and 'timestamp' in df_laps.columns:
        df_laps['timestamp'] = pd.to_datetime(df_laps['timestamp'])
        df_laps['Minutos'] = (df_laps['timestamp'] - inicio).dt.total_seconds() / 60.0
        
    return df, df_laps

if archivos_subidos:
    modo_vista = "Individual"
    if len(archivos_subidos) > 1:
        st.divider()
        modo_vista = st.radio(
            "🔎 Opciones de visualización:", 
            ["Ver individualmente (Métricas completas)", "Superponer entrenamientos (Comparación)"],
            horizontal=True
        )
        
    # --- MODO SUPERPOSICIÓN (COMPARACIÓN) ---
    if "Superponer" in modo_vista:
        st.subheader("📊 Comparativa de Entrenamientos")
        todos_los_datos = []
        
        for archivo in archivos_subidos:
            contenido_bytes = archivo.read()
            resumen, df, df_laps = decodificar_fit(contenido_bytes)
            df, df_laps = procesar_telemetria(df, df_laps)
            if not df.empty:
                df['Archivo'] = archivo.name
                todos_los_datos.append(df)
                
        if todos_los_datos:
            df_global = pd.concat(todos_los_datos, ignore_index=True)
            
            if 'heart_rate' in df_global.columns:
                fig_hr = px.line(df_global, x='Minutos', y='heart_rate', color='Archivo',
                                 title="Comparativa: Frecuencia Cardíaca Suavizada (bpm)",
                                 hover_data={'Minutos': False, 'Tiempo_Formato': True, 'heart_rate': True})
                st.plotly_chart(fig_hr, use_container_width=True)
                
            if 'Ritmo (min/km)' in df_global.columns:
                fig_pace = px.line(df_global, x='Minutos', y='Ritmo (min/km)', color='Archivo',
                                   title="Comparativa: Ritmo Suavizado (min/km)",
                                   hover_data={'Minutos': False, 'Tiempo_Formato': True, 'Ritmo (min/km)': False, 'Ritmo_Formato': True})
                fig_pace.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_pace, use_container_width=True)

    # --- MODO INDIVIDUAL ---
    else:
        for archivo in archivos_subidos:
            st.divider()
            try:
                contenido_bytes = archivo.read()
                resumen, df, df_laps = decodificar_fit(contenido_bytes)
                df, df_laps = procesar_telemetria(df, df_laps)
                
                # Creación del título dinámico (Deporte + Fecha)
                deporte = str(resumen.get('sport', 'Actividad')).capitalize()
                fecha_inicio = resumen.get('start_time')
                try:
                    fecha_formateada = pd.to_datetime(fecha_inicio).strftime('%d/%m/%Y %H:%M')
                    titulo = f"🏃 {deporte} - {fecha_formateada}"
                except:
                    titulo = f"🏃 Actividad de COROS"
                    
                st.subheader(f"{titulo}")
                st.caption(f"Archivo original: {archivo.name}")
                
                # Tarjetas de Métricas
                col1, col2, col3, col4, col5 = st.columns(5)
                
                distancia_km = (resumen.get('total_distance', 0) or 0) / 1000.0
                col1.metric("Distancia", f"{distancia_km:.2f} km")
                
                duracion_seg = resumen.get('total_timer_time', 0) or 0
                col2.metric("Duración", f"{int(duracion_seg // 60)}m {int(duracion_seg % 60)}s")
                
                fc_prom = resumen.get('avg_heart_rate', 'N/A')
                col3.metric("FC Promedio", f"{fc_prom} bpm" if fc_prom != 'N/A' else 'N/A')
                
                calorias = resumen.get('total_calories', 'N/A')
                col4.metric("Calorías", f"{calorias} kcal" if calorias != 'N/A' else 'N/A')
                
                temp_prom = resumen.get('avg_temperature', 'N/A')
                col5.metric("Temp. Media", f"{temp_prom} °C" if temp_prom != 'N/A' else 'N/A')
                
                if not df.empty:
                    def dibujar_fases(figura):
                        """Añade líneas verticales grises en los minutos exactos donde hay un Lap/Vuelta"""
                        if not df_laps.empty and 'Minutos' in df_laps.columns:
                            for min_lap in df_laps['Minutos']:
                                figura.add_vline(x=min_lap, line_width=1.5, line_dash="dash", line_color="gray", opacity=0.6)
                        return figura

                    if 'lat' in df.columns and 'lon' in df.columns:
                        st.write("### 🗺️ Ruta GPS")
                        
                        # Interruptor para mostrar u ocultar el mapa
                        mostrar_mapa = st.toggle("Mostrar mapa en pantalla", value=True)
                        
                        if mostrar_mapa:
                            df_mapa = df[['lat', 'lon']].dropna()
                            if not df_mapa.empty:
                                fig_map = px.line_mapbox(
                                    df_mapa, lat="lat", lon="lon", 
                                    zoom=13, height=400
                                )
                                fig_map.update_traces(line=dict(width=3, color='#FF4B4B'))
                                fig_map.update_layout(
                                    mapbox_style="open-street-map",
                                    margin={"r":0, "t":0, "l":0, "b":0}
                                )
                                st.plotly_chart(fig_map, use_container_width=True)

                    st.write("### 📈 Telemetría de la Actividad")
                    
                    if 'heart_rate' in df.columns:
                        fig_hr = px.line(df, x='Minutos', y='heart_rate',
                                         title="Frecuencia Cardíaca Suavizada (bpm)",
                                         labels={'Minutos': 'Tiempo (min)', 'heart_rate': 'Pulsaciones (bpm)'},
                                         hover_data={'Minutos': False, 'Tiempo_Formato': True, 'heart_rate': True})
                        fig_hr.update_traces(line_color='#FF4B4B')
                        fig_hr = dibujar_fases(fig_hr)
                        st.plotly_chart(fig_hr, use_container_width=True)
                        
                    if 'Ritmo (min/km)' in df.columns:
                        fig_pace = px.line(df, x='Minutos', y='Ritmo (min/km)',
                                           title="Ritmo Suavizado (min/km)",
                                           labels={'Minutos': 'Tiempo (min)'},
                                           hover_data={'Minutos': False, 'Tiempo_Formato': True, 'Ritmo (min/km)': False, 'Ritmo_Formato': True})
                        fig_pace.update_traces(line_color='#1E90FF')
                        fig_pace.update_yaxes(autorange="reversed")
                        fig_pace = dibujar_fases(fig_pace)
                        st.plotly_chart(fig_pace, use_container_width=True)
                        
                    if 'temperature' in df.columns:
                        fig_temp = px.line(df, x='Minutos', y='temperature',
                                           title="Temperatura a lo largo de la ruta (°C)",
                                           labels={'Minutos': 'Tiempo (min)', 'temperature': 'Temp (°C)'},
                                           hover_data={'Minutos': False, 'Tiempo_Formato': True, 'temperature': True})
                        fig_temp.update_traces(line_color='#FFA500')
                        fig_temp = dibujar_fases(fig_temp)
                        st.plotly_chart(fig_temp, use_container_width=True)
                
                # Descarga del archivo final procesado
                csv_datos = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar telemetría en CSV",
                    data=csv_datos,
                    file_name=f"{deporte}_telemetria.csv",
                    mime="text/csv"
                )
                
            except Exception as error:
                st.error(f"Error procesando '{archivo.name}': {error}")

