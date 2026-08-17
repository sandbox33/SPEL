"""
core/monte_carlo.py
====================
`run_monte_carlo_validation()` -- validación de robustez de una señal de
gold_score mediante simulación GBM (Geometric Brownian Motion) vectorizada.
Responde a la pregunta "si el precio se mueve al azar dentro de su
volatilidad reciente, ¿la señal se sigue sosteniendo?" -- no reemplaza
`gold_score_bma()`, lo audita después de calculado.

FUENTE (spel_bayesian_core.py::run_monte_carlo_validation, verificada línea
por línea, no reescrita de memoria -- "port, don't rewrite").

HALLAZGO DE ESTA SESIÓN (confirmado con números, no supuesto): la fórmula
legacy de `T` (fracción de año que representa el horizonte) tenía un bug de
unidades. Su propio docstring dice "T = 15min / (252*24*4) = fracción de
año para 15min" -- pero `252*24*4` son PERIODOS de 15 min en el año
(252 días * 24h * 4 periodos/hora), no MINUTOS en el año. Dividir minutos
(15) entre un conteo de periodos-de-15-min (24192) en vez de entre
minutos-en-el-año (252*24*60=362880) infla T exactamente 15x:

    legacy_T  = 15 / (252*24*4)  = 0.00062004
    correct_T = 15 / (252*24*60) = 0.00004134
    ratio = 15.0000 exacto (verificado con Python, no a mano)

El término de volatilidad (`sigma * sqrt(T)`) queda inflado sqrt(15) ≈
3.87x -- la simulación "de 15 minutos" en realidad dispersa precios como
si fueran ~3.75 horas adelante. Fix: `T` se calcula desde
`horizon_minutes` y `trading_days_per_year` reales, ambos parametrizados
(la función original solo soportaba 15 min hardcodeado; parametrizar es
necesario para el motor multi-timeframe de la Fase 6 del BLUEPRINT, no un
cambio de alcance no pedido).

NO se recalibró `SENSITIVITY_MAP` -- se porta tal cual, con la misma nota
del legacy: "pendiente de calibración post Gate R30" / sin backtest. Es
un mapeo heurístico (price_diff * sensitivity ≈ delta en gold_score), no
una relación derivada -- declarado así, no disfrazado de fórmula exacta.

NO se portó `check_data_staleness()`, `get_rolling_kl()` ni
`append_bma_history()` -- pertenecen al ciclo de persistencia/orquestación
(`governance/persistence.py`, Fase 1 en progreso, orquestador en 0%), no a
la validación matemática en sí. Se portan cuando ese consumidor exista.

Validación pendiente (no resuelta acá, requiere datos reales): si
`SENSITIVITY_MAP` sigue siendo razonable con GDELT real corriendo, y si
`trading_days_per_year=252` uniforme para BTC/EURUSD (que operan ~365
días) subestima su horizonte real -- ver default parametrizable abajo,
no se fuerza un valor "correcto" sin evidencia.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


#: spel_bayesian_core.py::SENSITIVITY_MAP -- port directo, sin recalibrar.
#: Sensibilidad heurística de gold_score a movimiento de precio, por activo.
#: "pendiente de calibración post Gate R30" en la fuente -- sigue pendiente.
DEFAULT_SENSITIVITY_MAP: dict[str, float] = {
    "BTC": 0.07,
    "XAU": 0.04,
    "NVDA": 0.05,
    "NIFTY50": 0.04,
    "EURUSD": 0.02,
}

#: spel_bayesian_core.py: sensibilidad default para activos fuera del mapa.
FALLBACK_SENSITIVITY: float = 0.05

#: Umbral de aprobación: fracción de trayectorias simuladas con
#: sim_score > SUCCESS_SCORE_THRESHOLD que se requiere para MC_APPROVE.
#: spel_bayesian_core.py: ">= 850/1000 trayectorias con gold_score > 0.85".
SUCCESS_SCORE_THRESHOLD: float = 0.85
SUCCESS_RATE_THRESHOLD: float = 0.85

#: Calendario por defecto -- 252 días/año (convención de mercado de
#: acciones). BTC/EURUSD operan más días reales; parametrizable por
#: llamada, no forzado, ver docstring del módulo.
DEFAULT_TRADING_DAYS_PER_YEAR: float = 252.0
MINUTES_PER_DAY: float = 24 * 60


@dataclass(frozen=True)
class MonteCarloResult:
    """Resultado de una corrida de validación Monte Carlo."""

    mc_approved: bool
    success_rate: float
    p5_score: float
    p50_score: float
    p95_score: float
    sensitivity: float
    iterations: int
    asset: str
    horizon_minutes: float


def run_monte_carlo_validation(
    current_price: float,
    volatility: float,
    base_gold_score: float,
    asset: str = "UNKNOWN",
    *,
    iterations: int = 1000,
    horizon_minutes: float = 15.0,
    trading_days_per_year: float = DEFAULT_TRADING_DAYS_PER_YEAR,
    sensitivity_map: dict[str, float] | None = None,
    success_score_threshold: float = SUCCESS_SCORE_THRESHOLD,
    success_rate_threshold: float = SUCCESS_RATE_THRESHOLD,
    seed: int | None = None,
) -> MonteCarloResult:
    """
    Simula `iterations` trayectorias terminales GBM a `horizon_minutes`
    vista sobre `current_price`, traduce cada precio simulado a un
    gold_score simulado (heurística lineal por sensibilidad de activo), y
    reporta qué fracción sigue por encima de `success_score_threshold`.

    GBM (forma cerrada, un solo salto a T -- válido porque GBM es Markoviano,
    no hace falta simular el camino minuto a minuto para el precio
    terminal, por eso corre en <50ms vectorizado):
        S_T = S_0 * exp((mu - sigma^2/2)*T + sigma*sqrt(T)*Z),  Z ~ N(0,1)
        mu = 0.0 (drift neutro -- sin sesgo de dirección, deliberado: esto
             valida ROBUSTEZ de la señal ante ruido, no predice dirección)

    Args:
        current_price: precio actual del activo.
        volatility: volatilidad (desviación estándar de retornos) en la
            misma escala temporal que el resto del pipeline la calcule --
            esta función no la deriva, la recibe.
        base_gold_score: gold_score ya calculado (ej. por
            `core.scoring.compute_gold_score_bma`) que se está validando.
        asset: nombre del activo -- selecciona `sensitivity_map[asset]`.
        iterations: trayectorias a simular. Debe ser > 0.
        horizon_minutes: horizonte de la simulación en minutos. Default 15
            (igual que el legacy). Parametrizado para reusar esta función
            en 1/5/15/30 min sin duplicar código (Fase 6 del BLUEPRINT).
        trading_days_per_year: días de mercado asumidos por año, para
            anualizar `horizon_minutes` a `T`. Default 252 (convención de
            acciones) -- ver nota de módulo sobre activos 24/7.
        sensitivity_map: override de `DEFAULT_SENSITIVITY_MAP`.
        success_score_threshold: score simulado por encima del cual una
            trayectoria cuenta como "éxito".
        success_rate_threshold: fracción mínima de trayectorias exitosas
            para `mc_approved=True`.
        seed: semilla de `numpy.random.default_rng` -- None (default) usa
            aleatoriedad real; fijarla es solo para tests reproducibles.

    Raises:
        ValueError: `iterations <= 0`, `volatility < 0`,
            `horizon_minutes <= 0`, o `current_price <= 0`.

    Validación pendiente (F2, no resuelta acá): `sensitivity_map` es
    heurístico, sin backtest contra datos reales -- ver docstring del
    módulo.
    """
    if iterations <= 0:
        raise ValueError(f"iterations debe ser > 0, recibido {iterations}")
    if volatility < 0:
        raise ValueError(f"volatility no puede ser negativa, recibido {volatility}")
    if horizon_minutes <= 0:
        raise ValueError(f"horizon_minutes debe ser > 0, recibido {horizon_minutes}")
    if current_price <= 0:
        raise ValueError(f"current_price debe ser > 0, recibido {current_price}")

    smap = sensitivity_map if sensitivity_map is not None else DEFAULT_SENSITIVITY_MAP
    sensitivity = smap.get(asset, FALLBACK_SENSITIVITY)

    # FIX de esta sesión (ver docstring del módulo): T en fracción de año,
    # derivado de minutos reales -- no de un conteo de periodos que ya
    # incluía el horizonte, que inflaba T 15x para el caso horizon=15min.
    minutes_per_year = trading_days_per_year * MINUTES_PER_DAY
    T = horizon_minutes / minutes_per_year
    mu = 0.0  # drift neutro -- valida robustez ante ruido, no direcciona

    rng = np.random.default_rng(seed)
    z = rng.standard_normal(iterations)
    returns = np.exp((mu - 0.5 * volatility**2) * T + volatility * np.sqrt(T) * z)
    sim_prices = current_price * returns
    price_diff = (sim_prices - current_price) / current_price
    sim_scores = base_gold_score + (price_diff * sensitivity)
    sim_scores = np.clip(sim_scores, 0.0, 1.0)

    success_rate = float(np.mean(sim_scores > success_score_threshold))
    # FIX de esta sesión: np.percentile(..., [5,50,95]) devuelve un array
    # de 3 elementos -- envolverlo en float() (como hacía el legacy) lanza
    # TypeError. Se desempaqueta primero, se castea cada elemento después.
    p5, p50, p95 = np.percentile(sim_scores, [5, 50, 95])
    mc_approved = success_rate >= success_rate_threshold

    result = MonteCarloResult(
        mc_approved=mc_approved,
        success_rate=round(success_rate, 4),
        p5_score=round(float(p5), 4),
        p50_score=round(float(p50), 4),
        p95_score=round(float(p95), 4),
        sensitivity=sensitivity,
        iterations=iterations,
        asset=asset,
        horizon_minutes=horizon_minutes,
    )
    logger.debug(
        "monte_carlo asset=%s horizon=%.0fmin mc_approved=%s success_rate=%.4f",
        asset, horizon_minutes, result.mc_approved, result.success_rate,
    )
    return result
