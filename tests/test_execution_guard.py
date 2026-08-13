"""tests/test_execution_guard.py

Cada valor esperado en estos tests salió de calcularlo a mano (o con
python -c) ANTES de escribir el assert — no se ajustó el test para que
pase, se verificó la matemática primero. Ver la verificación de la Regla
1-vs-Regla-5 en la respuesta que acompaña este módulo.
"""

from __future__ import annotations

import pytest

from execution.execution_guard import (
    ExecutionGuard,
    ExecutionGuardConfigError,
    TradeSetupError,
)


def make_guard(k=2.0, max_risk_pct=0.02) -> ExecutionGuard:
    return ExecutionGuard(k_factor=k, max_risk_pct=max_risk_pct)


# ── Validación de construcción ──────────────────────────────────────────

def test_rechaza_k_factor_no_positivo():
    with pytest.raises(ExecutionGuardConfigError):
        make_guard(k=0)
    with pytest.raises(ExecutionGuardConfigError):
        make_guard(k=-1)


def test_rechaza_max_risk_pct_fuera_de_rango():
    with pytest.raises(ExecutionGuardConfigError):
        make_guard(max_risk_pct=0)
    with pytest.raises(ExecutionGuardConfigError):
        make_guard(max_risk_pct=1.5)


def test_defaults_coinciden_con_el_spec():
    guard = ExecutionGuard()
    assert guard.k_factor == 2.0
    assert guard.max_risk_pct == 0.02


# ── Validación de inputs de evaluate_viability ──────────────────────────

def test_rechaza_entry_price_no_positivo():
    guard = make_guard()
    with pytest.raises(ValueError):
        guard.evaluate_viability(0, 1.10, 1.09, 0.5, 0.001)


def test_rechaza_confidence_score_fuera_de_rango():
    guard = make_guard()
    with pytest.raises(ValueError):
        guard.evaluate_viability(1.10, 1.11, 1.09, 1.5, 0.001)
    with pytest.raises(ValueError):
        guard.evaluate_viability(1.10, 1.11, 1.09, -0.1, 0.001)


def test_rechaza_spread_negativo():
    guard = make_guard()
    with pytest.raises(ValueError):
        guard.evaluate_viability(1.10, 1.11, 1.09, 0.5, -0.001)


def test_rechaza_setup_geometricamente_invalido_mismo_lado():
    """TP y SL del mismo lado de entry -- ni largo ni corto tiene sentido."""
    guard = make_guard()
    with pytest.raises(TradeSetupError):
        guard.evaluate_viability(1.10, 1.12, 1.11, 0.5, 0.001)  # TP y SL ambos > entry


def test_rechaza_stop_loss_igual_a_entry():
    guard = make_guard()
    with pytest.raises(TradeSetupError):
        guard.evaluate_viability(1.10, 1.12, 1.10, 0.5, 0.001)


def test_acepta_setup_corto_valido():
    """take_profit < entry < stop_loss -- corto válido, no debe lanzar."""
    guard = make_guard()
    is_viable, reason, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.0950, stop_loss=1.1020,
        confidence_score=0.7, spread=0.0002, account_balance=10.0,
    )
    assert isinstance(is_viable, bool)  # no lanzó, eso es lo que se prueba acá


# ══════════════════════════════════════════════════════════════════════
#  ESCENARIO DERIV — spread ancho, microcuenta $10
# ══════════════════════════════════════════════════════════════════════

def test_deriv_spread_ancho_pasa_regla_1_pero_reprueba_regla_5():
    """
    Verificado a mano: entry=1.1000, spread=15 pips, SL=20 pips,
    TP=50 pips, confidence=0.7.
      expected_return = 0.00318, K*friction_pct = 0.00273 -> Regla 1 PASA
      position_size=$110 (11x apalancamiento implícito), friction=$0.15,
      ratio=75% -> Regla 5 RECHAZA.
    Este es el caso que justifica por qué la Regla 5 existe separada de
    la 1: buen retorno esperado, pero el stop es demasiado angosto para
    ese spread ancho en una cuenta de $10.
    """
    guard = make_guard(k=2.0, max_risk_pct=0.02)
    is_viable, reason, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1050, stop_loss=1.0980,
        confidence_score=0.7, spread=0.0015, account_balance=10.0,
    )

    assert metrics["expected_return"] == pytest.approx(0.003182, abs=1e-5)
    assert metrics["required_return"] == pytest.approx(0.002727, abs=1e-5)
    assert metrics["expected_return"] > metrics["required_return"]  # Regla 1 pasa

    assert metrics["position_size_usd"] == pytest.approx(110.0, abs=0.5)
    assert metrics["implied_leverage"] == pytest.approx(11.0, abs=0.1)
    assert metrics["friction_to_risk_ratio"] == pytest.approx(0.75, abs=0.01)

    assert is_viable is False
    assert "Regla 5" in reason
    assert "Regla 1" not in reason  # Regla 1 SÍ pasó, no debe aparecer como violación


def test_deriv_retorno_insuficiente_reprueba_regla_1_con_stop_ancho():
    """
    Verificado a mano: TP muy cerca (4 pips) de un spread de 6 pips, pero
    con SL ancho (100 pips) para que la Regla 5 NO dispare -- aísla la
    Regla 1 como única causa de rechazo.
    """
    guard = make_guard(k=2.0, max_risk_pct=0.02)
    is_viable, reason, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1004, stop_loss=1.0900,
        confidence_score=0.8, spread=0.0006, account_balance=10.0,
    )

    assert metrics["expected_return"] < metrics["required_return"]
    assert metrics["friction_to_risk_ratio"] < 0.30  # Regla 5 no debe disparar acá

    assert is_viable is False
    assert "Regla 1" in reason
    assert "Regla 5" not in reason


def test_deriv_trade_viable_con_stop_razonable():
    """Mismo par, pero con SL más ancho (60 pips) para que el
    apalancamiento implícito baje y la fricción no se coma el riesgo."""
    guard = make_guard(k=2.0, max_risk_pct=0.02)
    is_viable, reason, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1080, stop_loss=1.0940,
        confidence_score=0.75, spread=0.0010, account_balance=10.0,
    )
    assert is_viable is True
    assert metrics["friction_to_risk_ratio"] <= 0.30


# ══════════════════════════════════════════════════════════════════════
#  ESCENARIO ALPACA — spread ajustado, microcuenta $10
# ══════════════════════════════════════════════════════════════════════

def test_alpaca_spread_ajustado_es_claramente_viable():
    """
    Verificado a mano: acción a $150, spread de 2 centavos (típico en un
    nombre líquido), micro-tarifa regulatoria ilustrativa 0.0000221
    (orden de magnitud de la tasa SEC -- verificar la tasa vigente en
    sec.gov antes de usar en producción, no es un valor congelado acá).
    friction_pct resultante es diminuto comparado con Deriv -- así es
    como debería comportarse un stock líquido de EE.UU.
    """
    guard = make_guard(k=2.0, max_risk_pct=0.02)
    is_viable, reason, metrics = guard.evaluate_viability(
        entry_price=150.00, take_profit=153.00, stop_loss=148.50,
        confidence_score=0.6, spread=0.02, account_balance=10.0,
        extra_fee_pct=0.0000221,
    )

    assert metrics["friction_pct"] == pytest.approx(0.0001554, abs=1e-6)
    assert metrics["friction_to_risk_ratio"] < 0.02  # fricción casi nula vs riesgo
    assert is_viable is True


def test_alpaca_friction_pct_significativamente_menor_que_deriv_mismo_setup_relativo():
    """Prueba comparativa directa: mismo % de movimiento TP/SL relativo,
    friction_pct de Alpaca debe ser marcadamente menor que el de Deriv --
    es la diferencia estructural que motivó este módulo. Verificado a
    mano antes de fijar el umbral: la razón real en este caso es ~7.0x
    (0.0010909 / 0.0001554), no "un orden de magnitud" como afirmé al
    principio sin calcularlo -- ese assert original fallaba. Umbral de
    5x deja margen real sin exagerar la afirmación."""
    guard = make_guard()

    _, _, metrics_deriv = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1055, stop_loss=1.0945,
        confidence_score=0.7, spread=0.0012, account_balance=10.0,
    )
    _, _, metrics_alpaca = guard.evaluate_viability(
        entry_price=150.00, take_profit=157.50, stop_loss=142.50,
        confidence_score=0.7, spread=0.02, account_balance=10.0,
        extra_fee_pct=0.0000221,
    )

    ratio = metrics_deriv["friction_pct"] / metrics_alpaca["friction_pct"]
    assert ratio == pytest.approx(7.02, abs=0.05)
    assert metrics_alpaca["friction_pct"] < metrics_deriv["friction_pct"] / 5


# ── Metrics dict — forma y completitud ──────────────────────────────────

def test_metrics_dict_contiene_todas_las_claves_del_spec():
    guard = make_guard()
    _, _, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1050, stop_loss=1.0980,
        confidence_score=0.7, spread=0.0005, account_balance=10.0,
    )
    claves_requeridas_por_spec = {
        "max_risk_usd", "position_size_usd", "friction_usd", "friction_to_risk_ratio",
    }
    assert claves_requeridas_por_spec.issubset(metrics.keys())


def test_max_risk_usd_es_02_dolares_para_cuenta_de_10():
    """Caso explícito del spec: '$0.20 USD para cuenta de $10'."""
    guard = make_guard(max_risk_pct=0.02)
    _, _, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1050, stop_loss=1.0980,
        confidence_score=0.7, spread=0.0005, account_balance=10.0,
    )
    assert metrics["max_risk_usd"] == pytest.approx(0.20)


def test_implied_leverage_se_reporta_no_se_esconde():
    """No hay cap de apalancamiento -- se reporta explícito. Este test
    documenta esa decisión de diseño con un caso donde el apalancamiento
    implícito es alto a propósito (stop muy angosto)."""
    guard = make_guard()
    _, _, metrics = guard.evaluate_viability(
        entry_price=1.1000, take_profit=1.1030, stop_loss=1.0995,
        confidence_score=0.9, spread=0.0002, account_balance=10.0,
    )
    assert metrics["implied_leverage"] > 1.0  # posición supera el balance de cuenta
    assert metrics["position_size_usd"] > 10.0
