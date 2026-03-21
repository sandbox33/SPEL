# spel_trading_router.py
# SPEL v22 — Router de Modos de Trading
# Sesion 16 - 07 Mar 2026
#
# Implementa la arquitectura de §27 del log maestro:
#   MODO INSTITUCIONAL : Score >= 90 + Godel activo -> daily, Kelly completo, RR 2.5x
#   MODO SCALPING      : Score 70-89 + Godel activo -> 15/30min, Kelly reducido, RR 1.5x
#   MODO FLAT          : Score < 70 o Godel inactivo -> no operar
#
# Integracion con SPELCostModel:
#   Institucional : 1 trade/dia por activo -> costo bajo en terminos de frecuencia
#   Scalping      : hasta 3 trades/sesion -> costo critico, solo si alpha > costo
#
# USO:
#   from spel_trading_router import SPELTradingRouter
#   router = SPELTradingRouter()
#   decision = router.route(score_resultado, lstm_output, df_intraday=df_15m)
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np

try:
    import polars as pl
    HAS_POLARS = True
except ImportError:
    HAS_POLARS = False

try:
    from spel_cost_model import SPELCostModel
    HAS_COST_MODEL = True
except ImportError:
    HAS_COST_MODEL = False

_log = logging.getLogger("SPEL.trading_router")


# Umbrales canonicos del router (modificar solo con auditoria)
SCORE_INSTITUCIONAL  = 90   # Score de Oro minimo para modo institucional
SCORE_SCALPING_MIN   = 70   # Score de Oro minimo para considerar scalping
GODEL_REQUERIDO      = True # Godel activo requerido para ambos modos activos
MAX_SCALPING_TRADES  = 3    # Maximo de trades scalping por sesion por activo
KELLY_REDUCCION_SCALP = 0.5 # Kelly al 50% en modo scalping (mas conservador)
RR_INSTITUCIONAL     = 2.5  # Risk:Reward institucional (canónico backbone)
RR_SCALPING          = 1.5  # Risk:Reward scalping (rapido, menor recorrido)

# Costo minimo de alpha por trade para que scalping sea viable
# Si alpha esperado <= costo -> no entrar
ALPHA_MIN_SOBRE_COSTO = 1.5  # alpha debe ser al menos 1.5x el costo por trade


class ModoTrading(str, Enum):
    INSTITUCIONAL = "INSTITUCIONAL"  # Daily, Kelly completo, Score >= 90
    SCALPING_15M  = "SCALPING_15M"   # 15 min candles, Kelly reducido
    SCALPING_30M  = "SCALPING_30M"   # 30 min candles, Kelly reducido
    FLAT          = "FLAT"           # No operar


@dataclass
class DecisionTrading:
    """Decision completa del router para un activo en un momento dado."""
    activo:             str
    modo:               ModoTrading
    score_oro:          int
    godel_activo:       bool
    natural_score:      float        # Score Bayesiano del backbone (0-1)
    kiereccion:          str          # CALL / PUT / FLAT
    kelly_fraccion:     float        # Fraccion Kelly ajustada al modo
    rr_objetivo:        float        # Risk:Reward objetivo del modo
    costo_estimado_pct: float        # Costo round-trip estimado
    alpha_estimado_pct: float        # Alpha esperado sobre costo
    viable:             bool         # True si alpha > costo * ALPHA_MIN_SOBRE_COSTO
    razon:              list[str]
    intraday_score:     float        # 0-1 confirmacion intraday (si disponible)
    ts_utc:             str

    def resumen(self) -> str:
        lines = [
            f"DecisionTrading [{self.activo}]",
            f"  Modo       : {self.modo.value}",
            f"  Score Oro  : {self.score_oro}/100",
            f"  Godel      : {'ACTIVO' if self.godel_activo else 'INACTIVO'}",
            f"  Direccion  : {self.kiereccion}",
            f"  Kelly f    : {self.kelly_fraccion:.4f}",
            f"  R:R        : {self.rr_objetivo:.1f}x",
            f"  Costo rt   : {self.costo_estimado_pct:.2f}%",
            f"  Alpha est  : {self.alpha_estimado_pct:.2f}%",
            f"  VIABLE     : {'SI' if self.viable else 'NO'}",
        ]
        for r in self.razon:
            lines.append(f"  > {r}")
        return "\n".join(lines)


def _calcular_intraday_score(df_intraday, timeframe_min: int = 15) -> float:
    """
    Calcula el score de confirmacion intraday (0-1) desde velas de 15 o 30 min.
    Features: EMA9/EMA21 cross, HH/LL pattern, body/wick ratio.
    Retorna 0.5 si df_intraday es None (sin confirmacion disponible).

    Ref: §27.3 del log maestro - INTRADAY_FEATURES canonicas.
    """
    if df_intraday is None or not HAS_POLARS:
        return 0.5  # neutral si no hay datos intraday

    try:
        if len(df_intraday) < 21:
            _log.warning("df_intraday insuficiente: %d filas (min 21)", len(df_intraday))
            return 0.5

        close = df_intraday["close"].to_numpy().astype(float)
        high  = df_intraday["high"].to_numpy().astype(float)
        low   = df_intraday["low"].to_numpy().astype(float)
        open_ = df_intraday["open"].to_numpy().astype(float)

        # EMA9 / EMA21 cross (peso 40%)
        def ema(arr, n):
            alpha = 2.0 / (n + 1)
            result = np.zeros_like(arr)
            result[0] = arr[0]
            for i in range(1, len(arr)):
                result[i] = alpha * arr[i] + (1 - alpha) * result[i-1]
            return result

        ema9  = ema(close, 9)
        ema21 = ema(close, 21)
        cross_score = 1.0 if ema9[-1] > ema21[-1] else 0.0

        # HH/LL pattern ultimas 5 velas (peso 30%)
        highs_5 = high[-5:]
        lows_5  = low[-5:]
        hh = all(highs_5[i] > highs_5[i-1] for i in range(1, 5))
        ll = all(lows_5[i]  < lows_5[i-1]  for i in range(1, 5))
        trend_score = 1.0 if hh else (0.0 if ll else 0.5)

        # Body/wick ratio ultima vela (peso 30%)
        # Vela con cuerpo grande = determinacion; mecha grande = trampa
        body  = abs(close[-1] - open_[-1])
        total = high[-1] - low[-1]
        bwr   = body / (total + 1e-9)
        bwr_score = min(bwr * 1.5, 1.0)  # > 0.67 cuerpo = vela de conviction

        intraday = 0.40 * cross_score + 0.30 * trend_score + 0.30 * bwr_score

        _log.debug(
            "intraday_score=%0.3f (ema_cross=%.2f trend=%.2f bwr=%.2f)",
            intraday, cross_score, trend_score, bwr_score
        )
        return float(np.clip(intraday, 0.0, 1.0))

    except Exception as e:
        _log.warning("_calcular_intraday_score error: %s -> 0.5 (neutral)", e)
        return 0.5


class SPELTradingRouter:
    """
    Router de modos de trading SPEL.

    Consume el output del Score Engine y el Backbone para decidir:
      - Modo INSTITUCIONAL : operar en daily con Kelly completo
      - Modo SCALPING 15M/30M: operar intraday con Kelly reducido
      - FLAT: no operar

    Uso:
        router = SPELTradingRouter()
        decision = router.route(
            activo        = "BTC",
            score_resultado = calcular_score_de_oro(...),
            lstm_output   = engine.inferir(...),
            natural_score = backbone_signal.natural_score,
            df_intraday   = df_15min,      # opcional
            timeframe_min = 15,
            capital       = 50.0,
        )
        print(decision.resumen())
    """

    def __init__(
        self,
        score_institucional:   int   = SCORE_INSTITUCIONAL,
        score_scalping_min:    int   = SCORE_SCALPING_MIN,
        kelly_reduccion_scalp: float = KELLY_REDUCCION_SCALP,
        alpha_min_ratio:       float = ALPHA_MIN_SOBRE_COSTO,
    ):
        self.score_institucional   = score_institucional
        self.score_scalping_min    = score_scalping_min
        self.kelly_reduccion_scalp = kelly_reduccion_scalp
        self.alpha_min_ratio       = alpha_min_ratio
        _log.info(
            "SPELTradingRouter inicializado: inst_min=%d scalp_min=%d kelly_scalp=%.2f",
            score_institucional, score_scalping_min, kelly_reduccion_scalp
        )

    def route(
        self,
        activo:          str,
        score_resultado: dict,
        lstm_output:     dict,
        natural_score:   float = 0.5,
        df_intraday      = None,
        timeframe_min:   int   = 15,
        capital:         float = 50.0,
    ) -> DecisionTrading:
        """
        Decide el modo de trading para el activo en el momento actual.

        Parametros:
            activo          : "BTC" | "NVDA" | "XAU" | "NIFTY50"
            score_resultado : output de calcular_score_de_oro()
            lstm_output     : output de SPELInferenceEngine.inferir()
            natural_score   : BackboneSignal.natural_score (Bayesiano 0-1)
            df_intraday     : DataFrame con velas 15m o 30m (opcional)
            timeframe_min   : 15 o 30 (minutos de las velas intraday)
            capital         : capital disponible en broker ($)
        """
        score        = int(score_resultado.get("score", 0))
        godel_activo = bool(lstm_output.get("godel_activo", False))
        direccion    = score_resultado.get("direccion") or "FLAT"
        fakeout      = bool(score_resultado.get("fakeout", False))
        razon        = []
        ts           = datetime.now(timezone.utc).isoformat()

        # Modelo de costos
        costo_pct = 0.20  # default BTC conservador
        if HAS_COST_MODEL:
            try:
                costo_pct = SPELCostModel.resumen(activo)["total_pct"]
            except Exception:
                pass

        # Guard: fakeout -> FLAT siempre
        if fakeout:
            razon.append("FAKEOUT detectado -> FLAT obligatorio")
            return self._flat(activo, score, godel_activo, costo_pct, razon, ts)

        # Guard: Godel requerido para operar
        if not godel_activo:
            razon.append(f"Godel INACTIVO (entropy < P90) -> sin combustible -> FLAT")
            return self._flat(activo, score, godel_activo, costo_pct, razon, ts)

        # Guard: score minimo
        if score < self.score_scalping_min:
            razon.append(f"Score {score} < minimo {self.score_scalping_min} -> FLAT")
            return self._flat(activo, score, godel_activo, costo_pct, razon, ts)

        # Calcular intraday score si hay datos
        intraday_score = _calcular_intraday_score(df_intraday, timeframe_min)

        # MODO INSTITUCIONAL: Score >= 90 + Godel activo
        if score >= self.score_institucional:
            kelly_f = float(np.clip(natural_score * 0.25, 0.01, 0.05))
            alpha_est = float((natural_score - 0.5) * 2 * RR_INSTITUCIONAL * costo_pct * 3)
            viable = alpha_est >= costo_pct * self.alpha_min_ratio
            razon.append(f"Score={score} >= {self.score_institucional} + Godel ACTIVO -> INSTITUCIONAL")
            razon.append(f"Kelly={kelly_f:.4f}, RR={RR_INSTITUCIONAL}x, alpha_est={alpha_est:.2f}%")
            if not viable:
                razon.append(f"ADVERTENCIA: alpha_est {alpha_est:.2f}% < costo*{self.alpha_min_ratio} {costo_pct*self.alpha_min_ratio:.2f}%")
            return DecisionTrading(
                activo=activo, modo=ModoTrading.INSTITUCIONAL,
                score_oro=score, godel_activo=godel_activo,
                natural_score=natural_score, kiereccion=direccion,
                kelly_fraccion=kelly_f, rr_objetivo=RR_INSTITUCIONAL,
                costo_estimado_pct=costo_pct, alpha_estimado_pct=alpha_est,
                viable=viable, razon=razon,
                intraday_score=intraday_score, ts_utc=ts,
            )

        # MODO SCALPING: Score 70-89 + Godel activo
        # Requiere confirmacion intraday > 0.6 para ser viable
        modo_scalp = ModoTrading.SCALPING_15M if timeframe_min <= 15 else ModoTrading.SCALPING_30M
        kelly_f = float(np.clip(natural_score * 0.25 * self.kelly_reduccion_scalp, 0.005, 0.025))
        alpha_est = float((natural_score - 0.5) * 2 * RR_SCALPING * costo_pct * 2)
        viable = (alpha_est >= costo_pct * self.alpha_min_ratio) and (intraday_score >= 0.6)

        razon.append(f"Score={score} en rango scalping [{self.score_scalping_min}-{self.score_institucional-1}]")
        razon.append(f"Intraday score={intraday_score:.2f} (min 0.60 para viable)")
        razon.append(f"Kelly={kelly_f:.4f} (reducido {self.kelly_reduccion_scalp*100:.0f}%), RR={RR_SCALPING}x")
        razon.append(f"alpha_est={alpha_est:.2f}% vs costo={costo_pct:.2f}%")
        if not viable:
            if intraday_score < 0.6:
                razon.append(f"NO VIABLE: confirmacion intraday insuficiente ({intraday_score:.2f} < 0.60)")
            else:
                razon.append(f"NO VIABLE: alpha {alpha_est:.2f}% insuficiente para cubrir costo {costo_pct:.2f}%")

        return DecisionTrading(
            activo=activo, modo=modo_scalp,
            score_oro=score, godel_activo=godel_activo,
            natural_score=natural_score, kiereccion=direccion,
            kelly_fraccion=kelly_f, rr_objetivo=RR_SCALPING,
            costo_estimado_pct=costo_pct, alpha_estimado_pct=alpha_est,
            viable=viable, razon=razon,
            intraday_score=intraday_score, ts_utc=ts,
        )

    def _flat(self, activo, score, godel, costo_pct, razon, ts) -> DecisionTrading:
        return DecisionTrading(
            activo=activo, modo=ModoTrading.FLAT,
            score_oro=score, godel_activo=godel,
            natural_score=0.5, kiereccion="FLAT",
            kelly_fraccion=0.0, rr_objetivo=0.0,
            costo_estimado_pct=costo_pct, alpha_estimado_pct=0.0,
            viable=False, razon=razon,
            intraday_score=0.5, ts_utc=ts,
        )
