import sys
from pathlib import Path

# Añadir la raíz del proyecto al path de Python para que encuentre 'src'
root_path = Path(__file__).resolve().parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

import streamlit as st
import pandas as pd
import numpy as np

# Ahora ya podemos importar sin fallos
from src.fit_parser import FitFileParser
from src.metrics import BiometricsCalculator

st.title("⚡ Athlex Analytics")
st.write("¡Motor de analítica deportiva conectado correctamente!")

# Prueba rápida del parser y métricas en la app
parser = FitFileParser()
df = parser.parse_to_dataframe()
calc = BiometricsCalculator(ftp=250, max_hr=190, rest_hr=50)
summary = calc.compute_full_summary(df)

st.success(f"¡Actividad cargada con éxito! Duración: {summary['duration_formatted']} | TSS: {summary['tss']}")
