import streamlit as st
import fitdecode
import pandas as pd
import io

st.set_page_config(
    page_title="Analizador FIT - COROS Pace 4",
    page_icon="🏃",
    layout="wide"
)

st.title("🏃 Analizador de Entrenamientos - COROS Pace 4")
st.write("Sube uno o varios archivos `.fit` extraídos de tu COROS para visualizar métricas, gráficos interactivos y exportar a CSV.")

archivos_subidos = st.file_uploader(
    "Arrastra o selecciona tus archivos .fit", 
    type=["fit", "FIT"], 
    accept_multiple_files=True
)

def decodificar_fit(bytes_archivo):
    """
    Decodifica un archivo .fit utilizando fitdecode para tolerar
    campos personalizados o datos no estándar de COROS Pace 4.
    """
    resumen = {}
    puntos = []
    
    with fitdecode.FitReader(io.BytesIO(bytes_archivo)) as fit:
        for frame in fit:
            if frame.is_data():
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
                    
    df = pd.DataFrame(puntos)
    return resumen, df

if archivos_subidos:
    for archivo in archivos_subidos:
        st.divider()
        st.subheader(f"📁 Archivo: {archivo.name}")
        
        try:
            contenido_bytes = archivo.read()
            resumen, df = decodificar_fit(contenido_bytes)
            
            col1, col2, col3, col4 = st.columns(4)
            
            distancia_m = resumen.get('total_distance', 0) or 0
            distancia_km = distancia_m / 1000.0
            col1.metric("Distancia Total", f"{distancia_km:.2f} km")
            
            duracion_seg = resumen.get('total_timer_time', 0) or 0
            mins, segs = int(duracion_seg // 60), int(duracion_seg % 60)
            col2.metric("Duración", f"{mins}m {segs}s")
            
            fc_prom = resumen.get('avg_heart_rate', 'N/A')
            col3.metric("FC Promedio", f"{fc_prom} bpm" if fc_prom != 'N/A' else 'N/A')
            
            calorias = resumen.get('total_calories', 'N/A')
            col4.metric("Calorías", f"{calorias} kcal" if calorias != 'N/A' else 'N/A')
            
            if not df.empty and 'timestamp' in df.columns:
                st.write("### 📈 Telemetría de la Actividad")
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                
                # Conversión de velocidad a km/h
                if 'enhanced_speed' in df.columns:
                    df['Velocidad (km/h)'] = df['enhanced_speed'] * 3.6
                elif 'speed' in df.columns:
                    df['Velocidad (km/h)'] = df['speed'] * 3.6

                # Gráfico de Frecuencia Cardíaca
                if 'heart_rate' in df.columns:
                    st.subheader("Frecuencia Cardíaca (bpm)")
                    st.line_chart(df.set_index('timestamp')['heart_rate'])
                    
                # Gráfico de Velocidad
                if 'Velocidad (km/h)' in df.columns:
                    st.subheader("Velocidad (km/h)")
                    st.line_chart(df.set_index('timestamp')['Velocidad (km/h)'])
            
            csv_datos = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descargar datos procesados en CSV",
                data=csv_datos,
                file_name=f"{archivo.name}_telemetria.csv",
                mime="text/csv"
            )
            
        except Exception as error:
            st.error(f"Error procesando '{archivo.name}': {error}")

