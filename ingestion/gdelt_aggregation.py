"""
ingestion/gdelt_aggregation.py
================================
Conecta ingestion/gdelt.py (eventos crudos, un día) con core/scoring.py
(entropy_shannon, goldstein_mean, etc. -- inputs de nash_frozen_7d,
mass_panic_index, gold_score_bma). Sin esto, GDELTDailyAdapter descarga
eventos que nadie puede usar -- las funciones de core/scoring.py
esperan una serie histórica de entropía diaria, no una lista de eventos
individuales.

REUSA, no duplica:
  - core.scoring.classify_gdelt_event() para el filtro por país. La
    fuente (gdelt_foundation.py::_filter_by_asset) hace exactamente lo
    mismo que esa función ya hace -- filtrar por Actor1CountryCode/
    Actor2CountryCode contra la lista del activo. Reimplementar el
    filtro acá hubiera sido dos versiones del mismo concepto (Tamiz 3).

FUENTE (gdelt_foundation.py::EntropyCalculator.compute_daily_signals +
_shannon_entropy + _zipf_concentration, verificada línea por línea):
  entropy_shannon    = H = -Σ p(i)·log2(p(i)) sobre AvgTone discretizado
                       en 20 bins, rango [-100,100].
  zipf_concentration = Σ (s_i/S_total)² sobre NumSources (Herfindahl).
  goldstein_mean     = media de GoldsteinScale (con drop_nulls, no 0.0
                       para los nulos -- un evento sin goldstein no debe
                       arrastrar la media hacia 0 artificialmente).
  tone_variance      = varianza de AvgTone.
  n_events           = conteo de eventos que pasaron el filtro.
  Mínimo 5 eventos tras filtrar -- por debajo de eso, gdelt_foundation.py
  descarta el día entero (return None) por señal no confiable.

DELIBERADAMENTE NO PORTADO en este patch: nash_frozen_7d y
vitality_tesla NO se calculan acá. Esa lógica YA vive en
core/scoring.py (compute_nash_frozen_7d, compute_vitality_tesla) y
opera sobre una SERIE de días ya agregados, no sobre un día individual
-- este módulo produce esa serie, día por día; quien la acumula
(Parquet, lista en memoria, lo que sea) y se la pasa a esas funciones
es responsabilidad de un pipeline de más arriba, todavía no escrito.
Escribirlas acá hubiera sido una segunda implementación de algo que ya
existe y ya está testeado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from core.scoring import GdeltPipeline, classify_gdelt_event

#: gdelt_foundation.py::EntropyCalculator.N_TONE_BINS.
N_TONE_BINS = 20

#: Rango real de AvgTone en GDELT 1.0 (-100 a +100, confirmado en el
#: docstring de gdelt_foundation.py sobre esa columna).
TONE_RANGE = (-100.0, 100.0)

#: gdelt_foundation.py::compute_daily_signals -- "if ... < 5: return None".
MIN_EVENTS_FOR_VALID_DAY = 5


@dataclass(frozen=True)
class DailyAggregationResult:
    """Nunca produce una fila con valores inventados cuando no hay
    suficiente señal -- ver insufficient_events. Mismo patrón que
    insufficient_data en el resto de core/scoring.py: el campo bool
    manda, no un valor 0.0 disfrazado de dato real."""
    day: date
    asset: str
    entropy_shannon: Optional[float]
    zipf_concentration: Optional[float]
    goldstein_mean: Optional[float]
    tone_variance: Optional[float]
    n_events: int
    insufficient_events: bool


def _shannon_entropy(tones: list[float]) -> float:
    """H = -Σ p(i)·log2(p(i)) sobre tonos discretizados en N_TONE_BINS
    bins uniformes sobre TONE_RANGE. Sin numpy -- histograma manual, ya
    que este módulo no necesita el resto del ecosistema numpy y sumar
    la dependencia solo para un histograma de 20 bins sería peso sin
    necesidad real (misma decisión que ya se tomó en ingestion/gdelt.py
    para no sumar polars)."""
    if len(tones) < 2:
        return 0.0

    low, high = TONE_RANGE
    bin_width = (high - low) / N_TONE_BINS
    counts = [0] * N_TONE_BINS
    for tone in tones:
        clamped = max(low, min(high, tone))
        idx = int((clamped - low) / bin_width)
        if idx >= N_TONE_BINS:  # el valor exacto 'high' cae fuera por un pelo
            idx = N_TONE_BINS - 1
        counts[idx] += 1

    total = sum(counts)
    entropy = 0.0
    for count in counts:
        if count == 0:
            continue
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _zipf_concentration(sources: list[float]) -> float:
    """Herfindahl sobre NumSources: Σ (s_i/S_total)²."""
    total = sum(sources)
    if not sources or total == 0:
        return 0.0
    return sum((s / total) ** 2 for s in sources)


def aggregate_day(events: list[dict], asset: str, day: date) -> DailyAggregationResult:
    """
    Un día de eventos GDELT crudos (GdeltDayResult.events de
    ingestion/gdelt.py) -> una fila de señales agregadas.

    Filtra primero con classify_gdelt_event() (reuso, no duplicado --
    ver docstring del módulo), después calcula las 5 señales sobre los
    eventos que pasaron el filtro. Con < MIN_EVENTS_FOR_VALID_DAY tras
    filtrar, insufficient_events=True y el resto de campos en None --
    NUNCA un 0.0 disfrazado de "no hubo entropía ese día", que sería
    indistinguible de un día real de máxima certeza informativa.

    Args:
        events: lista de dicts con el esquema de GdeltDayResult.events
            (date_int, country1, country2, goldstein, num_mentions,
            num_sources, num_articles, avg_tone).
        asset: activo CORE contra el que filtrar (ver
            core.scoring.CORE_COUNTRY_FILTERS para los válidos).
        day: fecha de este batch de eventos.
    """
    filtrados = [
        evt for evt in events
        if classify_gdelt_event(
            [evt.get("country1"), evt.get("country2")], asset=asset,
        ).pipeline == GdeltPipeline.CORE
    ]

    if len(filtrados) < MIN_EVENTS_FOR_VALID_DAY:
        return DailyAggregationResult(
            day=day, asset=asset,
            entropy_shannon=None, zipf_concentration=None,
            goldstein_mean=None, tone_variance=None,
            n_events=len(filtrados), insufficient_events=True,
        )

    tones = [e["avg_tone"] for e in filtrados if e.get("avg_tone") is not None]
    sources = [e["num_sources"] for e in filtrados if e.get("num_sources") is not None]
    goldstein_values = [e["goldstein"] for e in filtrados if e.get("goldstein") is not None]

    entropy = _shannon_entropy(tones)
    zipf = _zipf_concentration(sources)
    goldstein_mean = (sum(goldstein_values) / len(goldstein_values)) if goldstein_values else 0.0

    if len(tones) > 1:
        mean_tone = sum(tones) / len(tones)
        tone_variance = sum((t - mean_tone) ** 2 for t in tones) / len(tones)
    else:
        tone_variance = 0.0

    return DailyAggregationResult(
        day=day, asset=asset,
        entropy_shannon=entropy, zipf_concentration=zipf,
        goldstein_mean=goldstein_mean, tone_variance=tone_variance,
        n_events=len(filtrados), insufficient_events=False,
    )
