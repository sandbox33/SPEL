"""
ingestion/training_dataset.py
================================
Une OHLCV (ingestion/adapters.py, hoy: DerivAdapter) con la serie GDELT
persistida (ingestion/gdelt_series.py) en un dataset de entrenamiento
validado -- el primer código de la Fase 2 (BLUEPRINT.md: "ML /
entrenamiento, 4,303 líneas legacy, 0% portado, bloqueado por Fase 2").

POR QUÉ ESTE ARCHIVO EXISTE, EXACTO (contexto de Altair, 2026-08-18):
el LSTM anterior se detuvo después de ~4 meses de trabajo al descubrir
que los parquets fuente tenían columnas de fecha en formatos distintos
entre sí (algunos con '/', otros con '-') -- datos "no normalizados"
alimentando el entrenamiento sin que nadie lo hubiera detectado a
tiempo. Este módulo es la respuesta directa a eso, no una función
genérica de conveniencia.

CÓMO SE EVITA EL MISMO PROBLEMA ACÁ (verificado, no prometido):
  1. OHLCV nunca pasa por un parser de fechas ambiguo -- Deriv entrega
     epoch (segundos Unix, un entero, cero ambigüedad de formato) y
     `ingestion/adapters.py::_to_dataframe()` ya lo convierte a
     datetime64 UTC-aware antes de que este módulo lo vea.
  2. `validate_ohlcv_schema()` (ya existente, ingestion/adapters.py) se
     re-usa acá tal cual, no se reimplementa una versión paralela --
     mismo contrato, un solo lugar que lo define (Tamiz #3).
  3. La serie GDELT usa `datetime.date` (ingestion/gdelt_series.py),
     tipo nativo de Python sin ambigüedad de formato -- nunca un string
     parseado. El join entre OHLCV (timestamp UTC) y GDELT (date) se
     hace explícito acá, en una sola línea auditable
     (`timestamp.dt.date`), no dos formatos de fecha adivinados y
     forzados a coincidir.
  4. Cualquier desalineación real (huecos, activo equivocado, fechas
     fuera de rango) se REPORTA en BuildDatasetResult -- coverage_ratio,
     n_dropped_no_entropy -- nunca se completa en silencio con un
     valor inventado.

ESTRATEGIA DE ALINEACIÓN TEMPORAL -- port de
gdelt_foundation.py::join_entropy_to_price(), adaptado a pandas:
OHLCV solo tiene días de trading (NVDA ~252/año), GDELT calcula
entropía 7/7 (incluye fines de semana). Forward-fill: el mercado abre
el lunes "procesando" la entropía acumulada del fin de semana -- mismo
razonamiento del legacy, no reinventado. Días de OHLCV anteriores al
primer día GDELT disponible (sin entropía previa que forward-fillear)
se excluyen y se cuentan en `n_dropped_no_entropy` -- nunca rellenados
con un placeholder.

QUÉ NO HACE ESTE MÓDULO, a propósito: no separa train/val/test, no
ajusta ningún scaler, no arma tensores. Eso es trabajo del trainer, que
todavía no existe. Se documenta acá, para que no se pierda, la lección
encontrada auditando el legacy (spel_trainer_audit.py, BUG-LA-01): el
scaler se ajusta SOLO sobre el split de train, nunca sobre el dataset
completo, y el split es por fecha (nunca `shuffle=True` sobre datos
temporales) -- spel_patch_coordinated.py ya lo hacía bien
(`scaler.fit(X_train_raw)`, split por corte de fecha antes de tocar el
scaler). Cuando se escriba el trainer, ese es el patrón a seguir, no
uno nuevo inventado bajo presión de tiempo.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ingestion.adapters import validate_ohlcv_schema
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import read_series

logger = logging.getLogger("spel.ingestion.training_dataset")


@dataclass(frozen=True)
class TrainingRow:
    """Una fila lista para entrenamiento -- precio + entropía alineados
    para el mismo día de trading, sin ambigüedad de formato de fecha."""

    date: date
    asset: str
    close: float
    entropy_shannon: float
    zipf_concentration: float
    goldstein_mean: float
    tone_variance: float
    n_events: int
    entropy_is_forward_filled: bool  # True si vino de un día GDELT anterior, no del mismo día


@dataclass(frozen=True)
class BuildDatasetResult:
    """Resultado completo del merge -- nunca solo las filas. Los
    conteos existen para que un coverage_ratio bajo sea visible, no
    descubierto meses después como en el LSTM anterior."""

    rows: list[TrainingRow]
    asset: str
    n_ohlcv_days: int
    n_gdelt_days_available: int
    n_dropped_no_entropy: int  # días OHLCV antes del primer día GDELT válido
    coverage_ratio: float  # len(rows) / n_ohlcv_days, 0.0 si n_ohlcv_days == 0


def build_training_dataset(ohlcv: pd.DataFrame, asset: str) -> BuildDatasetResult:
    """
    Args:
        ohlcv: DataFrame ya validado por el adapter que lo produjo (ej.
            DerivAdapter.fetch_ohlcv) -- se re-valida acá igual, nunca se
            confía en que el caller lo hizo bien (Tamiz #4).
        asset: activo -- debe tener serie GDELT persistida
            (ingestion/gdelt_series.py) para producir filas con
            coverage_ratio > 0. Cero historia GDELT es un resultado
            válido (coverage_ratio=0.0), no un error.

    Raises:
        Cualquier excepción que validate_ohlcv_schema() ya lance --
        mismo contrato, no una copia debilitada.
    """
    validate_ohlcv_schema(ohlcv, source="training_dataset", symbol=asset)

    gdelt_rows = [r for r in read_series(asset) if r.entropy_shannon is not None]
    gdelt_dates: list[date] = [r.day for r in gdelt_rows]  # ya viene ascendente

    ohlcv_dates = ohlcv["timestamp"].dt.date

    rows: list[TrainingRow] = []
    n_dropped_no_entropy = 0

    for ts_date, close in zip(ohlcv_dates, ohlcv["close"]):
        # Último día GDELT <= ts_date -- forward-fill explícito, búsqueda
        # binaria sobre una lista ya ordenada (gdelt_dates), no un
        # escaneo lineal ni un pd.merge_asof implícito difícil de auditar.
        idx = bisect_right(gdelt_dates, ts_date) - 1
        if idx < 0:
            n_dropped_no_entropy += 1
            continue

        g = gdelt_rows[idx]
        rows.append(TrainingRow(
            date=ts_date, asset=asset, close=float(close),
            entropy_shannon=g.entropy_shannon,
            zipf_concentration=g.zipf_concentration,
            goldstein_mean=g.goldstein_mean,
            tone_variance=g.tone_variance,
            n_events=g.n_events,
            entropy_is_forward_filled=(g.day != ts_date),
        ))

    n_ohlcv_days = len(ohlcv)
    coverage_ratio = (len(rows) / n_ohlcv_days) if n_ohlcv_days > 0 else 0.0

    result = BuildDatasetResult(
        rows=rows, asset=asset, n_ohlcv_days=n_ohlcv_days,
        n_gdelt_days_available=len(gdelt_rows),
        n_dropped_no_entropy=n_dropped_no_entropy,
        coverage_ratio=round(coverage_ratio, 4),
    )
    logger.info(
        "training_dataset: %s -- %d/%d días con cobertura (%.1f%%), %d sin entropía previa",
        asset, len(rows), n_ohlcv_days, coverage_ratio * 100, n_dropped_no_entropy,
    )
    return result
