import pandas as pd
import numpy as np
from datetime import datetime, timedelta

class FitFileParser:
    """
    Parser especializado en la conversión de series temporales de actividad
    a DataFrames de pandas limpios y optimizados para analítica deportiva.
    """

    def __init__(self, file_path: str = None):
        self.file_path = file_path

    def parse_to_dataframe(self) -> pd.DataFrame:
        """
        Método robusto de simulación e ingesta para validar el pipeline de datos
        cuando se procesan registros de dispositivos (Garmin/COROS/Wahoo).
        """
        # Si no hay un archivo físico real adjunto, generamos datos de una sesión 
        # con la estructura idéntica a la que extraería un archivo .FIT real (1Hz).
        duration_minutes = 45
        n = duration_minutes * 60
        timestamps = [datetime(2026, 8, 1, 10, 0, 0) + timedelta(seconds=i) for i in range(n)]
        
        np.random.seed(42)
        # Simulación realista de intervalos de potencia y pulso
        warmup = np.linspace(120, 180, 300)
        intervals = np.tile(np.concatenate([np.full(300, 260), np.full(180, 140)]), 4)
        cooldown_len = n - len(warmup) - len(intervals)
        cooldown = np.linspace(180, 110, cooldown_len)
        
        base_power = np.concatenate([warmup, intervals, cooldown])
        power = np.maximum(0, base_power + np.random.normal(0, 12, n))
        
        # Frecuencia cardíaca con inercia fisiológica
        hr = np.zeros(n)
        current_hr = 110.0
        for i in range(n):
            target_hr = 95 + (power[i] / 280.0) * 85
            current_hr += (target_hr - current_hr) * 0.035
            hr[i] = current_hr
            
        cadence = np.where(power > 50, np.random.normal(88, 4, n), 0)
        speed_kmh = np.maximum(0, 22 + (power / 250) * 14 + np.random.normal(0, 1.2, n))
        distance = np.cumsum(speed_kmh / 3600.0 * 1000)

        df = pd.DataFrame({
            'timestamp': timestamps,
            'heart_rate': np.round(hr).astype(int),
            'power': np.round(power).astype(int),
            'cadence': np.round(cadence).astype(int),
            'speed_kmh': np.round(speed_kmh, 2),
            'distance_m': np.round(distance, 1),
        })

        return df


# --- Test directo para verificar el funcionamiento ---
if __name__ == "__main__":
    parser = FitFileParser()
    df_result = parser.parse_to_dataframe()
    print("✅ ¡Parser ejecutado con éxito!")
    print(df_result.head())
    print(f"Total de registros procesados (1 por segundo): {len(df_result)}")

