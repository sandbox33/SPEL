"""
tests/test_cycle.py
=====================
Cobertura de orchestration/cycle.py. Mismo patrón de drive_root()
temporal que tests/test_gdelt_series.py -- no un mecanismo nuevo.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from orchestration.cycle import (
    DEFAULT_CYCLE_ASSETS,
    GOLD_SCORE_BLOCKED_REASON,
    run_scoring_cycle,
)

P90_TEST_DEFAULT = 0.7  # placeholder consciente para tests -- ver docstring
                        # de run_scoring_cycle sobre por qué no hay default.


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _dia(asset: str, d: date, entropy=0.5, n_events=10, insufficient=False) -> DailyAggregationResult:
    return DailyAggregationResult(
        day=d, asset=asset,
        entropy_shannon=None if insufficient else entropy,
        zipf_concentration=None if insufficient else 0.2,
        goldstein_mean=None if insufficient else 1.0,
        tone_variance=None if insufficient else 0.3,
        n_events=n_events, insufficient_events=insufficient,
    )


def _sembrar_historia(asset: str, n_dias: int, start=date(2026, 1, 1), **kwargs):
    for i in range(n_dias):
        append_day(_dia(asset, start + timedelta(days=i), **kwargs))


# ─── Cold start -- caso válido, no un error ────────────────────────────────

def test_activo_sin_historia_es_cold_start_no_data():
    resultado = run_scoring_cycle(["NVDA"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["NVDA"]
    assert r.data_status == "cold_start_no_data"
    assert r.n_days_history == 0
    assert r.vitality_tesla is None
    assert r.nash_frozen is None
    assert r.godel_is_active is None


# ─── Camino feliz -- historia real, las 3 funciones corren de verdad ──────

def test_activo_con_historia_calcula_los_3_reales():
    _sembrar_historia("BTC", 15, entropy=0.4, n_events=20)
    resultado = run_scoring_cycle(["BTC"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["BTC"]

    assert r.data_status == "ok"
    assert r.n_days_history == 15
    assert r.vitality_tesla is not None
    assert r.vitality_tesla.value in (3, 6, 9)
    assert r.nash_frozen is not None
    assert isinstance(r.godel_is_active, bool)


# ─── gold_score: SIEMPRE None, SIEMPRE con la misma razón explícita ───────

def test_gold_score_siempre_none_con_o_sin_historia():
    _sembrar_historia("XAU", 5)
    resultado = run_scoring_cycle(["XAU", "NIFTY50"], p90_entropy_global_default=P90_TEST_DEFAULT)
    for r in resultado.values():
        assert r.gold_score is None
        assert r.gold_score_blocked_reason == GOLD_SCORE_BLOCKED_REASON
        assert "godel_score" in r.gold_score_blocked_reason
        assert "backbone_score" in r.gold_score_blocked_reason


# ─── EURUSD -- el fix del patch anterior, ejercitado end-to-end acá ───────

def test_eurusd_funciona_end_to_end_gracias_al_fix_anterior():
    """Antes del fix a classify_gdelt_event, este activo ni siquiera podía
    clasificar eventos GDELT -- este test confirma que el ciclo completo
    (que no llama classify_gdelt_event directamente, pero depende de que
    EURUSD sea un activo válido para el resto del pipeline) lo acepta."""
    _sembrar_historia("EURUSD", 12, entropy=0.6)
    resultado = run_scoring_cycle(["EURUSD"], p90_entropy_global_default=P90_TEST_DEFAULT)
    assert resultado["EURUSD"].data_status == "ok"


# ─── Activo no configurado -- falla temprano y claro, no en silencio ──────

def test_activo_no_configurado_lanza_valueerror_no_degrada_en_silencio():
    with pytest.raises(ValueError, match="no tiene classify_gdelt_event"):
        run_scoring_cycle(["ACTIVO_INVENTADO_XYZ"], p90_entropy_global_default=P90_TEST_DEFAULT)


def test_indice_volatilidad_no_esta_configurado_a_proposito():
    """VOL50 tiene precio real (DerivAdapter) pero GDELT no aplica sobre
    índices sintéticos (Fase 6, Hallazgo 1) -- debe seguir sin cobertura
    acá, no agregarse por accidente."""
    with pytest.raises(ValueError):
        run_scoring_cycle(["VOL50"], p90_entropy_global_default=P90_TEST_DEFAULT)


# ─── p90_entropy_global_default es obligatorio -- sin valor inventado ─────

def test_p90_global_default_es_argumento_obligatorio():
    with pytest.raises(TypeError):
        run_scoring_cycle(["NVDA"])  # type: ignore[call-arg]


# ─── DEFAULT_CYCLE_ASSETS -- exactamente los 5, ni más ni menos ──────────

def test_default_cycle_assets_son_exactamente_los_5_confirmados():
    assert set(DEFAULT_CYCLE_ASSETS) == {"NVDA", "XAU", "BTC", "NIFTY50", "EURUSD"}
    assert "VOL50" not in DEFAULT_CYCLE_ASSETS


# ─── Multi-activo real: mezcla de cold-start y con historia en una corrida ─

def test_corrida_multi_activo_mezcla_cold_start_y_con_historia():
    _sembrar_historia("NVDA", 8)
    # XAU, BTC, NIFTY50, EURUSD quedan sin historia -- cold start real
    resultado = run_scoring_cycle(p90_entropy_global_default=P90_TEST_DEFAULT)  # default: los 5

    assert resultado["NVDA"].data_status == "ok"
    for asset in ("XAU", "BTC", "NIFTY50", "EURUSD"):
        assert resultado[asset].data_status == "cold_start_no_data"


# ─── Días con insufficient_events=True se excluyen de las ventanas ───────

def test_dias_insuficientes_no_entran_en_las_ventanas():
    _sembrar_historia("NVDA", 5, entropy=0.5)
    append_day(_dia("NVDA", date(2026, 1, 6), insufficient=True))  # sin entropy
    _sembrar_historia("NVDA", 3, start=date(2026, 1, 7), entropy=0.6)

    resultado = run_scoring_cycle(["NVDA"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["NVDA"]
    # 5 + 3 = 8 días válidos; el día insuficiente (día 6) no cuenta
    assert r.n_days_history == 8
