import pandas as pd
import numpy as np

class ACWRCalculator:
    """
    Calcula el ratio de carga aguda (fatiga a corto plazo, ej: 7 días) 
    frente a la carga crónica (forma física a largo plazo, ej: 28 días)
    utilizando EWMA (Exponentially Weighted Moving Average).
    """

    def __init__(self, acute_span: int = 7, chronic_span: int = 28):
        self.acute_span = acute_span
        self.chronic_span = chronic_span

    def calculate_ewma_acwr(self, daily_tss_series: pd.Series) -> pd.DataFrame:
        """
        Recibe una serie temporal de TSS diario y calcula la carga aguda,
        la carga crónica y el ratio ACWR resultante.
        """
        df = pd.DataFrame({'tss': daily_tss_series})
        
        # Cálculo de EWMA para Aguda y Crónica
        # span corresponde a los días de ventana temporal
        df['acute_load'] = df['tss'].ewm(span=self.acute_span, adjust=False).mean()
        df['chronic_load'] = df['tss'].ewm(span=self.chronic_span, adjust=False).mean()
        
        # Evitar división por cero
        df['acwr'] = np.where(
            df['chronic_load'] > 0, 
            df['acute_load'] / df['chronic_load'], 
            0.0
        )
        
        # Clasificación del nivel de riesgo de lesión (Sweet Spot vs Danger Zone)
        df['risk_status'] = df['acwr'].apply(self._classify_risk)
        
        return df

    @staticmethod
    def _classify_risk(acwr_value: float) -> str:
        """Clasifica el estado de forma según el rango de ACWR."""
        if acwr_value == 0:
            det = "Sin datos"
        elif acwr_value < 0.8:
            det = "Subentrenamiento / Desentrenado"
        elif 0.8 <= acwr_value <= 1.3:
            det = "Optimal Sweet Spot (Riesgo bajo)"
        elif 1.3 < acwr_value <= 1.5:
            det = "Zona de precaución (Incremento rápido)"
        else:
            det = "High Injury Risk (Alerta roja 🚨)"
        return det
