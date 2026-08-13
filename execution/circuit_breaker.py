"""
execution/circuit_breaker.py
==============================
Interruptor de seguridad para la cuenta de trading. Escrito desde cero — la
clase CircuitBreaker que existe en archive/legacy-pre-20260813/spel_guardian.py
NO se importó ni se copió; ese código es legado y está fuera de alcance por
tu propia regla ("prohibido importar nada desde archive/*").

DIFERENCIA DELIBERADA con un circuit breaker de resiliencia de red (tipo
Redis/HTTP): esos se auto-recuperan después de un timeout corto, porque el
costo de un reintento fallido es una latencia extra. Acá el costo de un
"reintento" tras 2 pérdidas seguidas es dinero real. Por eso este circuit
breaker NUNCA se auto-cierra por tiempo — solo se resetea al cruce de día
(el drawdown diario, por definición, es de HOY) o por acción explícita.

DECISIONES DE DISEÑO QUE TE CORRESPONDE CONFIRMAR (no hay número "correcto"
sin tu política de riesgo):
  1. max_daily_drawdown_pct NO tiene default — se exige explícito en el
     constructor. Preferí forzar la decisión antes que inventar un
     porcentaje razonable-sonante.
  2. El drawdown se mide desde el PICO de equity del día, no desde el
     balance inicial — es la definición matemática correcta de drawdown
     (si $10 sube a $12 y cae a $10.50, ya hay 12.5% de drawdown desde el
     pico, aunque siga por encima del balance inicial).
  3. Un trade con pnl == 0 (breakeven) CORTA la racha de pérdidas, no la
     extiende ni la ignora. Es una posición defendible, no la única.
  4. Opera sobre P&L REAL de la cuenta (el $10 real), no sobre el
     equivalente canónico escalado a $100k. Un circuit breaker de
     seguridad debe mirar el dinero real que se puede perder, no la
     métrica pensada para significancia estadística.
  5. Consecutive-loss y drawdown diario resetean juntos al cruce de día
     UTC. Alternativa más conservadora: consecutive-loss requiere reset
     manual (force_reset) incluso al día siguiente. Lo dejo como
     auto-reset por simplicidad — cambiarlo es una línea, marcada abajo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("spel.execution.circuit_breaker")


class CircuitState(Enum):
    CLOSED = auto()  # operación normal, se puede operar
    OPEN = auto()    # halt de seguridad activo, NO se puede operar


class CircuitBreakerTrippedError(Exception):
    """Se lanza si algo intenta operar mientras el breaker está OPEN.
    can_execute() es la vía normal de chequeo (devuelve bool, no lanza) —
    esta excepción es para cuando algo saltea ese chequeo por error."""


@dataclass
class TradeResult:
    """Un trade cerrado, tal como lo reporta execution/ tras cerrar posición."""
    pnl: float
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CircuitBreaker:
    """
    Interruptor de seguridad por cuenta. Un CircuitBreaker por cuenta real
    que opera (no uno global si hay varias cuentas).

    Uso:
        cb = CircuitBreaker(
            starting_equity=10.0,
            max_consecutive_losses=2,
            max_daily_drawdown_pct=0.15,  # ejemplo: 15% — DECISIÓN TUYA
        )
        if not cb.can_execute():
            return  # no operar, breaker abierto
        # ... ejecutar trade ...
        cb.record_trade(TradeResult(pnl=-1.20))
    """

    def __init__(
        self,
        starting_equity: float,
        max_consecutive_losses: int,
        max_daily_drawdown_pct: float,
    ) -> None:
        if starting_equity <= 0:
            raise ValueError(f"starting_equity debe ser positivo, recibido: {starting_equity}")
        if max_consecutive_losses < 1:
            raise ValueError(f"max_consecutive_losses debe ser >= 1, recibido: {max_consecutive_losses}")
        if not (0 < max_daily_drawdown_pct < 1):
            raise ValueError(
                f"max_daily_drawdown_pct debe estar en (0, 1) como fracción "
                f"(0.15 = 15%), recibido: {max_daily_drawdown_pct}"
            )

        self._starting_equity = starting_equity
        self._max_consecutive_losses = max_consecutive_losses
        self._max_daily_drawdown_pct = max_daily_drawdown_pct

        self._state = CircuitState.CLOSED
        self._consecutive_losses = 0
        self._current_equity = starting_equity
        self._daily_peak_equity = starting_equity
        self._trip_reason: Optional[str] = None
        self._current_day: str = self._utc_day_key()

    @staticmethod
    def _utc_day_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_reset_for_new_day(self) -> None:
        """Decisión de diseño #5 vive acá — si querés que consecutive_losses
        NO se resetee solo, sacar esa línea y dejar solo el reset de
        drawdown/peak, agregando un flag para exigir force_reset()."""
        today = self._utc_day_key()
        if today != self._current_day:
            logger.info(
                "[circuit_breaker] nuevo día UTC (%s -> %s) — reset de "
                "drawdown y racha de pérdidas", self._current_day, today,
            )
            self._current_day = today
            self._daily_peak_equity = self._current_equity
            self._consecutive_losses = 0
            if self._state == CircuitState.OPEN:
                self._state = CircuitState.CLOSED
                self._trip_reason = None

    def can_execute(self) -> bool:
        """Único método que execution/ debe consultar antes de abrir una
        posición nueva. Nunca lanza — siempre bool."""
        self._maybe_reset_for_new_day()
        return self._state == CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        self._maybe_reset_for_new_day()
        return self._state

    @property
    def trip_reason(self) -> Optional[str]:
        return self._trip_reason

    @property
    def daily_drawdown_pct(self) -> float:
        """Drawdown actual desde el pico de HOY, como fracción (0.05 = 5%)."""
        if self._daily_peak_equity <= 0:
            return 0.0
        return max(0.0, (self._daily_peak_equity - self._current_equity) / self._daily_peak_equity)

    def record_trade(self, result: TradeResult) -> None:
        """
        Registra el resultado de un trade cerrado. Actualiza equity, pico
        diario, racha de pérdidas, y evalúa si hay que abrir el breaker.
        """
        self._maybe_reset_for_new_day()

        self._current_equity += result.pnl
        self._daily_peak_equity = max(self._daily_peak_equity, self._current_equity)

        if result.pnl < 0:
            self._consecutive_losses += 1
        else:
            # Decisión de diseño #3: breakeven (pnl == 0) corta la racha.
            self._consecutive_losses = 0

        self._evaluate_trip_conditions()

    def _evaluate_trip_conditions(self) -> None:
        if self._state == CircuitState.OPEN:
            return  # ya está abierto, no hace falta re-evaluar

        if self._consecutive_losses >= self._max_consecutive_losses:
            self._trip(
                f"racha de {self._consecutive_losses} pérdidas consecutivas "
                f"(máximo permitido: {self._max_consecutive_losses})"
            )
            return

        dd = self.daily_drawdown_pct
        if dd >= self._max_daily_drawdown_pct:
            self._trip(
                f"drawdown diario {dd:.2%} alcanzó el máximo "
                f"{self._max_daily_drawdown_pct:.2%} "
                f"(pico hoy: {self._daily_peak_equity:.2f}, actual: {self._current_equity:.2f})"
            )

    def _trip(self, reason: str) -> None:
        self._state = CircuitState.OPEN
        self._trip_reason = reason
        logger.warning("[circuit_breaker] ABIERTO — %s", reason)

    def force_reset(self, *, authorized_by: str) -> None:
        """
        Reset manual explícito — para cuando el breaker abrió por drawdown
        pero un humano decide, con criterio, reanudar antes del cruce de
        día. `authorized_by` es obligatorio y queda en el log: un reset
        sin registrar quién lo autorizó no es aceptable para una cuenta
        con capital real.
        """
        if not authorized_by:
            raise ValueError("force_reset requiere authorized_by — no se resetea sin dejar registro de quién")
        logger.warning(
            "[circuit_breaker] RESET MANUAL por %s — razón previa: %s",
            authorized_by, self._trip_reason,
        )
        self._state = CircuitState.CLOSED
        self._trip_reason = None
        self._consecutive_losses = 0

    def snapshot(self) -> dict:
        """Estado completo para logging/auditoría — todo lo que EventEngine
        (cuando exista, con spec confirmado) necesitaría para un evento."""
        self._maybe_reset_for_new_day()
        return {
            "state": self._state.name,
            "trip_reason": self._trip_reason,
            "consecutive_losses": self._consecutive_losses,
            "current_equity": round(self._current_equity, 4),
            "daily_peak_equity": round(self._daily_peak_equity, 4),
            "daily_drawdown_pct": round(self.daily_drawdown_pct, 4),
            "day": self._current_day,
        }
