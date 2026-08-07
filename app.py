from src.fit_parser import FitFileParser
from src.metrics import BiometricsCalculator

print("🚀 Probando el Pipeline Completo de Athlex Analytics...\n")

# 1. Parsear / Ingestar los datos de la actividad
parser = FitFileParser()
df_activity = parser.parse_to_dataframe()

print(f"📊 Actividad cargada correctamente. Filas: {len(df_activity)}")

# 2. Calcular métricas utilizando el motor biométrico
calculator = BiometricsCalculator(ftp=250, max_hr=190, rest_hr=50)
summary = calculator.compute_full_summary(df_activity)

print("\n✅ Resumen de Rendimiento Obtenido:")
for key, value in summary.items():
    print(f"  • {key}: {value}")
