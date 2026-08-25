# ... existing code ...
def obtener_metricas_run_filtradas(df, laps, meta):
    """Calcula métricas promedio exclusivas para las fases principales de Carrera (Run), descartando Calentamiento (Warm Up) y Enfriamiento (Cool Down)."""
    df_run = pd.DataFrame()
    
    if not df.empty:
        df_filtered = df.copy()
        
        # 1. Detección de fases de Calentamiento (Warm Up) y Enfriamiento (Cool Down)
        if not laps.empty and 'start_time' in laps.columns:
            warmup_end = None
            cooldown_start = None
            
            for _, lap in laps.iterrows():
                if pd.isna(lap.get('start_time')):
                    continue
                
                t_start = convert_to_madrid_time(lap['start_time'])
                t_dur = lap.get('total_elapsed_time') or lap.get('total_timer_time') or 0
                t_end = t_start + pd.Timedelta(seconds=float(t_dur)) if t_dur > 0 else None
                
                lap_dict_str = str(lap.to_dict()).lower()
                
                # Identificación de Calentamiento (Warm Up)
                is_warmup = False
                if 'intensity' in lap and lap['intensity'] is not None:
                    intens = str(lap['intensity']).lower()
                    if 'warm' in intens or 'calent' in intens or intens == '2' or lap['intensity'] == 2:
                        is_warmup = True
                if 'wkt_step_type' in lap and lap['wkt_step_type'] is not None:
                    wkt = str(lap['wkt_step_type']).lower()
                    if 'warm' in wkt or 'calent' in wkt or wkt == '0' or lap['wkt_step_type'] == 0:
                        is_warmup = True
                if 'warm' in lap_dict_str or 'calent' in lap_dict_str or 'calentamiento' in lap_dict_str:
                    is_warmup = True
                    
                if is_warmup and t_end is not None:
                    if warmup_end is None or t_start <= warmup_end:
                        warmup_end = t_end

                # Identificación de Enfriamiento (Cool Down)
                is_cooldown = False
                if 'intensity' in lap and lap['intensity'] is not None:
                    intens = str(lap['intensity']).lower()
                    if 'cool' in intens or 'enfri' in intens or intens == '3' or lap['intensity'] == 3:
                        is_cooldown = True
                if 'wkt_step_type' in lap and lap['wkt_step_type'] is not None:
                    wkt = str(lap['wkt_step_type']).lower()
                    if 'cool' in wkt or 'enfri' in wkt or wkt == '1' or lap['wkt_step_type'] == 1:
                        is_cooldown = True
                if 'cool' in lap_dict_str or 'enfri' in lap_dict_str or 'descalentamiento' in lap_dict_str:
                    is_cooldown = True
                    
                if is_cooldown and cooldown_start is None:
                    cooldown_start = t_start

            # Filtrar: excluir registros antes del fin del calentamiento y después del inicio del enfriamiento
            if warmup_end is not None and 'timestamp' in df_filtered.columns:
                df_filtered = df_filtered[df_filtered['timestamp'] >= warmup_end]
                
            if cooldown_start is not None and 'timestamp' in df_filtered.columns:
                df_filtered = df_filtered[df_filtered['timestamp'] < cooldown_start]

        # 2. Filtrado por deporte de carrera
# ... existing code ...
            # Fila 1: Distancia | Duración | Avg. Pace (Run)
            r1_col1, r1_col2, r1_col3 = st.columns(3)
            if es_nat:
                r1_col1.metric("Distancia", f"{int(meta['distancia_total'])} m")
            else:
                r1_col1.metric("Distancia", f"{meta['distancia_total']:.2f} km")
                
            dur_mins = int(meta['duracion_total'] // 60)
            dur_segs = int(meta['duracion_total'] % 60)
            r1_col2.metric("Duración", f"{dur_mins}m {dur_segs}s")
            r1_col3.metric("Avg. Pace (Run)", pace_run_str, help="Ritmo medio exclusivo de la fase principal de Carrera (excluye Calentamiento y Enfriamiento)")

            # Fila 2: FC Promedio (Run) | EF (Eficiencia) | Coste Cardíaco (Run)
            r2_col1, r2_col2, r2_col3 = st.columns(3)
            r2_col1.metric("FC Promedio (Run)", fc_run_str, help="Pulsaciones medias en la fase principal de Carrera (excluye Calentamiento y Enfriamiento)")
            r2_col2.metric("EF (Eficiencia)", f"{ef_valor}" if ef_valor is not None else "N/A", help="Factor de Eficiencia: Velocidad (m/min) / FC Media")
            r2_col3.metric("Coste Cardíaco (Run)", coste_run_str, help="Latidos consumidos por km en la fase principal de Carrera (excluye Calentamiento y Enfriamiento)")

            # Fila 3: Running Power (Run) | Calorías | Temperatura
            r3_col1, r3_col2, r3_col3 = st.columns(3)
            r3_col1.metric("Running Power (Run)", power_run_str, help="Potencia media de carrera en Vatios (excluye Calentamiento y Enfriamiento)")
            r3_col2.metric("Calorías", f"{int(meta['calorias_totales'])} kcal" if meta['calorias_totales'] else "N/A")
            r3_col3.metric("Temperatura", f"{meta['temperatura_media']} °C" if meta['temperatura_media'] is not None else "N/A")
# ... existing code ...
```

### Instrucciones de Aplicación
1. Reemplaza en `app.py` la función `obtener_metricas_run_filtradas` y los textos de ayuda de las tarjetas de métricas por este bloque de código.
2. Guarda los cambios con **Commit changes** en GitHub.

¿Compruebas ahora que al subir un archivo estructurado la **FC Promedio (Run)** no incluye las pulsaciones bajas del calentamiento inicial?
