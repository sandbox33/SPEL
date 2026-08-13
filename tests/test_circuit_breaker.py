"""tests/test_circuit_breaker.py — cobertura completa, incluye el caso
sutil de drawdown medido desde el pico, no desde el balance inicial."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from execution.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    TradeResult,
)


def make_cb(starting_equity=10.0, max_losses=2, max_dd=0.15) -> CircuitBreaker:
    return CircuitBreaker(
        starting_equity=starting_equity,
        max_consecutive_losses=max_losses,
        max_daily_drawdown_pct=max_dd,
    )


# ── Validación de construcción ──────────────────────────────────────────

def test_rechaza_equity_no_positivo():
    with pytest.raises(ValueError):
        make_cb(starting_equity=0)
    with pytest.raises(ValueError):
        make_cb(starting_equity=-5)


def test_rechaza_max_losses_menor_a_uno():
    with pytest.raises(ValueError):
        make_cb(max_losses=0)


def test_rechaza_drawdown_fuera_de_rango():
    with pytest.raises(ValueError):
        make_cb(max_dd=0)
    with pytest.raises(ValueError):
        make_cb(max_dd=1.5)  # debe ser fracción, no porcentaje > 1


def test_estado_inicial_cerrado():
    cb = make_cb()
    assert cb.can_execute() is True
    assert cb.state == CircuitState.CLOSED


# ── Pérdidas consecutivas ───────────────────────────────────────────────

def test_una_perdida_no_abre_el_breaker():
    cb = make_cb(max_losses=2)
    cb.record_trade(TradeResult(pnl=-0.50))
    assert cb.can_execute() is True


def test_dos_perdidas_consecutivas_abre_el_breaker():
    cb = make_cb(max_losses=2)
    cb.record_trade(TradeResult(pnl=-0.50))
    cb.record_trade(TradeResult(pnl=-0.30))
    assert cb.can_execute() is False
    assert "pérdidas consecutivas" in cb.trip_reason


def test_una_ganancia_corta_la_racha():
    cb = make_cb(max_losses=2)
    cb.record_trade(TradeResult(pnl=-0.50))
    cb.record_trade(TradeResult(pnl=+0.20))  # corta la racha
    cb.record_trade(TradeResult(pnl=-0.10))  # solo 1 en la racha nueva
    assert cb.can_execute() is True


def test_breakeven_corta_la_racha_no_la_extiende():
    """Decisión de diseño #3, explícitamente probada."""
    cb = make_cb(max_losses=2)
    cb.record_trade(TradeResult(pnl=-0.50))
    cb.record_trade(TradeResult(pnl=0.0))    # breakeven: corta la racha
    cb.record_trade(TradeResult(pnl=-0.10))  # solo 1 en la racha nueva
    assert cb.can_execute() is True


# ── Drawdown diario — medido desde el PICO, no desde el balance inicial ──

def test_drawdown_medido_desde_el_pico_no_desde_balance_inicial():
    """
    Caso central que justifica la decisión de diseño #2: $10 -> $12 (pico)
    -> $10.50. Eso es 12.5% de drawdown desde el pico, aunque $10.50 siga
    por encima del balance inicial de $10. Un cálculo "desde el balance
    inicial" diría incorrectamente que hay ganancia neta, no drawdown.
    """
    cb = make_cb(starting_equity=10.0, max_losses=99, max_dd=0.20)  # alto para que no dispare por esto
    cb.record_trade(TradeResult(pnl=+2.0))   # equity: 12.0 (nuevo pico)
    cb.record_trade(TradeResult(pnl=-1.5))   # equity: 10.5

    dd = cb.daily_drawdown_pct
    assert dd == pytest.approx((12.0 - 10.5) / 12.0)  # 12.5%, no negativo
    assert dd == pytest.approx(0.125)


def test_drawdown_diario_abre_el_breaker_al_cruzar_el_umbral():
    cb = make_cb(starting_equity=10.0, max_losses=99, max_dd=0.15)
    cb.record_trade(TradeResult(pnl=+2.0))   # pico: 12.0
    cb.record_trade(TradeResult(pnl=-1.5))   # equity: 10.5, dd=12.5% -- aún cerrado
    assert cb.can_execute() is True

    cb.record_trade(TradeResult(pnl=-0.5))   # equity: 10.0, dd=16.6% -- dispara
    assert cb.can_execute() is False
    assert "drawdown diario" in cb.trip_reason


# ── Reset por cruce de día UTC ──────────────────────────────────────────

def test_reset_automatico_al_cruzar_dia_utc():
    cb = make_cb(max_losses=2, max_dd=0.99)
    cb.record_trade(TradeResult(pnl=-0.5))
    cb.record_trade(TradeResult(pnl=-0.5))
    assert cb.can_execute() is False

    manana = datetime.now(timezone.utc) + timedelta(days=1)
    with patch("execution.circuit_breaker.datetime") as mock_dt:
        mock_dt.now.return_value = manana
        mock_dt.strftime = datetime.strftime
        assert cb.can_execute() is True  # nuevo día, breaker se resetea solo


# ── Reset manual ─────────────────────────────────────────────────────────

def test_force_reset_requiere_authorized_by():
    cb = make_cb(max_losses=1)
    cb.record_trade(TradeResult(pnl=-1.0))
    assert cb.can_execute() is False

    with pytest.raises(ValueError):
        cb.force_reset(authorized_by="")


def test_force_reset_funciona_con_autorizacion():
    cb = make_cb(max_losses=1)
    cb.record_trade(TradeResult(pnl=-1.0))
    assert cb.can_execute() is False

    cb.force_reset(authorized_by="altair")
    assert cb.can_execute() is True
    assert cb.trip_reason is None


# ── snapshot() — para auditoría/logging ─────────────────────────────────

def test_snapshot_contiene_los_campos_esperados():
    cb = make_cb()
    cb.record_trade(TradeResult(pnl=-0.3))
    snap = cb.snapshot()
    assert set(snap.keys()) == {
        "state", "trip_reason", "consecutive_losses",
        "current_equity", "daily_peak_equity", "daily_drawdown_pct", "day",
    }
    assert snap["consecutive_losses"] == 1
    assert snap["state"] == "CLOSED"
