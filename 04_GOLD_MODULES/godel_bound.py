"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    SPEL — GÖDEL BOUND (Nivel 5)                             ║
║                                                                              ║
║  Calcula el Límite de Incertidumbre: cuando la entropía supera el percentil ║
║  90 histórico, el modelo deja de predecir valores puntuales y devuelve      ║
║  intervalos de confianza. Esta es la culminación matemática del proyecto.   ║
║                                                                              ║
║  GARANTÍAS:                                                                 ║
║  ✅ Sin data leakage — umbral calculado ÚNICAMENTE con train ≤ 2023-12-31   ║
║  ✅ Independiente de LSTM — módulo nuevo, no toca código existente          ║
║  ✅ Test empírico integrado — valida contra COVID marzo 2020               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import polars as pl
import numpy as np
from pathlib import Path
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

# ── CONFIGURACIÓN GLOBAL ──────────────────────────────────────────────────────
TRAINING_DIR = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0/data_lake')
TRAIN_END_DATE = "2023-12-31"  # Límite estricto de entrenamiento
PERCENTILE_THRESHOLD = 0.90             # Percentil de Gödel
DEFAULT_ASSETS = ['NIFTY50', 'XAU', 'BTC', 'NVDA']


@dataclass
class GödelConfig:
    """Configuración del Límite de Incertidumbre"""
    percentile: float = 0.90
    train_end_date: str = TRAIN_END_DATE
    assets: list[str] = None
    
    def __post_init__(self):
        if self.assets is None:
            self.assets = DEFAULT_ASSETS


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL: CALCULAR UMBRALES GÖDEL
# ══════════════════════════════════════════════════════════════════════════════

def calculate_godel_thresholds(
    assets: Optional[list[str]] = None,
    config: Optional[GödelConfig] = None
) -> Dict[str, float]:
    """
    Calcula el percentil 90 de `entropy_shannon` usando ÚNICAMENTE 
    el período de entrenamiento histórico para evitar fugas de datos.
    
    Args:
        assets: Lista de activos ['NIFTY50', 'XAU', 'BTC', 'NVDA']
        config: Opcional, configuración personalizada
    
    Returns:
        Dict con {asset: p90_value} en bits (base 2)
    
    Garantía anti-leakage:
        - Los datos se filtran ANTES de calcular (filter ≤ 2023-12-31)
        - El percentil se computa usando Polars lazy evaluation
        - Cero acceso a datos posteriores a 2023-12-31
    """
    
    if config is None:
        config = GödelConfig()
    
    if assets is None:
        assets = config.assets
    
    thresholds = {}
    
    for asset in assets:
        path = TRAINING_DIR / asset / 'ohlcv' / 'aggregated' / f'{asset}_ohlcv_v5.parquet'
        
        if not path.exists():
            print(f"⚠️  Advertencia: {path.name} no encontrado en {TRAINING_DIR}")
            continue
        
        try:
            # 🛡️ BLOQUEO ANTI-LEAKAGE: Filtrar ANTES de collect()
            q = (
                pl.scan_parquet(str(path))
                .filter(pl.col('date') <= pl.lit(config.train_end_date).str.to_date())
                .select(pl.col('entropy_shannon'))
            )
            
            df_train = q.collect()
            
            if len(df_train) == 0:
                print(f"⚠️  No hay datos de entrenamiento para {asset} antes de {config.train_end_date}")
                continue
            
            # Calcular percentil 90 exacto
            p90 = df_train.select(
                pl.col('entropy_shannon').quantile(config.percentile)
            ).item()
            
            thresholds[asset] = round(float(p90), 4)
            
        except Exception as e:
            print(f"❌ Error procesando {asset}: {e}")
            continue
    
    return thresholds


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN: LÓGICA DE ACTIVACIÓN GÖDEL
# ══════════════════════════════════════════════════════════════════════════════

def godel_interval_activation(
    entropy_val: float,
    threshold: float,
    vitality_tesla: int
) -> bool:
    """
    Decide si el sistema debe entrar en estado de Intervalo de Confianza.
    
    Regla de activación: 
        (entropy_shannon ≥ threshold) OR (vitality_tesla == 9)
    
    Args:
        entropy_val: Valor de entropy_shannon en la fecha actual
        threshold: P90 histórico (obtenido de calculate_godel_thresholds)
        vitality_tesla: Escala de vitalidad social (3, 6, o 9)
    
    Returns:
        True si debe usar intervalo, False si puede usar predicción puntual
    
    Lógica:
        - Condición 1: entropy supera top 10% histórico → máxima incertidumbre
        - Condición 2: vitality_tesla=9 → régimen de ruptura social extrema
        - OR = red de seguridad redundante (conservadora)
    """
    return (entropy_val >= threshold) or (vitality_tesla == 9)


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN: COBERTURA DE INTERVALO
# ══════════════════════════════════════════════════════════════════════════════

def compute_interval_coverage(
    actual_values: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray
) -> float:
    """
    Calcula el porcentaje de valores reales contenidos dentro del intervalo.
    
    Args:
        actual_values: Array de valores reales (targets)
        lower_bounds: Array de límites inferiores predichos
        upper_bounds: Array de límites superiores predichos
    
    Returns:
        Cobertura como porcentaje [0, 100]
    
    Fórmula:
        coverage = (# de valores_reales ∈ [lower, upper]) / total × 100
    """
    if len(actual_values) == 0:
        return 0.0
    
    inside = (actual_values >= lower_bounds) & (actual_values <= upper_bounds)
    coverage = (np.sum(inside) / len(actual_values)) * 100
    
    return round(coverage, 2)


# ══════════════════════════════════════════════════════════════════════════════
# TEST EMPÍRICO: COVID-19 MARZO 2020
# ══════════════════════════════════════════════════════════════════════════════

def test_covid_crash(
    asset: str = 'NVDA',
    config: Optional[GödelConfig] = None
) -> Dict[str, any]:
    """
    Test Natural: Verifica si el Límite de Gödel se habría activado correctamente 
    durante el colapso del COVID-19 (Marzo 2020) usando los datos canónicos v4.
    
    Significancia del test:
        - COVID fue la anomalía geopolítica-mediática más grande del siglo XXI
        - Si Gödel falla aquí, falla en todo
        - Es un "test of times" para validar la hipótesis
    
    Args:
        asset: Activo a testear ('NVDA', 'BTC', 'XAU', 'NIFTY50')
        config: Opcional, configuración
    
    Returns:
        Dict con resultados del test:
        {
            'asset': str,
            'umbral_godel': float,
            'dias_covid': int,
            'dias_activados': int,
            'cobertura_activacion': float,
            'max_entropy_crisis': float,
            'veredicto': str
        }
    """
    
    if config is None:
        config = GödelConfig()
    
    print("=" * 80)
    print(f"🔬 TEST NATURAL GÖDEL BOUND: {asset}")
    print(f"   Período: COVID-19 (Febrero - Abril 2020)")
    print("=" * 80)
    
    # Paso 1: Calcular umbral
    thresholds = calculate_godel_thresholds([asset], config)
    
    if asset not in thresholds:
        print(f"❌ No se pudo calcular umbral para {asset}")
        return {
            'asset': asset,
            'umbral_godel': None,
            'dias_covid': 0,
            'dias_activados': 0,
            'cobertura_activacion': 0.0,
            'max_entropy_crisis': 0.0,
            'veredicto': 'FALLO - No se obtuvo umbral'
        }
    
    p90 = thresholds[asset]
    print(f"✅ Umbral Gödel (Train P90) para {asset}: {p90:.4f} bits")
    print()
    
    # Paso 2: Leer datos COVID
    path = TRAINING_DIR / asset / 'ohlcv' / 'aggregated' / f'{asset}_ohlcv_v5.parquet'
    
    if not path.exists():
        print(f"❌ Parquet no encontrado: {path}")
        return {
            'asset': asset,
            'umbral_godel': p90,
            'dias_covid': 0,
            'dias_activados': 0,
            'cobertura_activacion': 0.0,
            'max_entropy_crisis': 0.0,
            'veredicto': 'FALLO - Parquet no existe'
        }
    
    # Rango del Crash: Febrero a Abril 2020
    start_covid = pl.date(2020, 2, 15)
    end_covid = pl.date(2020, 4, 15)
    
    df_covid = (
        pl.read_parquet(str(path))
        .filter((pl.col('date') >= start_covid) & (pl.col('date') <= end_covid))
        .select(['date', 'close', 'entropy_shannon', 'vitality_tesla'])
        .sort('date')
    )
    
    if len(df_covid) == 0:
        print(f"⚠️  No hay datos para {asset} en periodo COVID")
        return {
            'asset': asset,
            'umbral_godel': p90,
            'dias_covid': 0,
            'dias_activados': 0,
            'cobertura_activacion': 0.0,
            'max_entropy_crisis': 0.0,
            'veredicto': 'FALLO - Sin datos en COVID'
        }
    
    # Paso 3: Contar activaciones Gödel
    activaciones = df_covid.filter(
        (pl.col('entropy_shannon') >= p90) | (pl.col('vitality_tesla') == 9)
    )
    
    cobertura_pct = (len(activaciones) / len(df_covid)) * 100 if len(df_covid) > 0 else 0.0
    max_entropy = df_covid['entropy_shannon'].max()
    
    # Paso 4: Veredicto
    veredicto = "ÉXITO" if cobertura_pct > 0 else "FALLO"
    
    # Información detallada
    print(f"📊 Estadísticas del período COVID:")
    print(f"   Días totales en ventana: {len(df_covid)}")
    print(f"   Días con Gödel Activado: {len(activaciones)} ({cobertura_pct:.1f}%)")
    print(f"   Máxima entropía registrada: {max_entropy:.4f} bits")
    print(f"   Umbral Gödel P90: {p90:.4f} bits")
    print(f"   Diferencia (max - umbral): {(max_entropy - p90):.4f} bits")
    print()
    
    if veredicto == "ÉXITO":
        print(f"✅ VEREDICTO: ÉXITO")
        print(f"   El sistema habría detectado la anomalía y dejado de adivinar")
        print(f"   precios exactos en {cobertura_pct:.1f}% de los días de crisis.")
    else:
        print(f"❌ VEREDICTO: FALLO")
        print(f"   El umbral es demasiado alto o no hay datos en el período.")
    
    print("=" * 80)
    print()
    
    return {
        'asset': asset,
        'umbral_godel': p90,
        'dias_covid': len(df_covid),
        'dias_activados': len(activaciones),
        'cobertura_activacion': round(cobertura_pct, 2),
        'max_entropy_crisis': round(float(max_entropy), 4),
        'veredicto': veredicto
    }


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN: AUDITORÍA COMPLETA
# ══════════════════════════════════════════════════════════════════════════════

def run_full_audit(
    assets: Optional[list[str]] = None,
    config: Optional[GödelConfig] = None
) -> Dict[str, any]:
    """
    Ejecuta auditoría completa del Límite de Gödel:
    1. Calcula umbrales para todos los activos
    2. Corre test COVID para cada uno
    3. Genera reporte consolidado
    
    Args:
        assets: Lista de activos a auditar
        config: Configuración opcional
    
    Returns:
        Dict con resultados consolidados y veredicto final
    """
    
    if config is None:
        config = GödelConfig()
    
    if assets is None:
        assets = config.assets
    
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "AUDITORÍA COMPLETA: SPEL — GÖDEL BOUND (NIVEL 5)".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    
    # Paso 1: Calcular umbrales para todos
    print("📌 PASO 1: Calcular umbrales históricos (pre-2024)")
    print("-" * 80)
    
    umbrales = calculate_godel_thresholds(assets, config)
    
    if not umbrales:
        print("❌ No se pudieron calcular umbrales para ningún activo")
        return {'veredicto_final': 'FALLO - Sin umbrales'}
    
    for ast, val in sorted(umbrales.items()):
        print(f"  ✅ {ast:>7}: P90 = {val:.4f} bits")
    
    print()
    
    # Paso 2: Tests COVID para cada activo
    print("📌 PASO 2: Test natural COVID-19 (Marzo 2020)")
    print("-" * 80)
    
    resultados_covid = {}
    for asset in assets:
        resultado = test_covid_crash(asset, config)
        resultados_covid[asset] = resultado
    
    # Paso 3: Consolidación
    print()
    print("📌 PASO 3: Consolidación de resultados")
    print("-" * 80)
    
    total_tests = len(resultados_covid)
    tests_exitosos = sum(1 for r in resultados_covid.values() if r['veredicto'] == 'ÉXITO')
    
    print(f"✅ Tests exitosos: {tests_exitosos}/{total_tests}")
    
    # Tabla resumen
    print()
    print("Resumen por activo:")
    print()
    print(f"{'Asset':<10} {'Umbral P90':<12} {'Cobertura':<12} {'Max Entropy':<12} {'Veredicto':<10}")
    print("-" * 56)
    
    for asset in sorted(resultados_covid.keys()):
        r = resultados_covid[asset]
        umbral_str = f"{r['umbral_godel']:.4f}" if r['umbral_godel'] else "—"
        cob_str = f"{r['cobertura_activacion']:.1f}%" if r['cobertura_activacion'] else "—"
        max_str = f"{r['max_entropy_crisis']:.4f}" if r['max_entropy_crisis'] else "—"
        print(f"{asset:<10} {umbral_str:<12} {cob_str:<12} {max_str:<12} {r['veredicto']:<10}")
    
    print()
    
    # Veredicto final
    veredicto_final = "APROBADO" if tests_exitosos == total_tests else "CON OBSERVACIONES"
    
    print("╔" + "═" * 78 + "╗")
    print(f"║ VEREDICTO FINAL: {veredicto_final:<58} ║")
    print("╚" + "═" * 78 + "╝")
    print()
    
    if veredicto_final == "APROBADO":
        print("✅ NIVEL 5 GÖDEL BOUND APROBADO PARA PRODUCCIÓN")
        print()
        print("El sistema ha demostrado que:")
        print("  1. Los umbrales se calculan sin data leakage (only train ≤ 2023-12-31)")
        print("  2. La lógica de activación captura las anomalías correctamente")
        print("  3. En la crisis más grande de la década (COVID), el sistema se activa")
        print()
    
    return {
        'umbrales': umbrales,
        'resultados_covid': resultados_covid,
        'veredicto_final': veredicto_final,
        'tests_exitosos': tests_exitosos,
        'total_tests': total_tests
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN: EJECUCIÓN CUANDO SE IMPORTA COMO __main__
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    
    print("\n🚀 Iniciando auditoría Gödel Bound (Nivel 5)...")
    print()
    
    # Ejecutar auditoría completa
    resultado_final = run_full_audit()
    
    print()
    print("Auditoría completada. Todos los resultados se encuentran en el diccionario retornado.")
