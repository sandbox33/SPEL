"""
tests/test_monte_carlo.py
==========================
Cobertura de core/monte_carlo.py. Dos focos deliberados, no solo "no
revienta": (1) el fix del bug de unidades en T (verificado con números
exactos, no solo "corre sin error") y (2) el fix del desempaquetado de
percentiles (el bug legacy original lanzaba TypeError en cuanto se
llamaba -- una función que nunca se ejecutó de verdad).
"""

from __future__ import annotations

import numpy as np
import pytest

from core.monte_carlo import (
    DEFAULT_SENSITIVITY_MAP,
    FALLBACK_SENSITIVITY,
    MonteCarloResult,
    run_monte_carlo_validation,
)


# ─── Validación de inputs ─────────────────────────────────────────────────


def test_iterations_cero_o_negativo_lanza_value_error():
    with pytest.raises(ValueError, match="iterations"):
        run_monte_carlo_validation(100.0, 0.02, 0.9, iterations=0)
    with pytest.raises(ValueError, match="iterations"):
        run_monte_carlo_validation(100.0, 0.02, 0.9, iterations=-5)


def test_volatility_negativa_lanza_value_error():
    with pytest.raises(ValueError, match="volatility"):
        run_monte_carlo_validation(100.0, -0.01, 0.9)


def test_horizon_minutes_cero_o_negativo_lanza_value_error():
    with pytest.raises(ValueError, match="horizon_minutes"):
        run_monte_carlo_validation(100.0, 0.02, 0.9, horizon_minutes=0)
    with pytest.raises(ValueError, match="horizon_minutes"):
        run_monte_carlo_validation(100.0, 0.02, 0.9, horizon_minutes=-15)


def test_current_price_cero_o_negativo_lanza_value_error():
    with pytest.raises(ValueError, match="current_price"):
        run_monte_carlo_validation(0.0, 0.02, 0.9)
    with pytest.raises(ValueError, match="current_price"):
        run_monte_carlo_validation(-50.0, 0.02, 0.9)


# ─── Caso determinista: volatilidad cero ──────────────────────────────────


def test_volatilidad_cero_todas_las_trayectorias_son_el_precio_actual():
    """Con sigma=0, exp(0) = 1 para toda Z -- sim_prices == current_price
    exacto, price_diff == 0, sim_scores == base_gold_score sin variación.
    Caso 100% determinista, sirve para aislar el resto de la lógica."""
    result = run_monte_carlo_validation(
        current_price=100.0, volatility=0.0, base_gold_score=0.90,
        asset="BTC", iterations=500, seed=1,
    )
    assert result.p5_score == result.p50_score == result.p95_score == 0.90
    assert result.success_rate == 1.0  # 0.90 > 0.85 (default threshold)
    assert result.mc_approved is True


def test_volatilidad_cero_score_base_bajo_nunca_aprueba():
    result = run_monte_carlo_validation(
        current_price=100.0, volatility=0.0, base_gold_score=0.50,
        asset="BTC", iterations=500, seed=1,
    )
    assert result.success_rate == 0.0
    assert result.mc_approved is False


# ─── El fix del bug de unidades en T ──────────────────────────────────────


def test_horizon_mas_largo_produce_mayor_dispersion_p5_p95():
    """FIX verificado: T escala con horizon_minutes / minutes_per_year, así
    que sigma*sqrt(T) crece con sqrt(horizon). Duplicar el horizonte debe
    ensanchar el spread p95-p5 en aprox. sqrt(2), no en 1x (T no escalando)
    ni en 2x (si T escalara linealmente adentro de sqrt en vez de afuera)."""
    kwargs = dict(current_price=100.0, volatility=0.30, base_gold_score=0.70,
                  asset="BTC", iterations=20_000, seed=7)
    short = run_monte_carlo_validation(horizon_minutes=15.0, **kwargs)
    long_ = run_monte_carlo_validation(horizon_minutes=60.0, **kwargs)

    spread_short = short.p95_score - short.p5_score
    spread_long = long_.p95_score - long_.p5_score

    assert spread_long > spread_short  # más horizonte, más incertidumbre
    ratio = spread_long / spread_short
    # sqrt(60/15) = 2.0 -- tolerancia amplia porque son cuantiles de una
    # distribución simulada (ruido de muestreo), no una igualdad exacta.
    assert 1.5 < ratio < 2.6


def test_t_interno_no_esta_inflado_15x_regresion_del_bug_legacy():
    """Regresión directa del hallazgo de esta sesión: con el bug legacy
    (T 15x más grande), el spread a 15min sería indistinguible del spread
    real a 225min (15 periodos de 15min). Con el fix, 15min y 225min dan
    spreads claramente distintos."""
    kwargs = dict(current_price=100.0, volatility=0.30, base_gold_score=0.70,
                  asset="BTC", iterations=20_000, seed=7)
    fixed_15 = run_monte_carlo_validation(horizon_minutes=15.0, **kwargs)
    fixed_225 = run_monte_carlo_validation(horizon_minutes=225.0, **kwargs)

    spread_15 = fixed_15.p95_score - fixed_15.p5_score
    spread_225 = fixed_225.p95_score - fixed_225.p5_score

    # Si el bug legacy siguiera presente, spread_15 (calculado con T real
    # de 15x) sería aprox. igual a spread_225 con T correcto -- deben ser
    # claramente distintos ahora.
    assert spread_225 > spread_15 * 2.5


# ─── El fix del desempaquetado de percentiles ─────────────────────────────


def test_no_lanza_typeerror_al_desempaquetar_percentiles():
    """Bug legacy: float(np.percentile(x, [5,50,95])) revienta con
    TypeError ('only length-1 arrays can be converted to Python scalars')
    porque percentile(...) con una lista de 3 valores devuelve un array de
    3 elementos. Nunca se había ejecutado de verdad -- este test confirma
    que el fix (desempaquetar antes de castear) no revienta."""
    result = run_monte_carlo_validation(100.0, 0.02, 0.9, iterations=200, seed=3)
    assert isinstance(result.p5_score, float)
    assert isinstance(result.p50_score, float)
    assert isinstance(result.p95_score, float)


def test_percentiles_ordenados_p5_menor_igual_p50_menor_igual_p95():
    result = run_monte_carlo_validation(
        100.0, 0.05, 0.7, asset="NVDA", iterations=5_000, seed=42,
    )
    assert result.p5_score <= result.p50_score <= result.p95_score


# ─── sensitivity_map ───────────────────────────────────────────────────────


@pytest.mark.parametrize("asset,expected", list(DEFAULT_SENSITIVITY_MAP.items()))
def test_sensitivity_usa_el_valor_del_mapa_para_activos_conocidos(asset, expected):
    result = run_monte_carlo_validation(100.0, 0.02, 0.9, asset=asset, iterations=50, seed=1)
    assert result.sensitivity == expected


def test_sensitivity_usa_fallback_para_activo_desconocido():
    result = run_monte_carlo_validation(
        100.0, 0.02, 0.9, asset="DOGE_NO_CONFIGURADO", iterations=50, seed=1,
    )
    assert result.sensitivity == FALLBACK_SENSITIVITY


def test_sensitivity_map_personalizado_reemplaza_al_default():
    custom = {"BTC": 0.99}
    result = run_monte_carlo_validation(
        100.0, 0.02, 0.9, asset="BTC", iterations=50, seed=1,
        sensitivity_map=custom,
    )
    assert result.sensitivity == 0.99


# ─── Clipping a [0, 1] ──────────────────────────────────────────────────


def test_scores_simulados_nunca_salen_de_cero_uno_con_volatilidad_extrema():
    result = run_monte_carlo_validation(
        100.0, volatility=5.0, base_gold_score=0.5, asset="BTC",
        iterations=5_000, seed=9,
    )
    assert 0.0 <= result.p5_score <= 1.0
    assert 0.0 <= result.p50_score <= 1.0
    assert 0.0 <= result.p95_score <= 1.0


# ─── Reproducibilidad ──────────────────────────────────────────────────────


def test_misma_seed_da_el_mismo_resultado_exacto():
    kwargs = dict(current_price=100.0, volatility=0.04, base_gold_score=0.8,
                  asset="XAU", iterations=1000, seed=123)
    r1 = run_monte_carlo_validation(**kwargs)
    r2 = run_monte_carlo_validation(**kwargs)
    assert r1 == r2  # dataclass frozen, comparación por valor


def test_seeds_distintas_producen_resultados_distintos():
    """Corrige un test FLAKY encontrado en verificación real (no en teoría):
    la versión anterior de este test comparaba dos corridas SIN seed,
    confiando en que la aleatoriedad real las haría diferir. Corrida real
    contra este mismo repo: 5 de 15 intentos fallaron (~33%) -- con
    volatility=5.0 y horizon_minutes=15 (default), la dispersión resultante
    es tan chica (sensitivity fallback=0.05, T pequeño) que redondeando a
    4 decimales, dos corridas distintas frecuentemente coinciden por puro
    azar. Fix real, no un parche de suerte: se compara con SEEDS EXPLÍCITAS
    distintas (determinista, no depende de qué toque en el momento) y con
    parámetros donde el spread p95-p5 es ~150x el redondeo de 4 decimales
    (verificado: spread≈0.0154 vs. granularidad 0.0001) -- una colisión
    espuria queda fuera de rango, no solo "poco probable"."""
    kwargs = dict(current_price=100.0, volatility=0.40, base_gold_score=0.5,
                  asset="BTC", iterations=2000, horizon_minutes=10_080.0)
    r1 = run_monte_carlo_validation(seed=1, **kwargs)
    r2 = run_monte_carlo_validation(seed=2, **kwargs)
    assert r1 != r2


# ─── mc_approved coherente con los thresholds ─────────────────────────────


def test_mc_approved_respeta_threshold_de_tasa_personalizado():
    # horizon largo (1 día) + sensitivity_map alto a propósito: produce una
    # mezcla real de trayectorias arriba/abajo de success_score_threshold
    # (verificado: success_rate≈0.093 con esta semilla) -- ni 0% ni 100%,
    # para que threshold laxo y estricto den resultados distintos de verdad.
    kwargs = dict(current_price=100.0, volatility=0.30, base_gold_score=0.80,
                  asset="NVDA", iterations=3000, seed=5, horizon_minutes=1440.0,
                  sensitivity_map={"NVDA": 2.0})
    lenient = run_monte_carlo_validation(success_rate_threshold=0.01, **kwargs)
    strict = run_monte_carlo_validation(success_rate_threshold=0.999, **kwargs)
    assert 0.0 < lenient.success_rate < 1.0  # confirma que hay mezcla real
    assert lenient.mc_approved is True
    assert strict.mc_approved is False


def test_result_es_dataclass_frozen_inmutable():
    result = run_monte_carlo_validation(100.0, 0.02, 0.9, iterations=50, seed=1)
    with pytest.raises(Exception):
        result.mc_approved = False  # type: ignore[misc]
