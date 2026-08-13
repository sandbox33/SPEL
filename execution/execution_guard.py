"""
execution/execution_guard.py
==============================
Filtro de viabilidad financiera pre-ejecución. Decide si un trade candidato
tiene sentido económico ANTES de que CircuitBreaker o el broker lo vean.

Dos reglas independientes, no una redundante de la otra (verificado antes
de escribir este archivo, no asumido):

  Regla 1 — ¿el retorno esperado justifica el costo?
    expected_return > K * friction_pct

  Regla 5 — ¿el costo de fricción se come el presupuesto de riesgo?
    friction_usd / max_risk_usd <= 0.30

Cuando el tamaño de posición sale de risk-based sizing, la Regla 5 se
reduce algebraicamente a friction_pct / stop_loss_pct — es decir, NO
depende de expected_return ni de confidence_score en absoluto. Un trade
con excelente retorno esperado puede reprobar la Regla 5 igual si el stop
es angosto respecto al spread. Las dos reglas atrapan fallas distintas:
Regla 1 = "¿vale la pena el viaje?", Regla 5 = "¿el stop está mal puesto
para este spread?" — la segunda es la que de verdad protege una cuenta de
$10 en Deriv, donde el spread ancho + stop angosto + apalancamiento
implícito se combinan mal.

UNIFICACIÓN Deriv/Alpaca (no está en la firma como parámetro "broker" a
propósito): friction_pct = (spread / entry_price) + extra_fee_pct cubre
ambos casos. Para Deriv, extra_fee_pct=0.0 (todo el costo vive en el
spread). Para Alpaca, el bid-ask va en `spread` y la microtarifa
regulatoria en `extra_fee_pct`. Quien llama decide qué pasar según el
broker — el guard no necesita saberlo.

LÍMITE EXPLÍCITO, no silencioso: este módulo NO modela apalancamiento ni
márgenes de broker. position_size_usd sale de risk-based sizing puro
(max_risk_usd / stop_loss_pct) y puede superar account_balance — eso es
apalancamiento implícito, y se reporta como tal (implied_leverage) en vez
de taparlo con un cap inventado. Si el broker no permite ese apalancamiento
para ese instrumento, eso lo valida quien llama — no lo sé yo desde acá,
y no lo voy a asumir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


class ExecutionGuardConfigError(ValueError):
    """Parámetros de construcción inválidos — error de configuración, no
    de un trade en particular."""


class TradeSetupError(ValueError):
    """El trade candidato es geométricamente inválido (ej. TP y SL del
    mismo lado del precio de entrada) — error de quien llama, no una
    evaluación de viabilidad económica. Distinto de is_viable=False:
    esto significa 'este llamado no tiene sentido', no 'este trade no
    conviene'."""


@dataclass(frozen=True)
class ViabilityMetrics:
    """Todas las cantidades intermedias del cálculo — para que cualquiera
    audite el resultado sin tener que re-derivar la matemática."""
    expected_return: float
    friction_pct: float
    required_return: float          # K * friction_pct — contra esto se compara expected_return
    stop_loss_pct: float
    max_risk_usd: float
    position_size_usd: float
    implied_leverage: float         # position_size_usd / account_balance
    friction_usd: float
    friction_to_risk_ratio: float

    def as_dict(self) -> dict:
        return {
            "expected_return": round(self.expected_return, 6),
            "friction_pct": round(self.friction_pct, 6),
            "required_return": round(self.required_return, 6),
            "stop_loss_pct": round(self.stop_loss_pct, 6),
            "max_risk_usd": round(self.max_risk_usd, 4),
            "position_size_usd": round(self.position_size_usd, 4),
            "implied_leverage": round(self.implied_leverage, 2),
            "friction_usd": round(self.friction_usd, 6),
            "friction_to_risk_ratio": round(self.friction_to_risk_ratio, 4),
        }


class ExecutionGuard:
    """
    Uso:
        guard = ExecutionGuard(k_factor=2.0, max_risk_pct=0.02)
        is_viable, reason, metrics = guard.evaluate_viability(
            entry_price=1.1000, take_profit=1.1050, stop_loss=1.0980,
            confidence_score=0.7, spread=0.0015, account_balance=10.0,
        )
    """

    #: Umbral fijo de la Regla 5 — no configurable a propósito. Es una
    #: línea de seguridad estructural (fricción no debe pasar de 30% del
    #: riesgo asumido), no un parámetro de estrategia como K.
    MAX_FRICTION_TO_RISK_RATIO: float = 0.30

    def __init__(self, k_factor: float = 2.0, max_risk_pct: float = 0.02) -> None:
        if k_factor <= 0:
            raise ExecutionGuardConfigError(f"k_factor debe ser positivo, recibido: {k_factor}")
        if not (0 < max_risk_pct <= 1):
            raise ExecutionGuardConfigError(
                f"max_risk_pct debe estar en (0, 1] como fracción, recibido: {max_risk_pct}"
            )
        self.k_factor = k_factor
        self.max_risk_pct = max_risk_pct

    def evaluate_viability(
        self,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        confidence_score: float,
        spread: float,
        account_balance: float = 10.0,
        extra_fee_pct: float = 0.0,
    ) -> Tuple[bool, str, dict]:
        self._validate_inputs(
            entry_price, take_profit, stop_loss, confidence_score,
            spread, account_balance, extra_fee_pct,
        )

        expected_return = abs(take_profit - entry_price) / entry_price * confidence_score
        friction_pct = (spread / entry_price) + extra_fee_pct
        required_return = self.k_factor * friction_pct

        stop_loss_pct = abs(entry_price - stop_loss) / entry_price
        max_risk_usd = account_balance * self.max_risk_pct
        # Risk-based sizing puro — ver docstring del módulo sobre por qué
        # esto NO se limita a account_balance de forma implícita.
        position_size_usd = max_risk_usd / stop_loss_pct
        implied_leverage = position_size_usd / account_balance

        friction_usd = friction_pct * position_size_usd
        friction_to_risk_ratio = friction_usd / max_risk_usd if max_risk_usd > 0 else float("inf")

        metrics = ViabilityMetrics(
            expected_return=expected_return,
            friction_pct=friction_pct,
            required_return=required_return,
            stop_loss_pct=stop_loss_pct,
            max_risk_usd=max_risk_usd,
            position_size_usd=position_size_usd,
            implied_leverage=implied_leverage,
            friction_usd=friction_usd,
            friction_to_risk_ratio=friction_to_risk_ratio,
        )

        violations: list[str] = []

        if not (expected_return > required_return):
            violations.append(
                f"Regla 1: expected_return={expected_return:.5f} no supera "
                f"K*friction_pct={required_return:.5f} (K={self.k_factor})"
            )

        if friction_to_risk_ratio > self.MAX_FRICTION_TO_RISK_RATIO:
            violations.append(
                f"Regla 5: friction_to_risk_ratio={friction_to_risk_ratio:.2%} "
                f"supera el máximo {self.MAX_FRICTION_TO_RISK_RATIO:.0%} "
                f"(friction_usd=${friction_usd:.4f} de max_risk_usd=${max_risk_usd:.2f})"
            )

        if violations:
            return False, " | ".join(violations), metrics.as_dict()
        return True, "Viable — pasa Regla 1 (retorno vs. costo) y Regla 5 (fricción vs. riesgo)", metrics.as_dict()

    def _validate_inputs(
        self, entry_price, take_profit, stop_loss, confidence_score,
        spread, account_balance, extra_fee_pct,
    ) -> None:
        """Errores de uso (TradeSetupError/ValueError) — no evaluaciones de
        negocio. Un llamador que pasa esto mal tiene un bug, no un trade
        poco conveniente."""
        if entry_price <= 0:
            raise ValueError(f"entry_price debe ser positivo, recibido: {entry_price}")
        if account_balance <= 0:
            raise ValueError(f"account_balance debe ser positivo, recibido: {account_balance}")
        if not (0.0 <= confidence_score <= 1.0):
            raise ValueError(f"confidence_score debe estar en [0.0, 1.0], recibido: {confidence_score}")
        if spread < 0:
            raise ValueError(f"spread no puede ser negativo, recibido: {spread}")
        if extra_fee_pct < 0:
            raise ValueError(f"extra_fee_pct no puede ser negativo, recibido: {extra_fee_pct}")
        if stop_loss == entry_price:
            raise TradeSetupError("stop_loss no puede ser igual a entry_price (división por cero en el sizing)")
        if take_profit == entry_price:
            raise TradeSetupError("take_profit no puede ser igual a entry_price")

        is_long = take_profit > entry_price and stop_loss < entry_price
        is_short = take_profit < entry_price and stop_loss > entry_price
        if not (is_long or is_short):
            raise TradeSetupError(
                f"Setup geométricamente inválido: entry={entry_price}, "
                f"take_profit={take_profit}, stop_loss={stop_loss}. "
                f"Para largo: stop_loss < entry < take_profit. "
                f"Para corto: take_profit < entry < stop_loss."
            )
