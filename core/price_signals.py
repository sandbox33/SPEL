"""
core/price_signals.py
=======================
`compute_transfer_entropy_proxy()` y `compute_backbone_score()` -- 2 de
los 3 inputs que compute_gold_score_bma() necesita (core/scoring.py,
línea 6: "Pendiente... inputs externos, no calculados por este módulo
todavía"). El tercero, godel_score, NO está acá -- ver más abajo por qué.

FUENTE (spel_score_engine.py::GoldScoreEngine._compute_transfer_entropy
y ._compute_backbone, verificadas línea por línea).

BUG ENCONTRADO Y CORREGIDO ESTA SESIÓN (confirmado con grep, no
supuesto): el archivo fuente define `_get_np()` / `_get_pl()` como
lazy-loaders ("Ley 2: Lazy singleton guard, auto-generado por SPEL4
AST-Rewriter") pero NUNCA los llama -- ninguna línea hace `np =
_get_np()`. Cada uso de `np.clip`, `np.diff`, `np.log`,
`np.histogram2d`, `np.std` en todo el archivo referencia un nombre `np`
que no existe en ningún scope. El rewrite automático que generó los
lazy-loaders nunca terminó de reemplazar los usos -- exactamente lo que
el propio audit de la fuente ya marcaba ("25 broken_np_pl_refs
pendientes de cirugía AST", confirmado acá con un grep real, no
repetido de memoria). El archivo, tal cual está, lanza NameError en el
primer llamado a cualquiera de estas 2 funciones. Fix: `import numpy as
np` normal a nivel de módulo, igual que core/scoring.py y
core/monte_carlo.py -- este repo no usa el patrón lazy-loader del
legacy en ningún lado, no se introduce acá tampoco.

ADAPTACIÓN DELIBERADA (no un port ciego): el legacy devuelve 0.0 (TE) o
0.5 (backbone) en silencio cuando no hay suficiente historia --
exactamente el patrón "valor disfrazado de dato real" que
DailyAggregationResult.insufficient_events y NashFrozenResult.
insufficient_data ya evitan en el resto de este repo. Acá se hace lo
mismo: `insufficient_data=True` explícito, el valor numérico es
placeholder marcado como tal, no un 0.0/0.5 mudo.

QUÉ NO ESTÁ ACÁ, a propósito: `godel_score`. En el legacy,
`godel_score = float(godel_active) * val_dir if godel_active else 0.0`
-- depende de `val_dir`, la salida de INFERENCIA DE UN MODELO LSTM
entrenado (capa_c_inference.SPELInferenceEngine), que es el mismo
componente bloqueado en Fase 2 (~0.50 val_accuracy, diagnóstico sin
arrancar). No hay nada que portar acá todavía -- godel_score sigue
bloqueado hasta que Fase 2 resuelva el modelo. Cuando el LSTM esté
entrenado y sirviendo inferencia real, ESE es el momento de portar
`_run_inference` -- no antes, y no con un valor inventado mientras tanto.

_ASSET_TYPE_MAP y _WEIGHTS del archivo fuente NO se portan -- ya existe
una versión propia, auditada y testeada en core/scoring.py::BMA_WEIGHTS
(coincide en los pesos 0.40/0.30/0.30 y 0.55/0.45/0.00, pero la fuente
legacy clasifica NIFTY50 y SPY como "SYNTHETIC_INDEX", que es
inconsistente con CORE_COUNTRY_FILTERS de este mismo repo y con el
sentido común -- SPY es una acción real, no un índice sintético.
Discrepancia registrada, no propagada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: spel_score_engine.py: bins=5, lookback=63 días (hardcoded en el
#: legacy). Parametrizados acá, defaults idénticos al legacy.
DEFAULT_TE_BINS = 5
DEFAULT_TE_LOOKBACK_DAYS = 63
DEFAULT_TE_MIN_CLOSES = 10

#: spel_score_engine.py: EMA 20 vs EMA 63, sensitivity=5.0 (hardcoded).
DEFAULT_BACKBONE_EMA_FAST = 20
DEFAULT_BACKBONE_EMA_SLOW = 63
DEFAULT_BACKBONE_SENSITIVITY = 5.0


@dataclass(frozen=True)
class TransferEntropyResult:
    value: float          # [0,1]. Placeholder (0.0) si insufficient_data=True.
    insufficient_data: bool
    n_closes_used: int


@dataclass(frozen=True)
class BackboneResult:
    value: float          # [0,1]. Placeholder (0.5, neutral) si insufficient_data=True.
    insufficient_data: bool
    ema_fast_last: float | None
    ema_slow_last: float | None


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    """spel_score_engine.py::GoldScoreEngine._ema -- port directo, sin
    cambios (la fórmula en sí no tenía el bug de np, solo lo usaba)."""
    alpha = 2.0 / (span + 1)
    result = np.empty_like(arr)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def compute_transfer_entropy_proxy(
    closes: Sequence[float] | np.ndarray,
    *,
    bins: int = DEFAULT_TE_BINS,
    lookback_days: int = DEFAULT_TE_LOOKBACK_DAYS,
    min_closes: int = DEFAULT_TE_MIN_CLOSES,
) -> TransferEntropyResult:
    """
    Proxy de Transfer Entropy vía información mutua discreta entre
    retorno log actual y su rezago 1 -- NO es Transfer Entropy de
    Schreiber (2000) completa (esa requiere discretizar en 3 estados y
    contar probabilidades condicionales exactas; ver
    _transfer_entropy_manual en spel_math_engine.py, archivo con 177
    broken_np_pl_refs propios -- no auditado esta sesión, queda como
    mejora futura documentada, no fabricada acá). Esto es el proxy
    "rápido en stdlib/numpy" que el propio legacy ya distinguía como tal
    en su docstring.

    I(X_t; X_{t-1}) normalizada por el máximo teórico log2(bins).

    Args:
        closes: precios de cierre, orden cronológico (más viejo primero).
        bins: bins del histograma 2D para estimar la densidad conjunta.
        lookback_days: cuántos cierres finales usar (legacy: 63 = ~63
            días de trading).
        min_closes: mínimo de cierres (post-lookback) para considerar el
            resultado confiable -- por debajo, insufficient_data=True.
    """
    arr = np.asarray(closes, dtype=float)
    tail = arr[-lookback_days:] if len(arr) > lookback_days else arr

    if len(tail) < min_closes:
        return TransferEntropyResult(value=0.0, insufficient_data=True, n_closes_used=len(tail))

    ret = np.diff(np.log(tail + 1e-9))
    x, y = ret[1:], ret[:-1]

    h_xy, _, _ = np.histogram2d(x, y, bins=bins)
    h_x, _ = np.histogram(x, bins=bins)
    h_y, _ = np.histogram(y, bins=bins)
    h_xy = h_xy / (h_xy.sum() + 1e-12)
    h_x = h_x / (h_x.sum() + 1e-12)
    h_y = h_y / (h_y.sum() + 1e-12)

    mi = 0.0
    for i in range(bins):
        for j in range(bins):
            if h_xy[i, j] > 1e-12:
                mi += h_xy[i, j] * np.log2(h_xy[i, j] / (h_x[i] * h_y[j] + 1e-12))

    value = float(np.clip(mi / (np.log2(bins) + 1e-9), 0.0, 1.0))
    return TransferEntropyResult(value=value, insufficient_data=False, n_closes_used=len(tail))


def compute_backbone_score(
    closes: Sequence[float] | np.ndarray,
    *,
    ema_fast: int = DEFAULT_BACKBONE_EMA_FAST,
    ema_slow: int = DEFAULT_BACKBONE_EMA_SLOW,
    sensitivity: float = DEFAULT_BACKBONE_SENSITIVITY,
) -> BackboneResult:
    """
    Tendencia backbone: cruce EMA rápida vs EMA lenta, normalizado a
    [0,1] con centro en 0.5 (sin tendencia). NO es una predicción de
    dirección -- es una medida continua de "qué tan por encima/debajo
    está la tendencia corta de la larga", pensada como uno de los 3
    inputs de compute_gold_score_bma(), no como señal standalone.

        delta = (EMA_fast[-1] - EMA_slow[-1]) / |EMA_slow[-1]|
        backbone_score = clip(0.5 + delta * sensitivity, 0, 1)

    Requiere al menos `ema_slow` cierres -- con menos, la EMA lenta no
    es representativa (arranca desde el primer punto, sesgada por muy
    poca historia). insufficient_data=True en ese caso, value=0.5
    (neutral) es un placeholder marcado, no una lectura real de "sin
    tendencia".
    """
    arr = np.asarray(closes, dtype=float)
    if len(arr) < ema_slow:
        return BackboneResult(value=0.5, insufficient_data=True, ema_fast_last=None, ema_slow_last=None)

    ema_f = _ema(arr, ema_fast)
    ema_s = _ema(arr, ema_slow)
    delta = (ema_f[-1] - ema_s[-1]) / (abs(ema_s[-1]) + 1e-9)
    value = float(np.clip(0.5 + delta * sensitivity, 0.0, 1.0))

    return BackboneResult(
        value=value, insufficient_data=False,
        ema_fast_last=float(ema_f[-1]), ema_slow_last=float(ema_s[-1]),
    )
