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
st.write("Visualiza métricas, compara entrenamientos y analiza tu ritmo y pulso.")

archivos_subidos = st.file_uploader(
    "Arrastra o selecciona tus archivos .fit", 
    type=["fit", "FIT"], 
    accept_multiple_files=True
)

def decodificar_fit(bytes_archivo):
    """Extrae el resumen y los puntos de telemetría del archivo binario .FIT."""
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
                elif frame.name == 'lap':                    datos_lap = {}
                    for field in frame.fields:
                        if field.value is not None:
                            datos_lap[field.name] = field.value
                    laps.append(datos_lap)
                    
    df = pd.DataFrame(puntos)
    df_laps = pd.DataFrame(laps)
    return resumen, df, df_laps

def procesar_telemetria(df, df_laps):
    """Limpia los datos, calcula el tiempo relativo, el ritmo y las coordenadas."""
    if df.empty or 'timestamp' not in df.columns:
        return df, df_laps
        
    # 1. Tiempo relativo (Minutos desde el inicio en vez de hora absoluta)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    inicio = df['timestamp'].min()
    df['Segundos_Transcurridos'] = (df['timestamp'] - inicio).dt.total_seconds()
    df['Minutos'] = df['Segundos_Transcurridos'] / 60.0
    
    # Formato MM:SS para mostrar al pasar el ratón de forma amigable
    df['Tiempo_Formato'] = df['Segundos_Transcurridos'].apply(
        lambda x: f"{int(x//60)}:{int(x%60):02d}" if pd.notna(x) else "0:00"
    )

    # 2. Conversión a Ritmo (min/km) y suavizado para evitar saltos bruscos
    speed_col = 'enhanced_speed' if 'enhanced_speed' in df.columns else 'speed' if 'speed' in df.columns else None
    
    if speed_col:
        # 1000 / velocidad(m/s) / 60 = ritmo en min/km. Evitamos dividir por velocidades muy bajas (caminata/parado).
        df['Ritmo_Crudo'] = np.where(df[speed_col] > 0.8, (1000 / df[speed_col]) / 60.0, np.nan)
        # Suavizamos el ritmo usando un promedio de los últimos 10 segundos
        df['Ritmo (min/km)'] = df['Ritmo_Crudo'].rolling(window=10, min_periods=1).mean()
        
        # Formato visual del ritmo (Ej: 5.5 min/km -> 5:30)
        df['Ritmo_Formato'] = df['Ritmo (min/km)'].apply(
            lambda x: f"{int(x)}:{int((x-int(x))*60):02d}" if pd.notna(x) else "N/A"
        )

    # 3. Suavizado de Frecuencia Cardíaca
    if 'heart_rate' in df.columns:
        # Aplicamos una media móvil de 10 segundos para limpiar ruido del sensor
        df['heart_rate'] = df['heart_rate'].rolling(window=10, min_periods=1).mean()

    # 4. Coordenadas para el Mapa
    if 'position_lat' in df.columns and 'position_long' in df.columns:
        # Conversión matemática oficial del formato FIT (semicírculos a grados decimales)
        df['lat'] = df['position_lat'] * (180.0 / (2**31))
        df['lon'] = df['position_long'] * (180.0 / (2**31))
        
    # 5. STREAMING_CHUNK: Procesando los tiempos de las fases para las gráficas
    if not df_laps.empty and 'timestamp' in df_laps.columns:
        df_laps['timestamp'] = pd.to_datetime(df_laps['timestamp'])
        # Calculamos en qué minuto relativo terminó cada fase para pintarlo en la gráfica
        df_laps['Minutos'] = (df_laps['timestamp'] - inicio).dt.total_seconds() / 60.0
        
    return df, df_laps

if archivos_subidos:
    # Si hay más de un archivo, damos la opción de superponer gráficos
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
                df['Archivo'] = archivo.name # Añadimos el nombre para diferenciar colores en la leyenda
                todos_los_datos.append(df)
                
        if todos_los_datos:
            df_global = pd.concat(todos_los_datos, ignore_index=True)
            
            # Gráfico de Pulso Comparado
            if 'heart_rate' in df_global.columns:
                fig_hr = px.line(df_global, x='Minutos', y='heart_rate', color='Archivo',
                                 title="Comparativa: Frecuencia Cardíaca (bpm)",
                                 labels={'Minutos': 'Tiempo de Actividad (min)', 'heart_rate': 'Pulsaciones (bpm)'},
                                 hover_data={'Minutos': False, 'Tiempo_Formato': True, 'heart_rate': True})
                st.plotly_chart(fig_hr, use_container_width=True)
                
            # Gráfico de Ritmo Comparado
            if 'Ritmo (min/km)' in df_global.columns:
                fig_pace = px.line(df_global, x='Minutos', y='Ritmo (min/km)', color='Archivo',
                                   title="Comparativa: Ritmo (min/km)",
                                   labels={'Minutos': 'Tiempo de Actividad (min)', 'Ritmo (min/km)': 'Ritmo'},
                                   hover_data={'Minutos': False, 'Tiempo_Formato': True, 'Ritmo (min/km)': False, 'Ritmo_Formato': True})
                # ¡Invertimos el eje Y para que los ritmos rápidos (números bajos) estén arriba!
                fig_pace.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_pace, use_container_width=True)

    # --- MODO INDIVIDUAL (POR DEFECTO) ---
    else:
        for archivo in archivos_subidos:
            st.divider()
            
            try:
                contenido_bytes = archivo.read()
                resumen, df, df_laps = decodificar_fit(contenido_bytes)
                df, df_laps = procesar_telemetria(df, df_laps)
                
                deporte = str(resumen.get('sport', 'Actividad')).capitalize()
                fecha_inicio = resumen.get('start_time')
                try:
                    fecha_formateada = pd.to_datetime(fecha_inicio).strftime('%d/%m/%Y %H:%M')
                    titulo = f"🏃 {deporte} - {fecha_formateada}"
                except:
                    titulo = f"🏃 Actividad de COROS"
                    
                st.subheader(f"{titulo} (Archivo original: {archivo.name})")
                
                # Tarjetas de Métricas Resumen
                col1, col2, col3, col4, col5 = st.columns(5)
                
                distancia_km = (resumen.get('total_distance', 0) or 0) / 1000.0
                col1.metric("Distancia Total", f"{distancia_km:.2f} km")
                
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
                        if not df_laps.empty and 'Minutos' in df_laps.columns:
                            for min_lap in df_laps['Minutos']:
                                figura.add_vline(x=min_lap, line_width=1.5, line_dash="dash", line_color="gray", opacity=0.6)
                        return figura

                    # 1. Trazado del Mapa (Si hay GPS)
                    if 'lat' in df.columns and 'lon' in df.columns:
                        st.write("### 🗺️ Ruta GPS")
                        st.map(df[['lat', 'lon']].dropna(), zoom=13)

                    st.write("### 📈 Telemetría de la Actividad")
                    
                    # 2. Gráfico interactivo de Pulso
                    if 'heart_rate' in df.columns:
                        fig_hr = px.line(df, x='Minutos', y='heart_rate',
                                         title="Frecuencia Cardíaca (bpm)",
                                         labels={'Minutos': 'Tiempo (min)', 'heart_rate': 'Pulsaciones (bpm)'},
                                         hover_data={'Minutos': False, 'Tiempo_Formato': True, 'heart_rate': True})
                        fig_hr.update_traces(line_color='#FF4B4B') # Color rojo para el corazón
                        fig_hr = dibujar_fases(fig_hr) # Añadimos las líneas de fases
                        st.plotly_chart(fig_hr, use_container_width=True)
                        
                    # 3. Gráfico interactivo de Ritmo (Invertido)
                    if 'Ritmo (min/km)' in df.columns:
                        fig_pace = px.line(df, x='Minutos', y='Ritmo (min/km)',
                                           title="Ritmo Suavizado (min/km)",
                                           labels={'Minutos': 'Tiempo (min)'},
                                           hover_data={'Minutos': False, 'Tiempo_Formato': True, 'Ritmo (min/km)': False, 'Ritmo_Formato': True})
                        fig_pace.update_traces(line_color='#1E90FF') # Color azul para la velocidad
                        fig_pace.update_yaxes(autorange="reversed") # Los rápidos arriba
                        fig_pace = dibujar_fases(fig_pace) # Añadimos las líneas de fases
                        st.plotly_chart(fig_pace, use_container_width=True)
                        
                    # 4. STREAMING_CHUNK: Gráfico interactivo de Temperatura
                    if 'temperature' in df.columns:
                        fig_temp = px.line(df, x='Minutos', y='temperature',
                                           title="Temperatura a lo largo de la ruta (°C)",
                                           labels={'Minutos': 'Tiempo (min)', 'temperature': 'Temp (°C)'},
                                           hover_data={'Minutos': False, 'Tiempo_Formato': True, 'temperature': True})
                        fig_temp.update_traces(line_color='#FFA500') # Naranja para la temperatura
                        fig_temp = dibujar_fases(fig_temp) # Añadimos las líneas de fases
                        st.plotly_chart(fig_temp, use_container_width=True)
                
                # Descarga a CSV
                csv_datos = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar telemetría en CSV",
                    data=csv_datos,
                    file_name=f"{archivo.name}_analisis.csv",
                    mime="text/csv"
                )
                
            except Exception as error:
                st.error(f"Error procesando '{archivo.name}': {error}")

