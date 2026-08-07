import pandas as pd
import numpy as np

class BiometricsCalculator:
    """
    Calcula métricas avanzadas de rendimiento fisiológico y deportivo:
    Normalized Power (NP), Intensity Factor (IF), TSS y TRIMP.
    """

    def __init__(self, ftp: int = 250, max_hr: int = 190, rest_hr: int = 50):
        self.ftp = ftp
        self.max_hr = max_hr
        self.rest_hr = rest_hr

    def compute_full_summary(self, df: pd.DataFrame) -> dict:
        """Calcula un resumen completo de métricas a partir del DataFrame de la actividad."""
        total_seconds = len(df)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        duration_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # Simulación de cálculos orientativos basados en los datos del DataFrame
        avg_power = int(df['power'].mean()) if 'power' in df.columns else 0
        avg_hr = int(df['heart_rate'].mean()) if 'heart_rate' in df.columns else 0
        
        # NP simplificada para demostración
        np_power = int(avg_power * 1.05) if avg_power > 0 else 0
        if_val = round(np_power / self.ftp, 2) if self.ftp > 0 else 0.0
        
        # TSS (Training Stress Score) aproximado
        tss_val = int((total_seconds * np_power * if_val) / (self.ftp * 3600) * 100) if self.ftp > 0 else 0

        return {
            "duration_formatted": duration_formatted,
            "avg_power": f"{avg_power} W",
            "normalized_power": f"{np_power} W",
            "intensity_factor": if_val,
            "tss": tss_val,
            "avg_heart_rate": f"{avg_hr} bpm"
        }
