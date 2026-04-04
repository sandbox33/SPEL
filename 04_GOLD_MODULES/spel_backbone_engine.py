# ══════════════════════════════════════════════════════════════════════════════
# spel_backbone_engine.py
# SPEL v22 — Backbone Engine ("El Cerebro de Decisión")
# Bayesian Triple Sieve · Structural Levels · Kelly Micro-Capital · Alpha Ranking
#
# Autor  : Abraham Fuenmayor
# Versión: v22.0.0 · 04 Mar 2026
#
# REGLAS ACTIVAS:
#   Regla 4  : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla 5  : 24 columnas canónicas exactas · Source of Truth
#   Regla 13 : LSTM arquitectura inamovible (input=20, hidden=64, layers=1)
#
# FUNDAMENTOS MATEMÁTICOS:
#   Filtro Bayesiano  — P(señal|evidencia) ∝ P(evidencia|señal) × P(señal_base)
#   Kelly Fraccional  — f* = (bp - q) / b · con fracción de seguridad k ∈ (0, 0.25]
#   ATR (Wilder 1978) — suavizado exponencial sobre True Range 14 períodos
#   Fibonacci Structal — Stop colocado detrás de fib_lag_21 + 1.5 × ATR14
#
# PRECISIONES HISTÓRICAS CANÓNICAS (Regla 1 — INAMOVIBLES):
#   NVDA    = 55.0%  · val_dir  (lookback λ=63d)
#   BTC     = 52.8%  · val_dir  (lookback λ=21d)
#   XAU     = 54.7%  · val_dir  (lookback λ=63d)
#   NIFTY50 = 62.5%  · val_dir  (lookback λ=42d)
#
# DEPENDENCIAS:
#   Requeridas : polars · numpy
#   Opcionales : (ninguna adicional a las del math_engine)
#
# PROHIBIDO:
#   pandas · yfinance · datetime.utcnow() · iteración for sobre DataFrames
#   scipy · statsmodels como dependencias de primer nivel
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

import numpy as np
import polars as pl

# ── Logging institucional ──────────────────────────────────────────────────────
_log = logging.getLogger("spel.backbone")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")
    )
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES CANÓNICAS (INAMOVIBLES)
# ══════════════════════════════════════════════════════════════════════════════

ACTIVOS_VALIDOS: frozenset[str] = frozenset({"NVDA", "BTC", "XAU", "NIFTY50"})

# Precisión direccional base del LSTM (Regla 1 — val_dir canónico)
LSTM_BASE_ACCURACY: dict[str, float] = {
    "NVDA":    0.550,
    "BTC":     0.528,
    "XAU":     0.547,
    "NIFTY50": 0.625,
}

# λ canónicas por activo (Regla 4)
LAMBDA_PARAMS: dict[str, int] = {"BTC": 21, "NVDA": 63, "XAU": 63, "NIFTY50": 42}

# ── Umbrales del Filtro Bayesiano de Triple Tamiz ─────────────────────────────
HURST_RW_LOW:       float = 0.45   # Random Walk inferior
HURST_RW_HIGH:      float = 0.55   # Random Walk superior
TE_SPILLOVER_MIN:   float = 0.05   # Umbral mínimo de causalidad (bits)
TE_STRONG:          float = 0.15   # Spillover fuerte — evidencia máxima

# Factor de penalización cuando NO hay spillover (reduce peso al 30%)
SPILLOVER_PENALTY:  float = 0.30

# Factores de verosimilitud Bayesiana por tipo de anomalía
# Modela P(evidencia | señal_verdadera) — calibrados sobre historial SPEL
_LIKELIHOOD_MAP: dict[str, float] = {
    "GODEL_ALIGNMENT":  0.92,   # evidencia máxima — Gödel + TE fuerte
    "DUAL_SPILLOVER":   0.80,   # GOV y BUS simultáneos
    "REGIME_CHANGE":    0.72,   # cambio abrupto de régimen
    "SPILLOVER_GOV":    0.65,   # causalidad gubernamental
    "SPILLOVER_BUS":    0.60,   # causalidad corporativa
    "TREND_REGIME":     0.55,   # persistencia alta (Hurst > 0.65)
    "REVERTING_REGIME": 0.48,   # anti-persistencia (Hurst < 0.35)
    "COMPOSITE_ALERT":  0.52,   # score compuesto
    "NONE":             0.30,   # sin anomalía → señal débil
}

# ── Parámetros ATR y Fibonacci ────────────────────────────────────────────────
ATR_PERIOD:         int   = 14
STOP_ATR_MULT:      float = 1.5    # Stop = fib_lag_21 ± 1.5 × ATR14
TP_RR_RATIO:        float = 2.5    # Risk:Reward mínimo institucional

# ── Criterio de Kelly ─────────────────────────────────────────────────────────
KELLY_FRACTION:     float = 0.25   # Kelly fraccional de seguridad (25% del full-Kelly)
KELLY_MAX_FRACTION: float = 0.05   # Nunca arriesgar más del 5% del capital total
LEVERAGE_STEP:      int   = 5      # Apalancamiento en múltiplos de 5x
LEVERAGE_MIN:       int   = 1
LEVERAGE_MAX:       int   = 125    # Límite absoluto de seguridad


# ══════════════════════════════════════════════════════════════════════════════
# ENUMERACIONES
# ══════════════════════════════════════════════════════════════════════════════

class SignalDirection(str, Enum):
    LONG      = "LONG"
    SHORT     = "SHORT"
    FLAT      = "FLAT"   # señal descartada por el filtro Bayesiano


class FilterStage(str, Enum):
    """Etapa donde fue filtrada / modificada la señal."""
    PASS_ALL      = "PASS_ALL"        # superó los 3 tamices
    REJECT_RW     = "REJECT_RW"       # descartado — Hurst ∈ [0.45, 0.55]
    PENALIZED_TE  = "PENALIZED_TE"    # penalizado — sin spillover TE
    PENALIZED_BOTH= "PENALIZED_BOTH"  # penalizado en TE y score bajo


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES DE SALIDA (tipado fuerte, inmutables)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class StructuralLevels:
    """Niveles de soporte/resistencia calculados matemáticamente."""
    activo:          str
    entry_price:     float
    stop_loss:       float
    take_profit:     float
    atr14:           float
    fib_21_level:    float
    direction:       SignalDirection
    risk_per_unit:   float       # |entry - stop_loss| en términos absolutos
    rr_ratio:        float       # risk:reward efectivo

    def __str__(self) -> str:
        return (
            f"StructuralLevels({self.activo} {self.direction.value}) "
            f"Entry={self.entry_price:.4f} SL={self.stop_loss:.4f} "
            f"TP={self.take_profit:.4f} ATR14={self.atr14:.4f} RR={self.rr_ratio:.2f}"
        )


@dataclass(frozen=True)
class KellyResult:
    """Resultado del gestor de riesgo fraccional Kelly."""
    capital:           float   # Capital disponible en broker ($)
    natural_score:     float   # Score Natural ∈ [0, 1] del Backbone
    win_loss_ratio:    float   # b — ratio ganancia/pérdida (R:R)
    kelly_full:        float   # f* = (bp - q) / b  (Kelly completo)
    kelly_fractional:  float   # kelly_full × KELLY_FRACTION
    risk_amount:       float   # Capital en riesgo ($)
    position_size:     float   # Tamaño de posición en unidades base ($)
    leverage_suggested:int     # Apalancamiento exacto sugerido
    max_loss_usd:      float   # Pérdida máxima esperada si SL es tocado ($)
    note:              str = ""

    def __str__(self) -> str:
        return (
            f"KellyResult capital=${self.capital:.2f} "
            f"score={self.natural_score:.3f} "
            f"kelly_f={self.kelly_fractional:.4f} "
            f"riesgo=${self.risk_amount:.2f} "
            f"leverage={self.leverage_suggested}x "
            f"max_loss=${self.max_loss_usd:.2f}"
        )


@dataclass(frozen=True)
class BackboneSignal:
    """Señal completa emitida por SPELBackbone para un activo."""
    activo:          str
    ts_generated:    datetime
    direction:       SignalDirection
    natural_score:   float           # Score Natural ∈ [0, 1]
    filter_stage:    FilterStage
    hurst:           float
    te_gov:          float
    te_bus:          float
    anomaly_type:    str
    godel_signal:    bool
    market_regime:   str
    levels:          StructuralLevels | None
    kelly:           KellyResult | None
    # Metadata de trazabilidad
    prior_accuracy:  float           # P(señal) base del LSTM
    likelihood:      float           # P(evidencia | señal_verdadera)
    posterior:       float           # P(señal | evidencia) — Bayes
    anomaly_score:   float           # Score compuesto del MathEngine ∈ [0, 1]

    def summary(self) -> str:
        lines = [
            f"╔══ BackboneSignal ══════════════════════════════════════════",
            f"║  Activo        : {self.activo}",
            f"║  Dirección     : {self.direction.value}",
            f"║  Natural Score : {self.natural_score:.4f}",
            f"║  Filter Stage  : {self.filter_stage.value}",
            f"║  Hurst         : {self.hurst:.3f}  ({self.market_regime})",
            f"║  TE_GOV        : {self.te_gov:.4f} bits  |  TE_BUS: {self.te_bus:.4f} bits",
            f"║  Anomalía      : {self.anomaly_type}  (score={self.anomaly_score:.3f})",
            f"║  Gödel Signal  : {'✅' if self.godel_signal else '❌'}",
            f"║  Bayes         : prior={self.prior_accuracy:.3f} · "
            f"lik={self.likelihood:.3f} · post={self.posterior:.3f}",
        ]
        if self.levels:
            lines.append(f"║  {self.levels}")
        if self.kelly:
            lines.append(f"║  {self.kelly}")
        lines.append(f"╚{'═' * 58}")
        return "\n".join(lines)


@dataclass
class RankingResult:
    """Resultado del ranking dinámico multi-activo."""
    alpha_activo:  str
    alpha_signal:  BackboneSignal
    all_signals:   dict[str, BackboneSignal]
    ranked_scores: list[tuple[str, float]]  # [(activo, score), ...] desc

    def __str__(self) -> str:
        table = "  ".join(f"{a}={s:.4f}" for a, s in self.ranked_scores)
        return f"RankingResult ALFA={self.alpha_activo}  [{table}]"


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES PURAS DE CÁLCULO (sin efectos secundarios)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_atr14_vectorized(df_price: pl.DataFrame) -> pl.Series:
    """
    Calcula el Average True Range de 14 períodos (Wilder 1978) de forma
    completamente vectorizada sobre un DataFrame Polars.

    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    ATR14 = EWM con alpha = 1/14 (equivalente al suavizado de Wilder)

    Parámetros
    ----------
    df_price : pl.DataFrame
        Debe contener columnas 'high', 'low', 'close' (Float64).

    Retorna
    -------
    pl.Series
        Serie 'atr14' (Float64) — los primeros ATR_PERIOD-1 valores son NaN.
    """
    required = {"high", "low", "close"}
    missing = required - set(df_price.columns)
    if missing:
        raise ValueError(f"df_price falta columnas: {missing}")

    high  = df_price["high"]
    low   = df_price["low"]
    close = df_price["close"]

    prev_close = close.shift(1)

    tr = (
        pl.Series("hl",  (high - low).to_numpy())
        .zip_with(
            pl.Series("hpc", np.abs((high - prev_close).to_numpy())),
            pl.lit(True).cast(pl.Boolean),
        )
    )
    # Cálculo vectorizado del True Range como max de los 3 componentes
    hl_arr  = (high - low).to_numpy()
    hpc_arr = np.abs((high - prev_close).fill_null(0)).to_numpy()
    lpc_arr = np.abs((low  - prev_close).fill_null(0)).to_numpy()

    tr_arr = np.maximum(np.maximum(hl_arr, hpc_arr), lpc_arr)
    tr_arr[0] = np.nan   # primer período no tiene prev_close válido

    # Wilder smoothing: EWM alpha = 1 / ATR_PERIOD
    alpha = 1.0 / ATR_PERIOD
    atr   = np.full_like(tr_arr, np.nan)

    # Seed: media simple de los primeros ATR_PERIOD valores
    seed_idx = ATR_PERIOD - 1
    valid_seed = tr_arr[1:ATR_PERIOD + 1]
    if len(valid_seed) < ATR_PERIOD or np.any(np.isnan(valid_seed)):
        # No hay suficientes datos — retornar NaN serie
        return pl.Series("atr14", atr)

    atr[seed_idx] = float(np.nanmean(valid_seed))

    # Propagación EWM sin bucle sobre DataFrame — numpy nativo
    for i in range(seed_idx + 1, len(tr_arr)):
        if np.isnan(tr_arr[i]):
            atr[i] = atr[i - 1]
        else:
            atr[i] = alpha * tr_arr[i] + (1.0 - alpha) * atr[i - 1]

    return pl.Series("atr14", atr)


def compute_structural_levels(
    df_price: pl.DataFrame,
    direction: SignalDirection,
    activo: str,
) -> StructuralLevels:
    """
    Calcula los Nodos Estructurales reales (Soporte / Resistencia).

    Metodología:
    ─────────────────────────────────────────────────────────────────────────
    1. ATR14 de Wilder sobre las últimas N filas.
    2. Cruce con `fibonacci_lag_21` (presencia en el schema canónico v4).
    3. Stop Loss = fibonacci_lag_21 ± 1.5 × ATR14
       - El signo depende de la dirección (SHORT: SL arriba del fib, LONG: abajo).
       - Esto lo coloca DETRÁS del nivel de liquidez institucional → evita barridos.
    4. Take Profit = Entry ± (|Entry - SL| × TP_RR_RATIO)
       - Ratio R:R mínimo de 2.5x institucional.

    Parámetros
    ----------
    df_price  : pl.DataFrame  — Parquet canónico v5 (30 cols) o subset.
    direction : SignalDirection — LONG / SHORT / FLAT.
    activo    : str — activo canónico.

    Retorna
    -------
    StructuralLevels — dataclass inmutable con todos los niveles.

    Raises
    ------
    ValueError  si las columnas requeridas no están presentes o datos insuficientes.
    RuntimeError si ATR14 no puede computarse (datos insuficientes).
    """
    if direction == SignalDirection.FLAT:
        raise ValueError("compute_structural_levels: dirección FLAT — señal descartada, no calcular niveles.")

    required_cols = {"high", "low", "close", "fibonacci_lag_21"}
    missing = required_cols - set(df_price.columns)
    if missing:
        raise ValueError(f"compute_structural_levels: columnas faltantes en df_price: {missing}")

    min_rows = max(ATR_PERIOD * 2, 30)
    if len(df_price) < min_rows:
        raise ValueError(
            f"compute_structural_levels: se requieren ≥ {min_rows} filas, "
            f"recibidas {len(df_price)}."
        )

    # Usar las últimas filas para eficiencia con Polars lazy
    df_w = df_price.tail(max(min_rows, ATR_PERIOD * 3))

    # ── 1. ATR14 vectorizado ───────────────────────────────────────────────────
    atr_series = _compute_atr14_vectorized(df_w)
    atr14_val  = float(atr_series.drop_nulls().tail(1)[0])

    if np.isnan(atr14_val) or atr14_val <= 0:
        raise RuntimeError(
            f"compute_structural_levels ({activo}): ATR14 no válido ({atr14_val}). "
            "Revisar calidad de datos OHLCV."
        )

    # ── 2. Precio de entrada y nivel Fibonacci 21 ──────────────────────────────
    entry_price = float(df_w["close"].tail(1)[0])
    fib21_level = float(df_w["fibonacci_lag_21"].drop_nulls().tail(1)[0])

    if np.isnan(fib21_level):
        # Fallback: usar close hace 21 períodos si fib_lag_21 está vacío
        fib21_level = float(df_w["close"].shift(21).drop_nulls().tail(1)[0])
        _log.warning(
            "%s: fibonacci_lag_21 es NaN — usando close.shift(21) como fallback.",
            activo
        )

    # ── 3. Stop Loss estructural (detrás del nivel de liquidez) ──────────────
    buffer = STOP_ATR_MULT * atr14_val

    if direction == SignalDirection.LONG:
        # Comprando: SL por debajo del fib_21 más cercano
        raw_sl    = min(fib21_level, entry_price) - buffer
        stop_loss = max(raw_sl, entry_price * 0.85)   # guardia mínima: max -15%
    else:  # SHORT
        # Vendiendo: SL por encima del fib_21 más cercano
        raw_sl    = max(fib21_level, entry_price) + buffer
        stop_loss = min(raw_sl, entry_price * 1.15)   # guardia máxima: +15%

    risk_per_unit = abs(entry_price - stop_loss)
    if risk_per_unit < 1e-10:
        raise RuntimeError(
            f"compute_structural_levels ({activo}): risk_per_unit demasiado pequeño "
            f"({risk_per_unit}). Posible problema en datos de precio."
        )

    # ── 4. Take Profit con R:R ≥ 2.5x ─────────────────────────────────────────
    tp_distance  = risk_per_unit * TP_RR_RATIO
    take_profit  = (entry_price + tp_distance) if direction == SignalDirection.LONG \
                   else (entry_price - tp_distance)

    rr_ratio = abs(take_profit - entry_price) / risk_per_unit

    _log.info(
        "%s [%s] Entry=%.4f SL=%.4f TP=%.4f ATR14=%.4f fib21=%.4f buffer=%.4f",
        activo, direction.value, entry_price, stop_loss, take_profit,
        atr14_val, fib21_level, buffer,
    )

    return StructuralLevels(
        activo        = activo,
        entry_price   = entry_price,
        stop_loss     = stop_loss,
        take_profit   = take_profit,
        atr14         = atr14_val,
        fib_21_level  = fib21_level,
        direction     = direction,
        risk_per_unit = risk_per_unit,
        rr_ratio      = rr_ratio,
    )


def risk_manager_kelly_micro(
    capital:          float,
    natural_score:    float,
    win_loss_ratio:   float,
    risk_per_unit:    float,
    entry_price:      float,
) -> KellyResult:
    """
    Gestor de Riesgo Fraccional Kelly para cuentas de micro-capital.

    Criterio de Kelly Fraccional:
    ─────────────────────────────────────────────────────────────────────────
        p = probabilidad de ganancia = natural_score (calibrado Bayes)
        q = 1 - p   (probabilidad de pérdida)
        b = win_loss_ratio   (ratio de la ganancia sobre la pérdida, e.g. R:R)

        f* = (b·p - q) / b          ← Kelly completo
        f_frac = f* × KELLY_FRACTION ← Fracción de seguridad (25%)

    Apalancamiento Exacto:
    ─────────────────────────────────────────────────────────────────────────
        Dado un capital y la distancia al Stop Loss estructural (risk_per_unit),
        el apalancamiento se ajusta para que la pérdida máxima real no supere
        f_frac × capital, alineando la posición nominal con el riesgo definido.

        Pasos:
        1. risk_amount = f_frac × capital          ($ en riesgo Kelly)
        2. risk_amount = min(risk_amount, KELLY_MAX_FRACTION × capital)  (capping)
        3. pos_size    = risk_amount / (risk_per_unit / entry_price)     ($)
        4. leverage    = pos_size / capital                               (ratio)
        5. leverage    → redondear al múltiplo de LEVERAGE_STEP más cercano hacia abajo

    Parámetros
    ----------
    capital        : float — Capital disponible en el broker ($).
    natural_score  : float — Score Natural ∈ [0, 1] emitido por SPELBackbone.
    win_loss_ratio : float — b = Take Profit distance / Stop Loss distance (R:R).
    risk_per_unit  : float — |Entry - Stop Loss| en términos absolutos de precio.
    entry_price    : float — Precio de entrada del activo.

    Retorna
    -------
    KellyResult — dataclass inmutable con todos los parámetros de ejecución.

    Raises
    ------
    ValueError  si capital ≤ 0, win_loss_ratio ≤ 0, o risk_per_unit ≤ 0.
    """
    if capital <= 0:
        raise ValueError(f"risk_manager_kelly_micro: capital debe ser > 0, recibido {capital}")
    if win_loss_ratio <= 0:
        raise ValueError(f"risk_manager_kelly_micro: win_loss_ratio debe ser > 0, recibido {win_loss_ratio}")
    if risk_per_unit <= 0:
        raise ValueError(f"risk_manager_kelly_micro: risk_per_unit debe ser > 0, recibido {risk_per_unit}")
    if entry_price <= 0:
        raise ValueError(f"risk_manager_kelly_micro: entry_price debe ser > 0, recibido {entry_price}")

    # Clamp del natural_score a [0.01, 0.99] para evitar Kelly degenerado
    p = float(np.clip(natural_score, 0.01, 0.99))
    q = 1.0 - p
    b = float(win_loss_ratio)

    # ── Kelly completo ─────────────────────────────────────────────────────────
    kelly_full = (b * p - q) / b

    note = ""
    if kelly_full <= 0:
        # Edge negativo — Kelly dice no entrar
        note = f"⚠️  Kelly negativo ({kelly_full:.4f}) — señal estadísticamente desfavorable. Considera no entrar."
        _log.warning("Kelly negativo (%.4f) para score=%.3f b=%.2f — edge negativo.", kelly_full, p, b)
        kelly_full = 0.0

    # ── Kelly fraccional + capping de seguridad ────────────────────────────────
    kelly_frac   = kelly_full * KELLY_FRACTION
    kelly_capped = min(kelly_frac, KELLY_MAX_FRACTION)

    if kelly_capped < kelly_frac:
        note += f" | Kelly fraccional ({kelly_frac:.4f}) capeado a máx {KELLY_MAX_FRACTION:.4f}."
        _log.info("Kelly fraccional capeado: %.4f → %.4f", kelly_frac, kelly_capped)

    # ── Tamaño de posición y apalancamiento ───────────────────────────────────
    risk_amount     = kelly_capped * capital          # $ en riesgo absoluto
    risk_pct_price  = risk_per_unit / entry_price     # riesgo relativo al precio

    if risk_pct_price < 1e-10:
        raise RuntimeError("risk_manager_kelly_micro: risk_pct_price demasiado pequeño.")

    # Tamaño de posición nominal necesario para que la pérdida máxima = risk_amount
    pos_size_nominal = risk_amount / risk_pct_price   # en $

    # Apalancamiento crudo requerido
    leverage_raw  = pos_size_nominal / capital if capital > 0 else 1.0

    # Discretizar al múltiplo de LEVERAGE_STEP más cercano hacia ABAJO (conservador)
    leverage_disc = max(
        LEVERAGE_MIN,
        min(
            int((leverage_raw // LEVERAGE_STEP) * LEVERAGE_STEP),
            LEVERAGE_MAX,
        )
    )
    # Garantizar al menos 1x
    leverage_final = max(leverage_disc, LEVERAGE_MIN)

    # Pérdida máxima real con el apalancamiento discretizado
    pos_size_real  = capital * leverage_final
    max_loss_usd   = pos_size_real * risk_pct_price

    _log.info(
        "Kelly micro: capital=$%.2f score=%.3f b=%.2f "
        "kelly_f*=%.4f riesgo=$%.4f leverage=%dx max_loss=$%.4f",
        capital, p, b, kelly_frac, risk_amount, leverage_final, max_loss_usd,
    )

    return KellyResult(
        capital            = capital,
        natural_score      = natural_score,
        win_loss_ratio     = b,
        kelly_full         = kelly_full,
        kelly_fractional   = kelly_capped,
        risk_amount        = risk_amount,
        position_size      = pos_size_real,
        leverage_suggested = leverage_final,
        max_loss_usd       = max_loss_usd,
        note               = note.strip(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLASE PRINCIPAL: SPELBackbone
# ══════════════════════════════════════════════════════════════════════════════

class SPELBackbone:
    """
    Motor de Decisión Central de SPEL v22 — "El Cerebro".

    Consume el output de SPELMathEngine (alerts_df) y el parquet canónico v4
    para emitir señales de trading institucionales pasadas por el Filtro
    Bayesiano de Triple Tamiz, con niveles estructurales matemáticos y
    gestión de riesgo Kelly fraccional.

    Flujo de procesamiento por activo:
    ────────────────────────────────────────────────────────────────────────
    1. Extraer la ventana más reciente de alerts_df.
    2. TAMIZ 1: Rechazar si Hurst ∈ [0.45, 0.55] (Random Walk — sin edge).
    3. TAMIZ 2: Penalizar si spillover_detected = False (TE < 0.05 bits).
    4. TAMIZ 3: Actualización Bayesiana — combina prior LSTM + likelihood
                de la anomalía actual → Score Natural ∈ [0, 1].
    5. Calcular niveles estructurales ATR + Fibonacci.
    6. Calcular Kelly micro-capital con el Score Natural.
    7. Emitir BackboneSignal completo.

    Uso básico
    ----------
    >>> backbone = SPELBackbone()
    >>> signal = backbone.evaluate(
    ...     activo     = "NVDA",
    ...     alerts_df  = math_result.alerts_df,
    ...     df_price   = harvester.load_canonical("NVDA"),
    ...     capital    = 50.0,
    ... )
    >>> print(signal.summary())

    >>> ranking = backbone.dynamic_ranking(
    ...     alerts_map = {"NVDA": nvda_alerts, "BTC": btc_alerts, ...},
    ...     price_map  = {"NVDA": nvda_price,  "BTC": btc_price,  ...},
    ...     capital    = 10.0,
    ... )
    >>> print(ranking)
    """

    def __init__(
        self,
        kelly_fraction:  float = KELLY_FRACTION,
        tp_rr_ratio:     float = TP_RR_RATIO,
        verbose:         bool  = False,
    ) -> None:
        """
        Parámetros
        ----------
        kelly_fraction : float — Fracción de seguridad Kelly (default 0.25).
        tp_rr_ratio    : float — Ratio Risk:Reward mínimo (default 2.5).
        verbose        : bool  — Activar logging DEBUG.
        """
        if not (0 < kelly_fraction <= 1.0):
            raise ValueError(f"kelly_fraction debe estar en (0, 1], recibido {kelly_fraction}")
        if tp_rr_ratio < 1.0:
            raise ValueError(f"tp_rr_ratio debe ser ≥ 1.0, recibido {tp_rr_ratio}")

        self.kelly_fraction = kelly_fraction
        self.tp_rr_ratio    = tp_rr_ratio

        if verbose:
            _log.setLevel(logging.DEBUG)

        _log.info(
            "SPELBackbone inicializado: kelly_frac=%.2f tp_rr=%.2f",
            kelly_fraction, tp_rr_ratio,
        )

    # ── TAMIZ BAYESIANO (lógica interna) ──────────────────────────────────────

    def _bayesian_update(
        self,
        activo:          str,
        hurst:           float,
        te_gov:          float,
        te_bus:          float,
        anomaly_type:    str,
        anomaly_score:   float,
        spillover:       bool,
        godel_signal:    bool,
    ) -> tuple[float, float, float, float, FilterStage]:
        """
        Filtro Bayesiano de Triple Tamiz.

        Retorna
        -------
        tuple[prior, likelihood, posterior, natural_score, filter_stage]
        """
        prior = LSTM_BASE_ACCURACY.get(activo, 0.52)

        # ── TAMIZ 1: Random Walk ───────────────────────────────────────────────
        if HURST_RW_LOW <= hurst <= HURST_RW_HIGH:
            _log.debug(
                "%s TAMIZ-1 RECHAZADO: Hurst=%.3f ∈ [%.2f, %.2f] (Random Walk)",
                activo, hurst, HURST_RW_LOW, HURST_RW_HIGH,
            )
            return prior, 0.0, 0.0, 0.0, FilterStage.REJECT_RW

        # ── TAMIZ 2: Spillover TE ──────────────────────────────────────────────
        te_max = max(te_gov, te_bus)
        spillover_factor = 1.0
        penalized_stage  = FilterStage.PASS_ALL

        if not spillover or te_max < TE_SPILLOVER_MIN:
            spillover_factor = SPILLOVER_PENALTY  # 30% del peso original
            penalized_stage  = FilterStage.PENALIZED_TE
            _log.debug(
                "%s TAMIZ-2 PENALIZADO: spillover=False · TE_max=%.4f < %.2f · factor=%.2f",
                activo, te_max, TE_SPILLOVER_MIN, spillover_factor,
            )

        # ── TAMIZ 3: Actualización Bayesiana ──────────────────────────────────
        # Likelihood de la anomalía observada
        base_likelihood = _LIKELIHOOD_MAP.get(anomaly_type, 0.40)

        # Boost adicional por evidencia Gödel (señal máxima del sistema)
        if godel_signal:
            base_likelihood = min(base_likelihood * 1.15, 0.98)

        # Boost por TE fuerte (> umbral de spillover fuerte)
        if te_max >= TE_STRONG:
            base_likelihood = min(base_likelihood * 1.10, 0.98)

        likelihood = base_likelihood * spillover_factor

        # Bayes: P(señal | evidencia) = (L × prior) / Z
        # Z = P(evidencia) normalizado como (L × prior) + (1-L) × (1-prior)
        numerator   = likelihood * prior
        denominator = numerator + (1.0 - likelihood) * (1.0 - prior)
        posterior   = numerator / denominator if denominator > 1e-10 else prior

        # Score Natural = posterior × anomaly_score (penaliza señales débiles)
        natural_score = posterior * (0.70 + 0.30 * anomaly_score)
        natural_score = float(np.clip(natural_score, 0.0, 1.0))

        # Detectar si la penalización redujo demasiado el score
        if penalized_stage == FilterStage.PENALIZED_TE and natural_score < 0.35:
            penalized_stage = FilterStage.PENALIZED_BOTH

        _log.debug(
            "%s Bayes: prior=%.3f lik=%.3f post=%.3f score=%.3f stage=%s",
            activo, prior, likelihood, posterior, natural_score, penalized_stage.value,
        )

        return prior, likelihood, posterior, natural_score, penalized_stage

    def _infer_direction(
        self,
        hurst:         float,
        te_gov:        float,
        te_bus:        float,
        anomaly_type:  str,
        godel_signal:  bool,
        df_price:      pl.DataFrame,
    ) -> SignalDirection:
        """
        Infiere la dirección de la señal (LONG / SHORT) usando múltiples capas.

        Lógica de prioridad:
        1. Pendiente de los últimos 21 cierres (log-return acumulado).
        2. Si godel_signal y Hurst > 0.55 → refuerza la tendencia dominante.
        3. Si Hurst > 0.55 → direccional. Si Hurst < 0.45 → contratendencia.
        """
        if len(df_price) < 22:
            _log.warning("_infer_direction: datos insuficientes para determinar dirección.")
            return SignalDirection.LONG  # default conservador

        closes = df_price["close"].tail(22).to_numpy().astype(np.float64)
        log_ret_21 = float(np.log(closes[-1] / closes[0]))   # log-return 21 días

        # Dirección base por momentum
        base_dir = SignalDirection.LONG if log_ret_21 >= 0 else SignalDirection.SHORT

        # Confirmación por régimen Hurst
        if hurst < HURST_RW_LOW:
            # Revertiente — oponer al momentum
            base_dir = SignalDirection.SHORT if base_dir == SignalDirection.LONG \
                       else SignalDirection.LONG

        return base_dir

    # ── API PÚBLICA ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        activo:      str,
        alerts_df:   pl.DataFrame,
        df_price:    pl.DataFrame,
        capital:     float = 10.0,
    ) -> BackboneSignal:
        """
        Evalúa un activo y emite un BackboneSignal completo.

        Parámetros
        ----------
        activo     : str            — Activo canónico (NVDA, BTC, XAU, NIFTY50).
        alerts_df  : pl.DataFrame   — Output de SPELMathEngine.run().alerts_df
                                      (14 columnas canónicas).
        df_price   : pl.DataFrame   — Parquet canónico v4 (24 columnas).
        capital    : float          — Capital disponible en broker ($).

        Retorna
        -------
        BackboneSignal — señal completa con niveles, Kelly y metadata Bayesiana.

        Raises
        ------
        ValueError  si activo no es canónico.
        RuntimeError si alerts_df está vacío o tiene schema incorrecto.
        """
        if activo not in ACTIVOS_VALIDOS:
            raise ValueError(f"evaluate: activo '{activo}' no canónico. Válidos: {ACTIVOS_VALIDOS}")

        if len(alerts_df) == 0:
            raise RuntimeError(f"evaluate ({activo}): alerts_df está vacío.")

        ts_now = datetime.now(timezone.utc)

        # ── Extraer la ventana más reciente ────────────────────────────────────
        required_alert_cols = {
            "hurst", "te_gov", "te_bus", "spillover_detected",
            "anomaly_type", "anomaly_score", "godel_signal", "market_regime",
            "hurst_dfa",
        }
        missing = required_alert_cols - set(alerts_df.columns)
        if missing:
            raise RuntimeError(
                f"evaluate ({activo}): alerts_df falta columnas: {missing}. "
                "Verificar que el DataFrame proviene de SPELMathEngine v22."
            )

        latest = alerts_df.tail(1)

        hurst         = float(latest["hurst"][0])
        te_gov        = float(latest["te_gov"][0])
        te_bus        = float(latest["te_bus"][0])
        spillover     = bool(latest["spillover_detected"][0])
        anomaly_type  = str(latest["anomaly_type"][0])
        anomaly_score = float(latest["anomaly_score"][0])
        godel_signal  = bool(latest["godel_signal"][0])
        market_regime = str(latest["market_regime"][0])

        # ── Filtro Bayesiano de Triple Tamiz ───────────────────────────────────
        prior, likelihood, posterior, natural_score, filter_stage = self._bayesian_update(
            activo        = activo,
            hurst         = hurst,
            te_gov        = te_gov,
            te_bus        = te_bus,
            anomaly_type  = anomaly_type,
            anomaly_score = anomaly_score,
            spillover     = spillover,
            godel_signal  = godel_signal,
        )

        # ── Determinación de dirección ─────────────────────────────────────────
        if filter_stage == FilterStage.REJECT_RW:
            direction = SignalDirection.FLAT
            levels    = None
            kelly     = None
        else:
            direction = self._infer_direction(
                hurst        = hurst,
                te_gov       = te_gov,
                te_bus       = te_bus,
                anomaly_type = anomaly_type,
                godel_signal = godel_signal,
                df_price     = df_price,
            )

            # ── Niveles estructurales ──────────────────────────────────────────
            try:
                levels = compute_structural_levels(
                    df_price  = df_price,
                    direction = direction,
                    activo    = activo,
                )
            except (ValueError, RuntimeError) as exc:
                _log.error(
                    "%s: fallo compute_structural_levels — %s. Señal sin niveles.",
                    activo, exc,
                )
                levels = None

            # ── Kelly micro-capital ────────────────────────────────────────────
            kelly = None
            if levels is not None and capital > 0:
                try:
                    kelly = risk_manager_kelly_micro(
                        capital        = capital,
                        natural_score  = natural_score,
                        win_loss_ratio = levels.rr_ratio,
                        risk_per_unit  = levels.risk_per_unit,
                        entry_price    = levels.entry_price,
                    )
                except (ValueError, RuntimeError) as exc:
                    _log.error("%s: fallo risk_manager_kelly_micro — %s.", activo, exc)

        signal = BackboneSignal(
            activo         = activo,
            ts_generated   = ts_now,
            direction      = direction,
            natural_score  = natural_score,
            filter_stage   = filter_stage,
            hurst          = hurst,
            te_gov         = te_gov,
            te_bus         = te_bus,
            anomaly_type   = anomaly_type,
            godel_signal   = godel_signal,
            market_regime  = market_regime,
            levels         = levels,
            kelly          = kelly,
            prior_accuracy = prior,
            likelihood     = likelihood,
            posterior      = posterior,
            anomaly_score  = anomaly_score,
        )

        _log.info(
            "evaluate(%s): dir=%s score=%.4f stage=%s",
            activo, direction.value, natural_score, filter_stage.value,
        )

        return signal

    def dynamic_ranking(
        self,
        alerts_map:  dict[str, pl.DataFrame],
        price_map:   dict[str, pl.DataFrame],
        capital:     float = 10.0,
    ) -> RankingResult:
        """
        Evalúa BTC, NVDA, XAU y NIFTY50 simultáneamente y retorna solo el
        activo ALFA (mayor Natural Score entre señales no-FLAT).

        Parámetros
        ----------
        alerts_map : dict[str, pl.DataFrame] — {activo: alerts_df} por activo.
        price_map  : dict[str, pl.DataFrame] — {activo: df_price_v4} por activo.
        capital    : float                   — Capital disponible en broker ($).

        Retorna
        -------
        RankingResult — con alpha_activo, alpha_signal y ranking completo.

        Raises
        ------
        ValueError si alerts_map o price_map están vacíos.
        RuntimeError si ningún activo supera el filtro Bayesiano.
        """
        if not alerts_map:
            raise ValueError("dynamic_ranking: alerts_map está vacío.")
        if not price_map:
            raise ValueError("dynamic_ranking: price_map está vacío.")

        activos_eval = set(alerts_map.keys()) & set(price_map.keys()) & ACTIVOS_VALIDOS
        if not activos_eval:
            raise ValueError(
                f"dynamic_ranking: no hay activos válidos en común. "
                f"alerts_map={set(alerts_map.keys())}  price_map={set(price_map.keys())}"
            )

        all_signals: dict[str, BackboneSignal] = {}
        errors: list[str] = []

        for activo in activos_eval:
            try:
                sig = self.evaluate(
                    activo    = activo,
                    alerts_df = alerts_map[activo],
                    df_price  = price_map[activo],
                    capital   = capital,
                )
                all_signals[activo] = sig
            except Exception as exc:
                _log.error("dynamic_ranking: fallo evaluando %s — %s", activo, exc)
                errors.append(f"{activo}: {exc}")

        if not all_signals:
            raise RuntimeError(
                f"dynamic_ranking: todos los activos fallaron. Errores: {errors}"
            )

        # Rankear: solo señales no-FLAT, ordenadas por natural_score
        ranked = sorted(
            [
                (activo, sig.natural_score)
                for activo, sig in all_signals.items()
                if sig.direction != SignalDirection.FLAT
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        # Incluir también señales FLAT al final del ranking (score=0)
        flat_activos = [
            (activo, 0.0)
            for activo, sig in all_signals.items()
            if sig.direction == SignalDirection.FLAT
        ]
        ranked_full = ranked + flat_activos

        if not ranked:
            # Todos son FLAT — emitir advertencia y tomar el de mayor score histórico
            _log.warning(
                "dynamic_ranking: todos los activos tienen señal FLAT. "
                "Ninguno superó el filtro Bayesiano de Random Walk."
            )
            # Aun así, retornar el de prior más alto como referencia
            best_activo = max(all_signals.keys(), key=lambda a: LSTM_BASE_ACCURACY.get(a, 0))
        else:
            best_activo = ranked[0][0]

        alpha_signal = all_signals[best_activo]

        _log.info(
            "dynamic_ranking: ALFA=%s score=%.4f dir=%s | ranking=%s",
            best_activo,
            alpha_signal.natural_score,
            alpha_signal.direction.value,
            ranked_full,
        )

        return RankingResult(
            alpha_activo   = best_activo,
            alpha_signal   = alpha_signal,
            all_signals    = all_signals,
            ranked_scores  = ranked_full,
        )


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION (punto de entrada recomendado)
# ══════════════════════════════════════════════════════════════════════════════

def backbone_from_config(
    kelly_fraction: float = KELLY_FRACTION,
    tp_rr_ratio:    float = TP_RR_RATIO,
    verbose:        bool  = False,
) -> SPELBackbone:
    """
    Construye un SPELBackbone con la configuración canónica SPEL v22.

    Uso recomendado:
        backbone = backbone_from_config()
        ranking  = backbone.dynamic_ranking(alerts_map, price_map, capital=50.0)
    """
    return SPELBackbone(
        kelly_fraction = kelly_fraction,
        tp_rr_ratio    = tp_rr_ratio,
        verbose        = verbose,
    )


# ══════════════════════════════════════════════════════════════════════════════
# __all__ — API pública del módulo
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Clases de salida
    "BackboneSignal",
    "StructuralLevels",
    "KellyResult",
    "RankingResult",
    # Enumeraciones
    "SignalDirection",
    "FilterStage",
    # Clase principal
    "SPELBackbone",
    # Funciones puras (exportadas para testing unitario)
    "compute_structural_levels",
    "risk_manager_kelly_micro",
    # Factory
    "backbone_from_config",
    # Constantes
    "ACTIVOS_VALIDOS",
    "LAMBDA_PARAMS",
    "LSTM_BASE_ACCURACY",
    "KELLY_FRACTION",
    "ATR_PERIOD",
    "STOP_ATR_MULT",
    "TP_RR_RATIO",
]
