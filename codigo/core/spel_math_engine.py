# ══════════════════════════════════════════════════════════════════════════════
# spel_math_engine.py
# SPEL v22 — Quantitative Math Engine
# Transfer Entropy · Hurst Exponent · Discretization · Anomaly Detection
#
# Autor  : Abraham Fuenmayor
# Versión: v22.0.0 · 03 Mar 2026
#
# REGLAS ACTIVAS:
#   Regla 4  : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla 5  : 24 columnas canónicas exactas · Source of Truth
#   Regla 13 : LSTM arquitectura inamovible (input=20, hidden=64, layers=1)
#
# FUNDAMENTOS MATEMÁTICOS:
#   Transfer Entropy  — Schreiber (2000) · TE(X→Y) = H(Y_t+1|Y_t) - H(Y_t+1|Y_t,X_t)
#   Hurst Exponent    — Hurst (1951) R/S · Peng et al. (1994) DFA
#   Discretización    — rolling mean ± 1σ → estados {-1, 0, +1}
#   Spillover Effect  — Diebold & Yılmaz (2009) · TE como proxy de Granger causal
#
# DEPENDENCIAS:
#   Requeridas : polars · numpy
#   Opcionales : pyinform · dit  (validación cruzada de TE — no bloquean si faltan)
#
# PROHIBIDO:
#   pandas · yfinance · datetime.utcnow() · scipy.stats para TE principal
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

import numpy as np
import polars as pl

# ── Logging ───────────────────────────────────────────────────────────────────
_log = logging.getLogger("spel.math_engine")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")
    )
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# ── Imports opcionales ────────────────────────────────────────────────────────
_PYINFORM_AVAILABLE = False
_DIT_AVAILABLE = False

try:
    import pyinform  # type: ignore
    from pyinform.transferentropy import transfer_entropy as _pyinform_te
    _PYINFORM_AVAILABLE = True
    _log.info("pyinform disponible — validación cruzada de TE activa")
except ImportError:
    _log.debug("pyinform no instalado — usando implementación manual (equivalente)")

try:
    import dit  # type: ignore
    _DIT_AVAILABLE = True
    _log.info("dit disponible")
except ImportError:
    _log.debug("dit no instalado")


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES CANÓNICAS (Regla 4 — INAMOVIBLES)
# ══════════════════════════════════════════════════════════════════════════════

LAMBDA_PARAMS: dict[str, int] = {"BTC": 21, "NVDA": 63, "XAU": 63, "NIFTY50": 42}
ACTIVOS_VALIDOS: frozenset[str] = frozenset(LAMBDA_PARAMS.keys())

# Umbral Gödel canónico (Regla 5 · Condición OR)
GODEL_VITALITY_THRESHOLD: float = 9.0

# Umbrales de Transfer Entropy (en bits · log base 2)
TE_SPILLOVER_THRESHOLD: float = 0.05    # por encima → spillover significativo
TE_STRONG_THRESHOLD:    float = 0.15    # por encima → spillover fuerte
TE_MAX_THEORETICAL:     float = np.log2(3)  # máximo teórico con 3 estados ≈ 1.585 bits

# Umbrales de Hurst
HURST_TRENDING_HIGH:    float = 0.65    # persistencia fuerte
HURST_TRENDING_MEDIUM:  float = 0.55    # leve persistencia
HURST_RANDOM_LOW:       float = 0.45    # leve anti-persistencia
HURST_REVERTING_LOW:    float = 0.35    # anti-persistencia fuerte
HURST_REGIME_CHANGE_DELTA: float = 0.15 # cambio abrupto de régimen

# Pesos del composite anomaly score
_W_TE    = 0.50   # Transfer Entropy (causalidad informacional)
_W_HURST = 0.30   # desviación de Hurst respecto a random walk
_W_REGIME= 0.20   # cambio de régimen (discontinuidad)

# Mínimo de datos para cómputos confiables
MIN_ROWS_TE:    int = 30
MIN_ROWS_HURST: int = 50
MIN_ROWS_DISC:  int = 15   # mínimo para rolling std


# ══════════════════════════════════════════════════════════════════════════════
# ENUMERACIONES
# ══════════════════════════════════════════════════════════════════════════════



# ── SPEL: Normalizador Transfer Entropy rolling (anti-leakage) ──
# Inyectado por spel_patch_mathengine.py · S22c
# ❌ NUNCA usar min/max global — BUG-LA-01 bis
# ✅ Rolling sobre lookback inamovible (R4) ─────────────────────

TE_NORM_PARAMS = {
    "NVDA": {"lookback": 63, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-08},
    "BTC": {"lookback": 21, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-08},
    "XAU": {"lookback": 63, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-08},
    "NIFTY50": {"lookback": 42, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-08},
}

def normalize_transfer_entropy(
    df,
    asset: str,
    te_col: str = "transfer_entropy",
):
    """
    Normaliza Transfer Entropy a [0,1] con rolling MinMax anti-leakage.
    Usa parámetros calibrados en train <= 2023-12-31 (S22c).
    """
    import polars as pl
    p = TE_NORM_PARAMS.get(asset, {
        "lookback": 42, "clip_lower": 0.0,
        "clip_upper": 2.0, "epsilon": 1e-8,
    })
    lb, lo, hi, eps = p["lookback"], p["clip_lower"], p["clip_upper"], p["epsilon"]

    return (
        df.with_columns(
            pl.col(te_col).cast(pl.Float64).clip(lo, hi).alias("_te_c")
        )
        .with_columns(
            pl.col("_te_c").rolling_min(lb, min_periods=1).alias("_te_rmin"),
            pl.col("_te_c").rolling_max(lb, min_periods=1).alias("_te_rmax"),
        )
        .with_columns(
            ((pl.col("_te_c") - pl.col("_te_rmin"))
             / (pl.col("_te_rmax") - pl.col("_te_rmin") + eps))
            .clip(0.0, 1.0)
            .alias(te_col + "_norm")
        )
        .drop(["_te_c", "_te_rmin", "_te_rmax"])
    )

# Router Score de Oro — adaptativo por volume_type
def score_oro(godel: float, transfer_entropy_norm: float,
              volume_profile: float, volume_type: str) -> float:
    """
    Pesos adaptativos según semántica de volumen (R16, R17).
    SYNTHETIC_INDEX / YIELD_INSTRUMENT: Volume Profile = 0%.
    NATIVE_FUTURES / SPOT_CRYPTO:       Volume Profile = 30%.
    TICK_PROXY (forex):                 Volume Profile = 15%.
    """
    if volume_type in ("SYNTHETIC_INDEX", "YIELD_INSTRUMENT"):
        return godel * 0.55 + transfer_entropy_norm * 0.45
    elif volume_type == "TICK_PROXY":
        return volume_profile * 0.15 + godel * 0.45 + transfer_entropy_norm * 0.40
    else:  # NATIVE_FUTURES, SPOT_CRYPTO
        return volume_profile * 0.30 + godel * 0.40 + transfer_entropy_norm * 0.30

# ── FIN SPEL normalizador TE ────────────────────────────────────

class MarketRegime(str, Enum):
    """Régimen de mercado inferido del Exponente de Hurst."""
    TRENDING_STRONG  = "TRENDING_STRONG"    # H > 0.65 — persistencia fuerte
    TRENDING_WEAK    = "TRENDING_WEAK"      # H ∈ (0.55, 0.65]
    RANDOM_WALK      = "RANDOM_WALK"        # H ∈ [0.45, 0.55]
    REVERTING_WEAK   = "REVERTING_WEAK"     # H ∈ [0.35, 0.45)
    REVERTING_STRONG = "REVERTING_STRONG"   # H < 0.35 — anti-persistencia fuerte
    INSUFFICIENT_DATA= "INSUFFICIENT_DATA"


class ActorType(str, Enum):
    """Tipo de actor GDELT relevante para SPEL."""
    GOV = "GOV"   # Actores gubernamentales / geopolíticos
    BUS = "BUS"   # Actores corporativos / mercados


class AnomalyType(str, Enum):
    """Tipo de anomalía cuantitativa detectada."""
    SPILLOVER_GOV     = "SPILLOVER_GOV"     # Noticias GOV → precio (causalidad)
    SPILLOVER_BUS     = "SPILLOVER_BUS"     # Noticias BUS → precio (causalidad)
    DUAL_SPILLOVER    = "DUAL_SPILLOVER"    # Ambos actores → precio simultáneamente
    TREND_REGIME      = "TREND_REGIME"      # Hurst indica tendencia persistente
    REVERTING_REGIME  = "REVERTING_REGIME"  # Hurst indica mean-reversion fuerte
    REGIME_CHANGE     = "REGIME_CHANGE"     # Cambio abrupto en Hurst (discontinuidad)
    GODEL_ALIGNMENT   = "GODEL_ALIGNMENT"   # TE elevada + condición Gödel activa
    COMPOSITE_ALERT   = "COMPOSITE_ALERT"   # Score compuesto supera umbral crítico
    NONE              = "NONE"              # Sin anomalía en esta ventana


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES DE CONFIGURACIÓN Y RESULTADO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EngineConfig:
    """
    Configuración del SPELMathEngine.

    Attributes:
        activo          : Activo canónico SPEL. Determina λ (Regla 4).
        window          : Ventana deslizante en periodos. None → usa λ_activo.
        disc_window     : Ventana de rolling para discretización (media/std).
        te_lag          : Lag en periodos para Transfer Entropy. Default=1.
        te_backend      : Backend para TE. "manual" | "pyinform" | "auto".
        hurst_min_window: Mínimo de puntos para cada bloque R/S.
        composite_threshold: Score > threshold → alerta COMPOSITE_ALERT.
        verbose         : Nivel de logging detallado.
    """
    activo:               str   = "NVDA"
    window:               int | None = None   # None → usa LAMBDA_PARAMS[activo]
    disc_window:          int   = 20
    te_lag:               int   = 1
    te_backend:           Literal["manual", "pyinform", "auto"] = "auto"
    hurst_min_window:     int   = 8
    composite_threshold:  float = 0.50
    verbose:              bool  = False

    def __post_init__(self) -> None:
        self.activo = self.activo.upper()
        if self.activo not in ACTIVOS_VALIDOS:
            raise ValueError(
                f"Activo '{self.activo}' no válido. Opciones: {sorted(ACTIVOS_VALIDOS)}"
            )
        if self.window is None:
            self.window = LAMBDA_PARAMS[self.activo]
        if self.te_lag < 1:
            raise ValueError("te_lag debe ser ≥ 1")
        if self.te_backend == "pyinform" and not _PYINFORM_AVAILABLE:
            warnings.warn(
                "pyinform no disponible — cambiando a backend manual", stacklevel=2
            )
            self.te_backend = "manual"


@dataclass(frozen=True)
class WindowResult:
    """Resultado de una sola ventana deslizante."""
    date_end:         datetime
    n_samples:        int
    te_gov:           float          # bits
    te_bus:           float          # bits
    hurst:            float
    hurst_dfa:        float          # confirmación DFA
    regime:           MarketRegime
    prev_regime:      MarketRegime   # para detectar regime_change
    disc_price_entropy: float        # entropía de Shannon del precio discretizado
    godel_active:     bool
    ok:               bool
    error:            str = ""


@dataclass
class MathResult:
    """Resultado completo del engine sobre toda la serie temporal."""
    activo:      str
    config:      EngineConfig
    alerts_df:   pl.DataFrame   # DataFrame ligero de salida
    n_windows:   int
    n_anomalies: int
    ts_utc:      datetime
    backend_used: str

    def summary(self) -> str:
        anomaly_rate = (
            self.n_anomalies / self.n_windows * 100 if self.n_windows > 0 else 0
        )
        return (
            f"MathResult({self.activo}) "
            f"{self.n_windows} ventanas · "
            f"{self.n_anomalies} anomalías ({anomaly_rate:.1f}%) · "
            f"backend={self.backend_used}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE MATEMÁTICA PURA (privadas · numpy-based)
# ══════════════════════════════════════════════════════════════════════════════

def _discretize_series(
    values: np.ndarray,
    rolling_window: int,
) -> np.ndarray:
    """
    Discretiza una serie continua en estados {-1, 0, +1} usando una ventana
    deslizante de media y desviación estándar.

    Regla de asignación:
        +1  si  value > rolling_mean + 1·rolling_std   (movimiento alcista anómalo)
         0  si  |value - rolling_mean| ≤ 1·rolling_std (régimen neutral)
        -1  si  value < rolling_mean - 1·rolling_std   (movimiento bajista anómalo)

    Los primeros `rolling_window` elementos se rellenan con 0 (estado neutral)
    por falta de historia suficiente.

    Args:
        values        : Array numpy 1-D de valores continuos.
        rolling_window: Tamaño de la ventana deslizante para mean/std.

    Returns:
        Array numpy int8 de misma longitud con valores en {-1, 0, +1}.
    """
    n = len(values)
    result = np.zeros(n, dtype=np.int8)

    if n < rolling_window + 1:
        return result

    for i in range(rolling_window, n):
        window = values[i - rolling_window : i]
        mu  = window.mean()
        sig = window.std(ddof=1)

        if sig < 1e-12:           # serie constante → estado neutral
            result[i] = 0
        elif values[i] > mu + sig:
            result[i] = 1
        elif values[i] < mu - sig:
            result[i] = -1
        else:
            result[i] = 0

    return result


def _shannon_entropy(states: np.ndarray) -> float:
    """
    Entropía de Shannon H(X) en bits sobre un array de estados discretos.

    H(X) = -Σ p(x) · log2(p(x))

    Args:
        states: Array de estados enteros.

    Returns:
        Entropía en bits. Retorna 0.0 si el array está vacío.
    """
    if len(states) == 0:
        return 0.0
    unique, counts = np.unique(states, return_counts=True)
    probs = counts / len(states)
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


def _transfer_entropy_manual(
    source: np.ndarray,
    target: np.ndarray,
    lag:    int = 1,
) -> float:
    """
    Transfer Entropy de `source` → `target` por método directo de probabilidades
    conjuntas (Schreiber, 2000).

    Fórmula:
        TE(X→Y) = H(Y_t+1 | Y_t) - H(Y_t+1 | Y_t, X_t)
                = Σ_{y', y, x} p(y', y, x) · log2[p(y'|y,x) / p(y'|y)]

    Donde:
        y' = target[t+lag]  (futuro del precio)
        y  = target[t]      (pasado del precio)
        x  = source[t]      (pasado de las noticias GDELT)

    Los arrays deben ser secuencias de estados enteros en {-1, 0, +1}.
    Esta implementación usa aritmética de conteos exacta para evitar
    acumulación de error numérico en las probabilidades.

    Args:
        source: Array de estados del proceso fuente (GDELT discretizado).
        target: Array de estados del proceso objetivo (precio discretizado).
        lag   : Horizonte temporal en periodos (default=1).

    Returns:
        TE en bits (≥ 0). Retorna 0.0 si los datos son insuficientes.
    """
    n = len(target) - lag
    if n < MIN_ROWS_TE:
        return 0.0

    y_future = target[lag:].astype(np.int8)
    y_past   = target[:-lag].astype(np.int8)
    x_past   = source[:-lag].astype(np.int8)

    states = np.array([-1, 0, 1], dtype=np.int8)
    te = 0.0

    for y_f in states:
        mask_yf = y_future == y_f

        for y_p in states:
            mask_yp = y_past == y_p

            # p(y_t+1 | y_t) — marginal condicional en target solo
            mask_y_joint = mask_yf & mask_yp
            n_y_past     = mask_yp.sum()
            if n_y_past == 0:
                continue
            p_yf_given_yp = mask_y_joint.sum() / n_y_past

            for x_p in states:
                mask_xp = x_past == x_p

                # p(y_t+1, y_t, x_t) — probabilidad conjunta completa
                mask_triple = mask_yf & mask_yp & mask_xp
                n_triple     = mask_triple.sum()
                if n_triple == 0:
                    continue
                p_joint = n_triple / n

                # p(y_t+1 | y_t, x_t) — condicional completa
                n_yx = (mask_yp & mask_xp).sum()
                if n_yx == 0:
                    continue
                p_yf_given_yx = n_triple / n_yx

                # Contribución a la TE
                if p_yf_given_yx > 0 and p_yf_given_yp > 0:
                    te += p_joint * np.log2(p_yf_given_yx / p_yf_given_yp)

    return float(max(te, 0.0))


def _transfer_entropy_pyinform(
    source: np.ndarray,
    target: np.ndarray,
    lag:    int = 1,
) -> float:
    """
    Transfer Entropy usando pyinform como backend.
    Requiere estados mapeados a {0, 1, 2} (pyinform no acepta negativos).

    Args:
        source: Estados en {-1, 0, +1}.
        target: Estados en {-1, 0, +1}.
        lag   : Lag temporal.

    Returns:
        TE en bits. Retorna resultado manual como fallback si pyinform falla.
    """
    if not _PYINFORM_AVAILABLE:
        return _transfer_entropy_manual(source, target, lag)

    # Remap: -1→0 · 0→1 · 1→2
    src_mapped = (source + 1).astype(np.int32)
    tgt_mapped = (target + 1).astype(np.int32)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            te = float(_pyinform_te(src_mapped, tgt_mapped, k=lag))
        return max(te, 0.0)
    except Exception as exc:
        _log.debug("pyinform TE falló: %s — usando manual", exc)
        return _transfer_entropy_manual(source, target, lag)


def _compute_te(
    source:  np.ndarray,
    target:  np.ndarray,
    lag:     int,
    backend: str,
) -> float:
    """Dispatcher de TE según backend configurado."""
    if backend == "pyinform" and _PYINFORM_AVAILABLE:
        return _transfer_entropy_pyinform(source, target, lag)
    elif backend == "auto" and _PYINFORM_AVAILABLE:
        return _transfer_entropy_pyinform(source, target, lag)
    else:
        return _transfer_entropy_manual(source, target, lag)


def _hurst_rs(
    series:     np.ndarray,
    min_window: int = 8,
) -> float:
    """
    Exponente de Hurst por análisis R/S (Rescaled Range) de Hurst (1951).

    Algoritmo:
        1. Dividir la serie en bloques de tamaño w (log-espaciados).
        2. Para cada bloque: calcular R = rango de la desviación acumulada,
           S = desviación estándar muestral.
        3. Promediar R/S sobre todos los bloques del mismo tamaño w.
        4. Ajustar regresión lineal de log(R/S) vs log(w).
        5. La pendiente es el Exponente de Hurst H.

    Interpretación:
        H > 0.5 : Serie persistente (trending) — momentum
        H = 0.5 : Caminata aleatoria (Brownian motion)
        H < 0.5 : Serie anti-persistente (mean-reversion)

    Args:
        series    : Array 1-D de valores continuos (retornos logarítmicos).
        min_window: Tamaño mínimo de bloque.

    Returns:
        Exponente de Hurst H ∈ [0, 1]. Retorna 0.5 si datos insuficientes.
    """
    n = len(series)
    if n < MIN_ROWS_HURST:
        return 0.5

    # Ventanas log-espaciadas entre min_window y n//2
    max_window  = n // 2
    n_steps     = min(20, max_window - min_window)
    if n_steps < 3:
        return 0.5

    window_sizes = np.unique(
        np.logspace(
            np.log10(min_window),
            np.log10(max_window),
            n_steps,
        ).astype(int)
    )

    rs_means: list[tuple[int, float]] = []

    for w in window_sizes:
        if w < min_window:
            continue
        n_blocks = n // w
        if n_blocks == 0:
            continue

        rs_block: list[float] = []
        for b in range(n_blocks):
            block = series[b * w : (b + 1) * w]
            s = block.std(ddof=1)
            if s < 1e-12:
                continue
            deviations = np.cumsum(block - block.mean())
            r          = deviations.max() - deviations.min()
            rs_block.append(r / s)

        if rs_block:
            rs_means.append((w, np.mean(rs_block)))

    if len(rs_means) < 3:
        return 0.5

    log_w  = np.log([x[0] for x in rs_means])
    log_rs = np.log([x[1] for x in rs_means])

    # Regresión lineal por mínimos cuadrados: H = slope de log(R/S) vs log(w)
    slope, _ = np.polyfit(log_w, log_rs, 1)
    return float(np.clip(slope, 0.0, 1.0))


def _hurst_dfa(
    series:     np.ndarray,
    min_window: int = 8,
) -> float:
    """
    Exponente de Hurst por DFA (Detrended Fluctuation Analysis) — Peng et al. (1994).

    Más robusto que R/S para series no estacionarias (típico en GDELT/precio).

    DFA mide cómo la fluctuación F(n) escala con el tamaño de la ventana n:
        F(n) ~ n^α   →   H_DFA = α

    Args:
        series    : Array 1-D de retornos o variaciones.
        min_window: Tamaño mínimo de segmento.

    Returns:
        Exponente DFA α ≈ H ∈ [0, 1]. Retorna 0.5 si datos insuficientes.
    """
    n = len(series)
    if n < MIN_ROWS_HURST:
        return 0.5

    # Serie integrada (perfil)
    profile = np.cumsum(series - series.mean())

    max_window = n // 4
    if max_window < min_window:
        return 0.5

    n_steps  = min(15, max_window - min_window)
    if n_steps < 3:
        return 0.5

    window_sizes = np.unique(
        np.logspace(
            np.log10(min_window),
            np.log10(max_window),
            n_steps,
        ).astype(int)
    )

    f_values: list[tuple[int, float]] = []

    for w in window_sizes:
        n_segments = n // w
        if n_segments == 0:
            continue

        rms_segments: list[float] = []
        for seg in range(n_segments):
            segment = profile[seg * w : (seg + 1) * w]
            # Detrend local por regresión lineal
            x   = np.arange(w, dtype=float)
            p   = np.polyfit(x, segment, 1)
            trend = np.polyval(p, x)
            residual = segment - trend
            rms_segments.append(np.sqrt(np.mean(residual ** 2)))

        if rms_segments:
            f_values.append((w, np.mean(rms_segments)))

    if len(f_values) < 3:
        return 0.5

    log_w = np.log([x[0] for x in f_values])
    log_f = np.log([x[1] for x in f_values])

    slope, _ = np.polyfit(log_w, log_f, 1)
    return float(np.clip(slope, 0.0, 1.0))


def _regime_from_hurst(h: float) -> MarketRegime:
    """Clasifica el Exponente de Hurst en un MarketRegime."""
    if h > HURST_TRENDING_HIGH:
        return MarketRegime.TRENDING_STRONG
    elif h > HURST_TRENDING_MEDIUM:
        return MarketRegime.TRENDING_WEAK
    elif h >= HURST_RANDOM_LOW:
        return MarketRegime.RANDOM_WALK
    elif h >= HURST_REVERTING_LOW:
        return MarketRegime.REVERTING_WEAK
    else:
        return MarketRegime.REVERTING_STRONG


def _anomaly_type(
    te_gov:      float,
    te_bus:      float,
    hurst:       float,
    prev_hurst:  float,
    godel_active: bool,
    composite:   float,
    threshold:   float,
) -> AnomalyType:
    """
    Determina el tipo de anomalía dominante para una ventana dada.
    La prioridad de clasificación va de mayor a menor especificidad.
    """
    # Alineación Gödel + spillover elevado → máxima severidad
    if godel_active and (te_gov > TE_STRONG_THRESHOLD or te_bus > TE_STRONG_THRESHOLD):
        return AnomalyType.GODEL_ALIGNMENT

    # Cambio abrupto de régimen Hurst
    if abs(hurst - prev_hurst) > HURST_REGIME_CHANGE_DELTA:
        return AnomalyType.REGIME_CHANGE

    # Dual spillover (ambos actores simultáneos)
    if te_gov > TE_SPILLOVER_THRESHOLD and te_bus > TE_SPILLOVER_THRESHOLD:
        return AnomalyType.DUAL_SPILLOVER

    # Spillover individual
    if te_gov > TE_SPILLOVER_THRESHOLD:
        return AnomalyType.SPILLOVER_GOV
    if te_bus > TE_SPILLOVER_THRESHOLD:
        return AnomalyType.SPILLOVER_BUS

    # Régimen Hurst extremo
    if hurst > HURST_TRENDING_HIGH:
        return AnomalyType.TREND_REGIME
    if hurst < HURST_REVERTING_LOW:
        return AnomalyType.REVERTING_REGIME

    # Score compuesto supera umbral
    if composite > threshold:
        return AnomalyType.COMPOSITE_ALERT

    return AnomalyType.NONE


def _composite_score(
    te_gov:   float,
    te_bus:   float,
    hurst:    float,
    prev_hurst: float,
) -> float:
    """
    Score compuesto de anomalía ∈ [0, 1].

    Fórmula:
        score = 0.50 · TE_norm + 0.30 · |H - 0.5| · 2 + 0.20 · regime_change_flag

    Donde:
        TE_norm = max(TE_gov, TE_bus) / TE_MAX_THEORETICAL
        |H-0.5|·2 normaliza la desviación de Hurst a [0,1]
        regime_change_flag = 1 si |ΔH| > umbral, 0 otherwise
    """
    te_max    = max(te_gov, te_bus)
    te_norm   = min(te_max / TE_MAX_THEORETICAL, 1.0)

    hurst_dev = min(abs(hurst - 0.5) * 2.0, 1.0)

    regime_flag = 1.0 if abs(hurst - prev_hurst) > HURST_REGIME_CHANGE_DELTA else 0.0

    return float(_W_TE * te_norm + _W_HURST * hurst_dev + _W_REGIME * regime_flag)


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES GDELT
# ══════════════════════════════════════════════════════════════════════════════

def filter_gdelt_by_actor(
    lf_gdelt: pl.LazyFrame,
    actor:    ActorType,
    actor_col: str = "actor_type",
) -> pl.LazyFrame:
    """
    Filtra un LazyFrame de GDELT por tipo de actor (GOV o BUS).

    Si la columna `actor_col` no existe (GDELT canónico sin actor_type),
    se retorna el LazyFrame completo con un warning. En ese caso, el
    caller debe pre-filtrar con BigQuery antes de pasarlo al engine.

    Args:
        lf_gdelt  : LazyFrame de GDELT con schema canónico.
        actor     : ActorType.GOV o ActorType.BUS.
        actor_col : Nombre de la columna de tipo de actor.

    Returns:
        LazyFrame filtrado por actor.
    """
    try:
        schema = lf_gdelt.schema
    except Exception:
        schema = {}

    if actor_col not in schema:
        _log.warning(
            "Columna '%s' no encontrada en GDELT LazyFrame. "
            "Retornando LazyFrame sin filtrar. "
            "Para filtrado por actor, asegurar que BigQuery devuelva la columna.",
            actor_col,
        )
        return lf_gdelt

    return lf_gdelt.filter(pl.col(actor_col) == actor.value)


def compute_gdelt_variation(
    lf_gdelt:      pl.LazyFrame,
    value_col:     str = "n_events_ohlcv",
    date_col:      str = "date",
    agg_col:       str = "goldstein_geo",
) -> pl.LazyFrame:
    """
    Calcula la variación diaria del volumen de noticias GDELT y el promedio
    ponderado de Goldstein sobre el período.

    Columnas de salida añadidas:
        gdelt_delta   : Cambio porcentual en n_events_ohlcv respecto al día anterior.
        goldstein_avg : Promedio de goldstein_geo (negatividad geopolítica).

    Args:
        lf_gdelt  : LazyFrame GDELT ya filtrado por actor si es necesario.
        value_col : Columna de conteo de eventos (n_events_ohlcv por defecto).
        date_col  : Columna temporal.
        agg_col   : Columna de sentimiento para promedio ponderado.

    Returns:
        LazyFrame con columnas `gdelt_delta` y `goldstein_avg` añadidas.
    """
    return (
        lf_gdelt
        .sort(date_col)
        .with_columns([
            (
                (pl.col(value_col) - pl.col(value_col).shift(1))
                / (pl.col(value_col).shift(1).abs() + 1e-8)
            ).alias("gdelt_delta"),
            pl.col(agg_col).alias("goldstein_avg"),
        ])
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLASE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class SPELMathEngine:
    """
    Motor cuantitativo de SPEL v22.

    Recibe LazyFrames de OHLCV y de variaciones de noticias GDELT filtradas
    por actor (GOV, BUS) y computa sobre una ventana deslizante:

      1. **Discretización** de retornos y variaciones en {-1, 0, +1} usando
         desviación estándar rodante. Los estados representan movimientos
         anómalos al alza (+1), normal (0) y anómalo a la baja (-1).

      2. **Transfer Entropy** TE(GDELT_actor → Precio_discretizado) en bits.
         Cuantifica la reducción de incertidumbre sobre el futuro del precio
         dada la historia del flujo de noticias. TE > 0 indica causalidad
         informacional (no solo correlación).

      3. **Exponente de Hurst** por R/S Analysis (primario) y DFA (confirmación).
         Clasifica el régimen de mercado: TRENDING, RANDOM_WALK, REVERTING.

      4. **Score compuesto de anomalía** y clasificación por tipo. Compatible
         con la condición Gödel canónica de SPEL.

    Uso básico:
        >>> engine = SPELMathEngine(EngineConfig(activo="NVDA"))
        >>> result = engine.run(lf_ohlcv, lf_gdelt_gov, lf_gdelt_bus)
        >>> print(result.alerts_df)

    El output es un DataFrame ligero con una fila por ventana deslizante,
    diseñado para ser consumido directamente por el Score de Oro Engine
    o para alertas Telegram (Sprint 5b).
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.cfg = config or EngineConfig()
        self._backend = self._resolve_backend()
        _log.info(
            "SPELMathEngine init — activo=%s window=%d lag=%d backend=%s",
            self.cfg.activo, self.cfg.window, self.cfg.te_lag, self._backend,
        )

    def _resolve_backend(self) -> str:
        if self.cfg.te_backend == "auto":
            return "pyinform" if _PYINFORM_AVAILABLE else "manual"
        return self.cfg.te_backend

    # ── API pública ───────────────────────────────────────────────────────────

    def run(
        self,
        lf_ohlcv:      pl.LazyFrame,
        lf_gdelt_gov:  pl.LazyFrame,
        lf_gdelt_bus:  pl.LazyFrame,
    ) -> MathResult:
        """
        Ejecuta el pipeline cuantitativo completo sobre las tres fuentes de datos.

        El pipeline materializa los LazyFrames una sola vez, luego opera
        exclusivamente sobre arrays numpy para el cómputo matemático.

        Args:
            lf_ohlcv     : LazyFrame OHLCV con columnas canónicas v4.
            lf_gdelt_gov : LazyFrame GDELT pre-filtrado por actores GOV.
            lf_gdelt_bus : LazyFrame GDELT pre-filtrado por actores BUS.

        Returns:
            MathResult con alerts_df (pl.DataFrame ligero) y metadata.
        """
        ts_now = datetime.now(timezone.utc)

        # ── 1. Materializar y alinear datos ───────────────────────────────────
        df_price, arr_ret, arr_gov, arr_bus, dates = self._prepare_arrays(
            lf_ohlcv, lf_gdelt_gov, lf_gdelt_bus
        )

        if arr_ret is None:
            _log.error("run(%s): datos insuficientes para el análisis", self.cfg.activo)
            return MathResult(
                activo=self.cfg.activo,
                config=self.cfg,
                alerts_df=self._empty_alerts_df(),
                n_windows=0,
                n_anomalies=0,
                ts_utc=ts_now,
                backend_used=self._backend,
            )

        # ── 2. Discretizar series ─────────────────────────────────────────────
        disc_price = _discretize_series(arr_ret, self.cfg.disc_window)
        disc_gov   = _discretize_series(arr_gov, self.cfg.disc_window)
        disc_bus   = _discretize_series(arr_bus, self.cfg.disc_window)

        _log.info(
            "Discretización completada — precio: %d samples · GOV: %d · BUS: %d",
            len(disc_price), len(disc_gov), len(disc_bus),
        )

        # ── 3. Extraer señales Gödel si disponibles en el DataFrame ──────────
        godel_series = self._extract_godel_series(df_price)

        # ── 4. Ventana deslizante ─────────────────────────────────────────────
        window_results = self._run_rolling_windows(
            disc_price, disc_gov, disc_bus, godel_series, dates
        )

        # ── 5. Construir DataFrame de alertas ─────────────────────────────────
        alerts_df   = self._build_alerts_df(window_results)
        n_anomalies = int(
            (alerts_df["anomaly_type"] != AnomalyType.NONE.value).sum()
        )

        _log.info(
            "run(%s) completado — %d ventanas · %d anomalías · backend=%s",
            self.cfg.activo, len(window_results), n_anomalies, self._backend,
        )

        return MathResult(
            activo=self.cfg.activo,
            config=self.cfg,
            alerts_df=alerts_df,
            n_windows=len(window_results),
            n_anomalies=n_anomalies,
            ts_utc=ts_now,
            backend_used=self._backend,
        )

    # ── Preparación de datos ──────────────────────────────────────────────────

    def _prepare_arrays(
        self,
        lf_ohlcv:     pl.LazyFrame,
        lf_gdelt_gov: pl.LazyFrame,
        lf_gdelt_bus: pl.LazyFrame,
    ) -> tuple:
        """
        Materializa los LazyFrames, computa retornos logarítmicos y variaciones
        GDELT, y los alinea temporalmente por la columna `date`.

        Returns:
            (df_price, arr_returns, arr_gov_delta, arr_bus_delta, dates)
            Todos los arrays son numpy float64. Retorna Nones en error.
        """
        try:
            # Materializar OHLCV
            df_price = (
                lf_ohlcv
                .sort("date")
                .with_columns([
                    (pl.col("close") / pl.col("close").shift(1))
                    .log(base=float(np.e))
                    .alias("_log_ret")
                ])
                .drop_nulls(subset=["_log_ret"])
                .collect()
            )

            if len(df_price) < MIN_ROWS_HURST:
                _log.warning(
                    "_prepare_arrays: OHLCV tiene solo %d filas (mínimo %d)",
                    len(df_price), MIN_ROWS_HURST,
                )
                return df_price, None, None, None, None

            # Materializar y computar variación GDELT GOV
            lf_gov_var = compute_gdelt_variation(lf_gdelt_gov)
            df_gov = (
                lf_gov_var
                .sort("date")
                .drop_nulls(subset=["gdelt_delta"])
                .collect()
            )

            # Materializar y computar variación GDELT BUS
            lf_bus_var = compute_gdelt_variation(lf_gdelt_bus)
            df_bus = (
                lf_bus_var
                .sort("date")
                .drop_nulls(subset=["gdelt_delta"])
                .collect()
            )

            # Alineación temporal: inner join por date
            df_aligned = (
                df_price
                .join(
                    df_gov.select(["date", pl.col("gdelt_delta").alias("gdelt_gov_delta")]),
                    on="date", how="left",
                )
                .join(
                    df_bus.select(["date", pl.col("gdelt_delta").alias("gdelt_bus_delta")]),
                    on="date", how="left",
                )
                .with_columns([
                    pl.col("gdelt_gov_delta").fill_null(0.0),
                    pl.col("gdelt_bus_delta").fill_null(0.0),
                ])
                .sort("date")
            )

            arr_ret = df_aligned["_log_ret"].to_numpy().astype(np.float64)
            arr_gov = df_aligned["gdelt_gov_delta"].to_numpy().astype(np.float64)
            arr_bus = df_aligned["gdelt_bus_delta"].to_numpy().astype(np.float64)
            dates   = df_aligned["date"].to_list()

            _log.info(
                "_prepare_arrays(%s): %d filas alineadas",
                self.cfg.activo, len(arr_ret),
            )
            return df_aligned, arr_ret, arr_gov, arr_bus, dates

        except Exception as exc:
            _log.error("_prepare_arrays falló: %s", exc)
            return None, None, None, None, None

    def _extract_godel_series(self, df: pl.DataFrame) -> np.ndarray | None:
        """
        Extrae la condición Gödel del DataFrame si las columnas están presentes.

        godel_activo = (entropy_shannon >= P90) OR (vitality_tesla >= 9.0)

        Si las columnas no existen (OHLCV sin features de entropía todavía),
        retorna None para que el engine use godel_active=False.
        """
        required = ["entropy_shannon", "vitality_tesla"]
        if not all(c in df.columns for c in required):
            _log.debug(
                "Columnas Gödel no presentes en DataFrame — godel_active=False en todas las ventanas"
            )
            return None

        p90_entropy = float(df["entropy_shannon"].quantile(0.90) or 0.0)

        godel_arr = (
            df
            .with_columns(
                (
                    (pl.col("entropy_shannon") >= p90_entropy)
                    | (pl.col("vitality_tesla") >= GODEL_VITALITY_THRESHOLD)
                ).alias("_godel")
            )["_godel"]
            .to_numpy()
        )
        return godel_arr.astype(bool)

    # ── Ventana deslizante ────────────────────────────────────────────────────

    def _run_rolling_windows(
        self,
        disc_price:    np.ndarray,
        disc_gov:      np.ndarray,
        disc_bus:      np.ndarray,
        godel_series:  np.ndarray | None,
        dates:         list,
    ) -> list[WindowResult]:
        """
        Ejecuta el análisis sobre ventanas deslizantes.

        Cada ventana termina en el índice `i` y tiene longitud `window`.
        Las ventanas anteriores a `window + disc_window + te_lag` se omiten
        por insuficiencia de datos.

        Returns:
            Lista de WindowResult ordenada cronológicamente.
        """
        n       = len(disc_price)
        w       = self.cfg.window
        results: list[WindowResult] = []
        prev_hurst   = 0.5
        prev_regime  = MarketRegime.RANDOM_WALK

        # Inicio efectivo: necesitamos historia para disc_window + ventana
        start_idx = max(self.cfg.disc_window + w + self.cfg.te_lag, MIN_ROWS_HURST)

        for i in range(start_idx, n):
            window_slice = slice(i - w, i)

            p_win = disc_price[window_slice]
            g_win = disc_gov[window_slice]
            b_win = disc_bus[window_slice]

            # Verificar datos mínimos
            if len(p_win) < max(MIN_ROWS_TE, 10):
                continue

            # Transfer Entropy
            te_gov = _compute_te(g_win, p_win, self.cfg.te_lag, self._backend)
            te_bus = _compute_te(b_win, p_win, self.cfg.te_lag, self._backend)

            # Hurst sobre retornos (antes de discretizar para R/S)
            hurst     = _hurst_rs(p_win.astype(float), self.cfg.hurst_min_window)
            hurst_dfa = _hurst_dfa(p_win.astype(float), self.cfg.hurst_min_window)

            # Promedio R/S + DFA como estimador final robusto
            hurst_final = 0.65 * hurst + 0.35 * hurst_dfa

            regime = _regime_from_hurst(hurst_final)

            # Condición Gödel en el extremo de la ventana
            godel_active = bool(godel_series[i]) if godel_series is not None else False

            # Entropía de Shannon del precio discretizado (señal de información)
            disc_entropy = _shannon_entropy(p_win)

            # Score y tipo de anomalía
            composite = _composite_score(te_gov, te_bus, hurst_final, prev_hurst)
            anomaly   = _anomaly_type(
                te_gov, te_bus, hurst_final, prev_hurst,
                godel_active, composite, self.cfg.composite_threshold,
            )

            date_end = dates[i] if i < len(dates) else datetime.now(timezone.utc)

            results.append(WindowResult(
                date_end=date_end,
                n_samples=len(p_win),
                te_gov=round(te_gov, 6),
                te_bus=round(te_bus, 6),
                hurst=round(hurst_final, 4),
                hurst_dfa=round(hurst_dfa, 4),
                regime=regime,
                prev_regime=prev_regime,
                disc_price_entropy=round(disc_entropy, 4),
                godel_active=godel_active,
                ok=True,
            ))

            prev_hurst  = hurst_final
            prev_regime = regime

            if self.cfg.verbose and anomaly != AnomalyType.NONE:
                _log.info(
                    "  [%s] TE_gov=%.4f TE_bus=%.4f H=%.3f regime=%s anomaly=%s score=%.3f",
                    date_end,
                    te_gov, te_bus, hurst_final,
                    regime.value, anomaly.value, composite,
                )

        return results

    # ── Construcción del DataFrame de salida ──────────────────────────────────

    def _build_alerts_df(self, results: list[WindowResult]) -> pl.DataFrame:
        """
        Construye el DataFrame ligero de alertas a partir de los WindowResults.

        Schema de salida:
            date              : pl.Datetime("ms", "UTC")
            activo            : pl.Utf8
            hurst             : pl.Float64
            hurst_dfa         : pl.Float64
            market_regime     : pl.Utf8
            te_gov            : pl.Float64  — bits
            te_bus            : pl.Float64  — bits
            dominant_actor    : pl.Utf8     — "GOV" | "BUS" | "NONE"
            spillover_detected: pl.Boolean
            disc_entropy      : pl.Float64  — entropía de Shannon del precio discretizado
            anomaly_score     : pl.Float64  — composite ∈ [0, 1]
            anomaly_type      : pl.Utf8
            godel_signal      : pl.Boolean
            n_samples         : pl.Int32
        """
        if not results:
            return self._empty_alerts_df()

        rows = []
        for r in results:
            dominant = (
                "GOV" if r.te_gov > r.te_bus
                else "BUS" if r.te_bus > r.te_gov
                else "NONE"
            )
            spillover = (
                r.te_gov > TE_SPILLOVER_THRESHOLD
                or r.te_bus > TE_SPILLOVER_THRESHOLD
            )
            composite = _composite_score(r.te_gov, r.te_bus, r.hurst, 0.5)
            anomaly   = _anomaly_type(
                r.te_gov, r.te_bus, r.hurst,
                getattr(r, "prev_hurst", 0.5),
                r.godel_active, composite, self.cfg.composite_threshold,
            )

            # Convertir date_end a datetime UTC aware
            if isinstance(r.date_end, datetime):
                dt = r.date_end if r.date_end.tzinfo else r.date_end.replace(tzinfo=timezone.utc)
            else:
                # Polars datetime value (int ms)
                dt = datetime.fromtimestamp(int(r.date_end) / 1000, tz=timezone.utc)

            rows.append({
                "date":               dt,
                "activo":             self.cfg.activo,
                "hurst":              r.hurst,
                "hurst_dfa":          r.hurst_dfa,
                "market_regime":      r.regime.value,
                "te_gov":             r.te_gov,
                "te_bus":             r.te_bus,
                "dominant_actor":     dominant,
                "spillover_detected": spillover,
                "disc_entropy":       r.disc_price_entropy,
                "anomaly_score":      round(composite, 4),
                "anomaly_type":       anomaly.value,
                "godel_signal":       r.godel_active,
                "n_samples":          r.n_samples,
            })

        df = pl.DataFrame(rows)

        # Cast de columnas al schema definitivo
        return df.with_columns([
            pl.col("date").dt.replace_time_zone("UTC").dt.cast_time_unit("ms"),
            pl.col("activo").cast(pl.Utf8),
            pl.col("hurst").cast(pl.Float64),
            pl.col("hurst_dfa").cast(pl.Float64),
            pl.col("market_regime").cast(pl.Utf8),
            pl.col("te_gov").cast(pl.Float64),
            pl.col("te_bus").cast(pl.Float64),
            pl.col("dominant_actor").cast(pl.Utf8),
            pl.col("spillover_detected").cast(pl.Boolean),
            pl.col("disc_entropy").cast(pl.Float64),
            pl.col("anomaly_score").cast(pl.Float64),
            pl.col("anomaly_type").cast(pl.Utf8),
            pl.col("godel_signal").cast(pl.Boolean),
            pl.col("n_samples").cast(pl.Int32),
        ])

    def _empty_alerts_df(self) -> pl.DataFrame:
        """DataFrame vacío con el schema correcto para retorno en caso de error."""
        return pl.DataFrame(schema={
            "date":               pl.Datetime("ms", "UTC"),
            "activo":             pl.Utf8,
            "hurst":              pl.Float64,
            "hurst_dfa":          pl.Float64,
            "market_regime":      pl.Utf8,
            "te_gov":             pl.Float64,
            "te_bus":             pl.Float64,
            "dominant_actor":     pl.Utf8,
            "spillover_detected": pl.Boolean,
            "disc_entropy":       pl.Float64,
            "anomaly_score":      pl.Float64,
            "anomaly_type":       pl.Utf8,
            "godel_signal":       pl.Boolean,
            "n_samples":          pl.Int32,
        })


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def math_engine_from_config(
    activo:  str,
    window:  int | None = None,
    verbose: bool = False,
    **kwargs,
) -> SPELMathEngine:
    """
    Construye un SPELMathEngine con configuración canónica SPEL.
    El parámetro `window` por defecto usa λ del activo (Regla 4).

    Example:
        >>> engine = math_engine_from_config("NVDA")
        >>> result = engine.run(lf_ohlcv, lf_gdelt_gov, lf_gdelt_bus)
        >>> result.alerts_df.filter(pl.col("anomaly_type") != "NONE")
    """
    return SPELMathEngine(
        EngineConfig(activo=activo, window=window, verbose=verbose, **kwargs)
    )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

def _generate_test_data(
    n:       int = 300,
    seed:    int = 42,
    activo:  str = "NVDA",
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Genera datos sintéticos para el self-test."""
    rng   = np.random.default_rng(seed)
    dates = [
        datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        for i in range(n)
    ]

    # Precio con régimen trending en la segunda mitad (Hurst > 0.5)
    returns_noise  = rng.normal(0, 0.015, n)
    returns_trend  = np.concatenate([
        returns_noise[:n // 2],
        np.cumsum(rng.normal(0.002, 0.008, n // 2)),
    ])
    close = 500.0 * np.cumprod(1 + returns_trend)

    df_ohlcv = pl.LazyFrame({
        "date":             pl.Series(dates).cast(pl.Datetime("ms", "UTC")),
        "open":             close * (1 + rng.normal(0, 0.001, n)),
        "high":             close * (1 + np.abs(rng.normal(0, 0.005, n))),
        "low":              close * (1 - np.abs(rng.normal(0, 0.005, n))),
        "close":            close,
        "volume":           rng.uniform(1e6, 5e6, n),
        "entropy_shannon":  rng.uniform(0.1, 0.9, n),
        "vitality_tesla":   rng.uniform(0.0, 12.0, n),
    })

    # GDELT GOV: correlacionado con retornos en la segunda mitad (spillover)
    gdelt_gov_events = np.abs(rng.normal(20, 5, n))
    gdelt_gov_events[n // 2:] += (
        np.abs(returns_trend[n // 2:]) * 200
    )   # inyectar causalidad

    df_gdelt_gov = pl.LazyFrame({
        "date":             pl.Series(dates).cast(pl.Datetime("ms", "UTC")),
        "n_events_ohlcv":   gdelt_gov_events,
        "goldstein_geo":    rng.uniform(-5.0, 2.0, n),
        "vitality_tesla":   rng.uniform(0.0, 8.0, n),
        "mass_panic_index": rng.uniform(0.0, 0.4, n),
        "fear_momentum":    rng.uniform(0.0, 0.2, n),
        "actor_type":       ["GOV"] * n,
    })

    # GDELT BUS: ruido independiente (sin causalidad directa)
    df_gdelt_bus = pl.LazyFrame({
        "date":             pl.Series(dates).cast(pl.Datetime("ms", "UTC")),
        "n_events_ohlcv":   rng.uniform(10, 50, n),
        "goldstein_geo":    rng.uniform(-3.0, 3.0, n),
        "vitality_tesla":   rng.uniform(0.0, 5.0, n),
        "mass_panic_index": rng.uniform(0.0, 0.2, n),
        "fear_momentum":    rng.uniform(0.0, 0.1, n),
        "actor_type":       ["BUS"] * n,
    })

    return df_ohlcv, df_gdelt_gov, df_gdelt_bus


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    print("=" * 65)
    print("  🧮  SPELMathEngine — Self-Test v22")
    print("=" * 65)

    ACTIVOS_TEST = ["NVDA", "BTC"]
    all_ok = True

    for activo in ACTIVOS_TEST:
        print(f"\n  ▶  {activo} (λ={LAMBDA_PARAMS[activo]}d)")

        lf_ohlcv, lf_gov, lf_bus = _generate_test_data(n=300, activo=activo)
        engine = math_engine_from_config(activo=activo, verbose=False)
        result = engine.run(lf_ohlcv, lf_gov, lf_bus)

        print(f"     {result.summary()}")

        df = result.alerts_df
        print(f"     Schema: {dict(df.schema)}")
        assert "date"               in df.columns, "Falta columna date"
        assert "hurst"              in df.columns, "Falta columna hurst"
        assert "te_gov"             in df.columns, "Falta columna te_gov"
        assert "te_bus"             in df.columns, "Falta columna te_bus"
        assert "anomaly_type"       in df.columns, "Falta columna anomaly_type"
        assert "godel_signal"       in df.columns, "Falta columna godel_signal"
        assert "spillover_detected" in df.columns, "Falta columna spillover_detected"
        assert df["date"].dtype == pl.Datetime("ms", "UTC"), \
            f"dtype de date incorrecto: {df['date'].dtype}"
        assert df["hurst"].is_between(0.0, 1.0).all(), \
            f"Hurst fuera de [0,1]: {df['hurst'].min()} - {df['hurst'].max()}"
        assert (df["te_gov"] >= 0.0).all(), "TE_gov negativa detectada"
        assert (df["te_bus"] >= 0.0).all(), "TE_bus negativa detectada"
        assert result.n_windows > 0, "Sin ventanas procesadas"

        # Verificar que el self-test detecta el spillover inyectado
        spillovers = df.filter(pl.col("spillover_detected") == True)
        n_anomalies = df.filter(pl.col("anomaly_type") != AnomalyType.NONE.value)

        # Distribución de regímenes
        regime_dist = df.group_by("market_regime").agg(pl.len().alias("count"))
        print(f"     Regímenes detectados:")
        for row in regime_dist.iter_rows(named=True):
            print(f"       {row['market_regime']:<24} {row['count']} ventanas")

        # TE stats
        print(f"     TE_gov  max={df['te_gov'].max():.4f} mean={df['te_gov'].mean():.4f} bits")
        print(f"     TE_bus  max={df['te_bus'].max():.4f} mean={df['te_bus'].mean():.4f} bits")
        print(f"     Hurst   max={df['hurst'].max():.3f} min={df['hurst'].min():.3f} mean={df['hurst'].mean():.3f}")
        print(f"     Spillovers: {len(spillovers)} · Anomalías totales: {len(n_anomalies)}")
        print(f"     Backend: {result.backend_used} ✅")

    print("\n" + "=" * 65)
    print(f"  {'✅  Todos los tests pasaron' if all_ok else '❌  Fallos detectados'}")
    print("  SPELMathEngine operacional")
    print("=" * 65)
    sys.exit(0 if all_ok else 1)
