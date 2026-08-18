"""
tests/test_price_signals.py
=============================
Cobertura de core/price_signals.py. Un foco deliberado: confirmar que
el bug real del legacy (np nunca vinculado, NameError garantizado) no
puede reproducirse acá -- no solo que las funciones "dan un número".
"""

from __future__ import annotations

import numpy as np
import pytest

from core.price_signals import (
    BackboneResult,
    TransferEntropyResult,
    compute_backbone_score,
    compute_transfer_entropy_proxy,
)


# ─── Regresión directa del bug del legacy ──────────────────────────────────

def test_no_lanza_nameerror_te():
    """El legacy (spel_score_engine.py) usaba `np.` sin nunca vincular
    ese nombre -- NameError garantizado en el primer llamado real.
    Este test es la regresión directa de ese bug específico."""
    closes = [100.0 + i * 0.1 for i in range(70)]
    result = compute_transfer_entropy_proxy(closes)  # no debe lanzar NameError
    assert isinstance(result, TransferEntropyResult)


def test_no_lanza_nameerror_backbone():
    closes = [100.0 + i * 0.1 for i in range(70)]
    result = compute_backbone_score(closes)  # no debe lanzar NameError
    assert isinstance(result, BackboneResult)


# ─── Transfer Entropy: datos insuficientes es explícito, no un 0.0 mudo ──

def test_te_pocos_cierres_es_insufficient_data_no_cero_silencioso():
    result = compute_transfer_entropy_proxy([100.0, 101.0, 99.0])  # 3 < min_closes=10
    assert result.insufficient_data is True
    assert result.value == 0.0  # placeholder, no un dato real


def test_te_suficientes_cierres_produce_valor_en_rango_valido():
    rng = np.random.default_rng(42)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 100)))
    result = compute_transfer_entropy_proxy(closes)
    assert result.insufficient_data is False
    assert 0.0 <= result.value <= 1.0
    assert result.n_closes_used == 63  # DEFAULT_TE_LOOKBACK_DAYS, recortado


def test_te_precios_constantes_no_lanza_ni_produce_nan():
    """Caso borde real: log-retornos todos cero -- histograma degenerado.
    Debe seguir devolviendo un float válido en [0,1], no NaN."""
    closes = [100.0] * 70
    result = compute_transfer_entropy_proxy(closes)
    assert not np.isnan(result.value)
    assert 0.0 <= result.value <= 1.0


def test_te_respeta_lookback_personalizado():
    closes = list(range(100, 200))  # 100 cierres
    result = compute_transfer_entropy_proxy(closes, lookback_days=20)
    assert result.n_closes_used == 20


# ─── Backbone: mismo criterio de datos insuficientes explícitos ──────────

def test_backbone_pocos_cierres_es_insufficient_data():
    result = compute_backbone_score([100.0, 101.0, 102.0])  # << ema_slow=63
    assert result.insufficient_data is True
    assert result.value == 0.5  # placeholder neutral, no una lectura real
    assert result.ema_fast_last is None
    assert result.ema_slow_last is None


def test_backbone_tendencia_alcista_da_valor_mayor_a_0_5():
    """EMA rápida por encima de la lenta en una serie claramente
    ascendente -- backbone_score debe reflejar sesgo alcista (>0.5)."""
    closes = [100.0 + i * 0.5 for i in range(80)]  # tendencia sostenida
    result = compute_backbone_score(closes)
    assert result.insufficient_data is False
    assert result.value > 0.5


def test_backbone_tendencia_bajista_da_valor_menor_a_0_5():
    closes = [100.0 - i * 0.5 for i in range(80)]
    result = compute_backbone_score(closes)
    assert result.value < 0.5


def test_backbone_precios_planos_da_valor_cercano_a_0_5():
    closes = [100.0] * 80
    result = compute_backbone_score(closes)
    assert abs(result.value - 0.5) < 0.01


def test_backbone_siempre_en_rango_0_1_incluso_con_tendencia_extrema():
    closes = [100.0 * (1.05 ** i) for i in range(80)]  # +5%/día, extremo
    result = compute_backbone_score(closes)
    assert 0.0 <= result.value <= 1.0


# ─── Reproducibilidad -- ambas funciones son puras, deterministas ────────

def test_ambas_funciones_son_deterministas_mismo_input_mismo_output():
    closes = [100.0 + (i % 7) * 0.3 for i in range(90)]
    te1 = compute_transfer_entropy_proxy(closes)
    te2 = compute_transfer_entropy_proxy(closes)
    assert te1 == te2

    bb1 = compute_backbone_score(closes)
    bb2 = compute_backbone_score(closes)
    assert bb1 == bb2
