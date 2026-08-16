"""
core/scoring.py
================
`vitality_tesla` (cascada B->A->C), condición Gödel, `nash_frozen_7d`,
`mass_panic_index`, `entropy_fibonacci_lags` y `gold_score_bma`.
Pendiente (fear_momentum, backbone_score real / TE real -- acá son
inputs externos al gold_score, no calculados por este módulo todavía):
ver ESTADO.md.

HALLAZGOS DE ESTA SESIÓN (verificados contra fuente, no supuestos --
"Modo Investigador": diagnosticar antes de construir):

  #1 mass_panic_index tiene 2 fórmulas legacy INCOMPATIBLES, no 1:
     - spel_bulk_harvester.py + base_adapter.py (SQL): frac(GoldsteinScale
       < -5) sobre eventos GDELT individuales. Requiere ingestion a nivel
       de evento que no existe en el repo nuevo todavía.
     - spel_ingest_incremental.py: z-score de entropy_shannon vs ventana
       de 7d. Sí portable al nivel agregado en que ya trabaja este módulo.
     compute_mass_panic_index() sintetiza ambas (ver su docstring) --
     NINGUNA de las 2 tiene evidencia empírica (a diferencia de B en
     vitality_tesla, que sí la tiene). Marcado EXPERIMENTAL a propósito.

  #2 nash_frozen_7d NO mide rango de precio / ATR / iliquidez -- mide
     estabilidad del entropía GDELT (Equilibrio de Nash informacional).
     Una sesión anterior de este mismo proyecto lo había re-derivado
     (mal) como si fuera ATR-14 antes de confirmar gdelt_foundation.py
     como fuente real. El nombre legacy "nash_frozen_7d" es correcto y
     se conserva -- la corrección fue de FÓRMULA, no de nombre.
     Fórmula alternativa encontrada y NO portada (documentada, no
     descartada por mala): spel_ingest_incremental.py usa un coeficiente
     de variación (1 - std/mean) en vez de std de la serie normalizada.
     gdelt_foundation.py se prefirió por tener constantes nombradas
     (NASH_FROZEN_THRESHOLD, NASH_ROLLING_WINDOW) y estar referenciado
     internamente por su propio método de auditoría (nash_frozen_days).

  #3 fibonacci_lag es en DÍAS, no en minutos. 1 turno atrás en esta
     misma conversación se había confirmado cadencia de 1 minuto para
     este feature específico, basada en la granularidad OHLCV de Deriv
     -- esa cadencia es real para Deriv pero no aplica acá.
     gdelt_foundation.py lo dice explícito ("lags en DÍAS") y su
     ENTROPY_SCHEMA usa date (no datetime), confirmando agregación
     diaria del pipeline GDELT. La cadencia de 1m de Deriv sigue siendo
     correcta para features intradía de OHLCV -- no para este.

  #4 gold_score / BMA: SÍ es Bayesian Model Averaging real, con pesos
     "inamovibles" (Regla 13, spel_bayesian_core.py) -- una sesión
     anterior de este proyecto había propuesto renombrarlo para evitar
     llamarlo BMA, asumiendo que eran pesos heurísticos sin marco. Con
     la fuente real en mano, esa cautela no aplicaba acá: el propio
     proyecto SÍ define esto formalmente como BMA. Se sintetizó una
     sola diferencia deliberada (no un port ciego): el kill signal
     reutiliza godel_active() en vez de reimplementar el umbral fijo
     de entropía del legacy (0.42) -- ver docstring de
     compute_gold_score_bma para el razonamiento completo (Tamiz 3).

DECISIÓN QUE ESTO IMPLEMENTA (confirmada explícitamente, no inventada):
  vitality_tesla se resuelve con una cascada de degradación en 3 niveles:
    PRIMARIA   (B): tercil de n_events (conteo de eventos GDELT) en la
                     ventana provista. Es la única de las 5 variantes legacy
                     con evidencia empírica real -- es la fórmula que
                     efectivamente entrenó el checkpoint de XAU que alcanzó
                     val_dir=0.5614 (por encima del umbral de 56% que el
                     propio proyecto legacy se había fijado).
    RESPALDO 1 (A): percentil de entropy_shannon en ventana rolling. Sin
                     precedente directo en el legacy (gdelt_foundation.py
                     lo marcaba como "planeado, no implementado aquí") --
                     se diseña acá por primera vez, misma familia de dato
                     que B (ambas dependen de GDELT).
    RESPALDO 2 (C): entropy_shannon vs percentiles GLOBALES fijos
                     (p33=0.30, p66=0.70 por defecto). Red de seguridad de
                     arranque en frío -- no necesita ventana histórica,
                     funciona desde el primer dato. Formula exacta portada
                     de spel_bayesian_core.py::compute_vitality_tesla.

NOTA CONCEPTUAL (de la discusión con Altair, no un hallazgo de código):
  n_events mide cuánta COBERTURA MEDIÁTICA GDELT tiene el activo, no
  volumen de order flow ni microestructura real de mercado. La lógica de
  "poca cobertura -> consolidación, mucha cobertura -> shock/pánico" sigue
  siendo válida -- el nombre "vitality" no implica que esto sea un
  indicador de precio (tipo RVI real); es un indicador de entropía
  informacional, capturado con GDELT.

DIFERENCIA DE CONVENCIÓN ENTRE B Y A (deliberada, documentada):
  B replica el legacy exacto: la ventana INCLUYE el punto actual, y el
  percentil se calcula sobre toda la ventana (auto-referencial -- así es
  como lo hacía spel_ingest_incremental.py::compute_entropy_features).
  A no tiene precedente legacy, así que usa la convención más limpia:
  la ventana es histórica (NO incluye el punto actual), y el valor actual
  se compara contra los percentiles de esa historia. Si en algún momento
  se prefiere unificar la convención, es un cambio de una línea -- queda
  marcado acá para que no sea una inconsistencia silenciosa.

CONDICIÓN GÖDEL -- confirmada con evidencia doble, no solo una fuente:
  godel_active = (entropy_shannon >= p90_entropy) OR (vitality_tesla == 9)
  Confirmado en godel_bound.py (con test empírico contra el crash de
  COVID-19, marzo 2020) Y en la resolución cerrada del Bug #35/#36 en el
  historial de sesiones legacy ("Opción A elegida: entropy >= P90 OR
  vitality==9 es canónica para todos los assets").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ─── Excepciones tipadas (Tamiz 4: ejecución atómica) ──────────────────────

class ScoringError(Exception):
    """Error base del módulo de scoring. Nunca se lanza directamente."""


class InvalidThresholdError(ScoringError):
    """global_p33 / global_p66 no forman un rango válido (p33 debe ser < p66)."""


# ─── vitality_tesla ─────────────────────────────────────────────────────────

#: Igual que el legacy: hacen falta al menos 3 puntos para que un tercil/
#: percentil tenga sentido. Con menos de 3, cualquier percentil es ruido.
MIN_WINDOW_FOR_PERCENTILE = 3

#: Defaults de spel_bayesian_core.py (godel_thresholds_v2.json, cuando el
#: archivo no existe). Se pueden sobreescribir una vez haya calibración
#: real para EURUSD -- ver Decisión de fuente CORE en SPEL_PERSISTENCE_STATE.md.
DEFAULT_GLOBAL_P33 = 0.30
DEFAULT_GLOBAL_P66 = 0.70


class VitalityTier(str, Enum):
    """Qué nivel de la cascada produjo el valor -- para auditoría y para
    que la compuerta de luz verde sepa si el activo está operando con la
    fuente primaria o ya degradado."""
    PRIMARY_N_EVENTS = "primary_n_events"                    # B
    FALLBACK_ENTROPY_ROLLING = "fallback_entropy_rolling"    # A
    FALLBACK_GLOBAL_THRESHOLDS = "fallback_global_thresholds"  # C


@dataclass(frozen=True)
class VitalityResult:
    """Resultado de compute_vitality_tesla -- nunca se devuelve un int
    pelado, porque saber CUÁL nivel de la cascada disparó es información
    operativa real (si todo cae siempre a C, algo está mal con el feed
    de GDELT, y hay que saberlo sin tener que inferirlo)."""
    value: int                  # 3, 6, o 9 -- nunca otro valor
    tier_used: VitalityTier
    degraded: bool               # False solo si tier_used == PRIMARY_N_EVENTS


def compute_vitality_tesla(
    n_events_window: Sequence[float] | None,
    entropy_window: Sequence[float] | None,
    current_entropy: float,
    *,
    global_p33: float = DEFAULT_GLOBAL_P33,
    global_p66: float = DEFAULT_GLOBAL_P66,
) -> VitalityResult:
    """
    Cascada de 3 niveles para vitality_tesla. Ver docstring del módulo
    para la justificación completa de cada nivel.

    Args:
        n_events_window: ventana de conteo de eventos GDELT, INCLUYENDO
            el punto actual como último elemento (igual que el legacy).
            None o con menos de MIN_WINDOW_FOR_PERCENTILE puntos -> cae a A.
        entropy_window: ventana HISTÓRICA de entropy_shannon, sin incluir
            el punto actual. None o insuficiente -> cae a C.
        current_entropy: entropy_shannon del punto actual -- siempre
            requerido, es lo único que necesita el nivel C (cold-start).
        global_p33 / global_p66: percentiles globales fijos para el nivel C.

    Raises:
        InvalidThresholdError: si global_p33 >= global_p66.
    """
    if global_p33 >= global_p66:
        raise InvalidThresholdError(
            f"global_p33 ({global_p33}) debe ser estrictamente menor que "
            f"global_p66 ({global_p66})."
        )

    # PRIMARIA (B) -- tercil de n_events, ventana auto-referencial (incluye actual)
    if n_events_window is not None and len(n_events_window) >= MIN_WINDOW_FOR_PERCENTILE:
        current_n = n_events_window[-1]
        p33 = float(np.percentile(n_events_window, 33))
        p66 = float(np.percentile(n_events_window, 66))
        value = 3 if current_n <= p33 else (6 if current_n <= p66 else 9)
        return VitalityResult(value=value, tier_used=VitalityTier.PRIMARY_N_EVENTS, degraded=False)

    logger.warning(
        "vitality_tesla: n_events_window insuficiente (%s puntos, hacen falta %d) -- "
        "degradando a RESPALDO 1 (entropía rolling).",
        0 if n_events_window is None else len(n_events_window),
        MIN_WINDOW_FOR_PERCENTILE,
    )

    # RESPALDO 1 (A) -- percentil de entropía, ventana histórica (sin el actual)
    if entropy_window is not None and len(entropy_window) >= MIN_WINDOW_FOR_PERCENTILE:
        p33 = float(np.percentile(entropy_window, 33))
        p66 = float(np.percentile(entropy_window, 66))
        value = 3 if current_entropy <= p33 else (6 if current_entropy <= p66 else 9)
        return VitalityResult(value=value, tier_used=VitalityTier.FALLBACK_ENTROPY_ROLLING, degraded=True)

    logger.warning(
        "vitality_tesla: entropy_window también insuficiente (%s puntos) -- "
        "degradando a RESPALDO 2 (percentiles globales, arranque en frío).",
        0 if entropy_window is None else len(entropy_window),
    )

    # RESPALDO 2 (C) -- percentiles globales fijos, siempre disponible
    value = 3 if current_entropy < global_p33 else (6 if current_entropy < global_p66 else 9)
    return VitalityResult(value=value, tier_used=VitalityTier.FALLBACK_GLOBAL_THRESHOLDS, degraded=True)


# ─── Condición Gödel ────────────────────────────────────────────────────────

def godel_active(entropy_shannon: float, p90_entropy: float, vitality_tesla: int) -> bool:
    """
    Condición de activación Gödel -- régimen de alta entropía / anomalía.

        godel_active = (entropy_shannon >= p90_entropy) OR (vitality_tesla == 9)

    Confirmada en godel_bound.py (con test empírico contra el crash de
    COVID-19, marzo 2020) y en la resolución cerrada del Bug #35/#36 del
    historial legacy: "Opción A elegida: entropy >= P90 OR vitality==9 es
    canónica para todos los assets."

    p90_entropy debe calcularse SOLO con datos de entrenamiento -- nunca
    incluir datos de validación/test (regla de integridad temporal, uno
    de los 4 Tamices Irrompibles del proyecto).
    """
    return entropy_shannon >= p90_entropy or vitality_tesla == 9


# ─── nash_frozen_7d ─────────────────────────────────────────────────────────

#: gdelt_foundation.py::NASH_ROLLING_WINDOW.
NASH_ROLLING_WINDOW_DAYS = 7

#: legacy usa rolling_std(..., min_periods=2) -- con 1 punto no hay varianza.
MIN_WINDOW_FOR_NASH = 2

#: gdelt_foundation.py::NASH_FROZEN_THRESHOLD -- std normalizado por debajo
#: de esto es "congelado" (Equilibrio de Nash, sin movimiento informacional).
NASH_FROZEN_THRESHOLD = 0.15


class NashFrozenSource(str, Enum):
    """Única fuente implementada -- ver docstring del módulo, hallazgo #2,
    para la fórmula alternativa (spel_ingest_incremental.py, coeficiente
    de variación) que se dejó documentada y no se portó."""
    GDELT_FOUNDATION_NORMALIZED_STD = "gdelt_foundation_normalized_std"


@dataclass(frozen=True)
class NashFrozenResult:
    """Nunca un bool pelado: si insufficient_data es True, frozen es un
    placeholder (False) que NO significa 'sistema no congelado' -- léase
    insufficient_data primero, igual que degraded en VitalityResult."""
    std_normalized: float | None
    frozen: bool
    insufficient_data: bool
    source: NashFrozenSource


def compute_nash_frozen_7d(
    entropy_window: Sequence[float] | None,
    *,
    window_days: int = NASH_ROLLING_WINDOW_DAYS,
    threshold: float = NASH_FROZEN_THRESHOLD,
) -> NashFrozenResult:
    """
    nash_frozen_7d -- estabilidad del RÉGIMEN INFORMACIONAL, no del precio
    ni de la liquidez. Ver docstring del módulo, hallazgo #2: el nombre
    legacy es correcto y se conserva -- una sesión anterior de este mismo
    proyecto lo había re-derivado (equivocadamente) como si fuera ATR-14
    de precio antes de confirmar la fuente real; queda anotado para que
    el error no se repita.

    FUENTE (gdelt_foundation.py::add_nash_and_tesla, sin ambigüedad):
        e_norm = (entropy_shannon - min(ventana)) / (max(ventana) - min(ventana))
        nash_frozen_7d = rolling_std(e_norm, window=7, min_periods=2)
        frozen = nash_frozen_7d < 0.15
    Valor BAJO -> entropía estable (Nash: sin movimiento). Valor ALTO ->
    entropía transitando (oportunidad o caos).

    ADAPTACIÓN respecto al legacy (documentada): el legacy normaliza con
    min/max del AÑO COMPLETO (batch, offline, todo el dataset por
    adelantado). Acá se normaliza con el min/max de la ventana provista
    -- es la versión online-computable de la misma fórmula. Si
    min==max (entropía constante), se usa rango=1.0 para evitar
    división por cero, igual que el legacy.

    Args:
        entropy_window: entropy_shannon en orden cronológico, el ÚLTIMO
            elemento es el punto actual (ventana auto-referencial, igual
            que la Primaria B de vitality_tesla).
        window_days: tamaño del rolling std (legacy: 7).
        threshold: por debajo de esto, frozen=True (legacy: 0.15).

    Validación pendiente (F2): ¿0.15 es el umbral correcto para los 4
    activos del proyecto, o hace falta calibrar por activo?
    """
    if entropy_window is None or len(entropy_window) == 0:
        logger.warning("nash_frozen_7d: entropy_window vacía o None -- insufficient_data.")
        return NashFrozenResult(
            std_normalized=None, frozen=False, insufficient_data=True,
            source=NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD,
        )

    window = list(entropy_window)
    e_min, e_max = min(window), max(window)
    e_range = (e_max - e_min) if (e_max - e_min) > 0 else 1.0
    normalized = [(v - e_min) / e_range for v in window]
    tail = normalized[-window_days:]

    if len(tail) < MIN_WINDOW_FOR_NASH:
        logger.warning(
            "nash_frozen_7d: %d punto(s) tras normalizar (hacen falta %d) -- "
            "insufficient_data.", len(tail), MIN_WINDOW_FOR_NASH,
        )
        return NashFrozenResult(
            std_normalized=None, frozen=False, insufficient_data=True,
            source=NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD,
        )

    std_normalized = float(np.std(tail, ddof=1)) if len(tail) > 1 else 0.0
    return NashFrozenResult(
        std_normalized=std_normalized,
        frozen=std_normalized < threshold,
        insufficient_data=False,
        source=NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD,
    )


# ─── mass_panic_index ────────────────────────────────────────────────────────

#: Necesita std (ddof=1) -> mínimo 2 puntos.
MIN_WINDOW_FOR_ZSCORE = 2

#: NO es una constante legacy nombrada -- "2 sigma" es convención
#: estadística estándar, sin backtest todavía. Ver docstring de la función.
Z_ENTROPY_PANIC_THRESHOLD = 2.0
Z_GOLDSTEIN_PANIC_THRESHOLD = -2.0


class MassPanicComponent(str, Enum):
    """Qué señal(es) dispararon el flag. Auditoría obligatoria: un OR de
    2 señales puede subir falsos positivos si una es ruido puro -- sin
    este registro no hay forma de saberlo en F2."""
    NONE = "none"
    ENTROPY = "entropy"
    GOLDSTEIN = "goldstein"
    BOTH = "both"


@dataclass(frozen=True)
class MassPanicResult:
    flag: bool
    component: MassPanicComponent
    z_entropy: float | None
    z_goldstein: float | None
    insufficient_data: bool


def _zscore_last(window: Sequence[float] | None, current: float | None) -> float | None:
    """z-score de `current` contra media/std de `window` (histórica, NO
    incluye `current` -- misma convención que Fallback A de vitality_tesla).
    None si faltan datos."""
    if window is None or current is None or len(window) < MIN_WINDOW_FOR_ZSCORE:
        return None
    arr = np.asarray(window, dtype=float)
    std = float(np.std(arr, ddof=1))
    if std == 0.0:
        return 0.0
    return float((current - np.mean(arr)) / std)


def compute_mass_panic_index(
    entropy_window: Sequence[float] | None,
    current_entropy: float,
    goldstein_window: Sequence[float] | None = None,
    current_goldstein: float | None = None,
    *,
    z_entropy_threshold: float = Z_ENTROPY_PANIC_THRESHOLD,
    z_goldstein_threshold: float = Z_GOLDSTEIN_PANIC_THRESHOLD,
) -> MassPanicResult:
    """
    EXPERIMENTAL -- sin backtest fuera de muestra (ver docstring del
    módulo, hallazgo #1). A diferencia de vitality_tesla (val_dir=0.5614
    confirmado), NINGUNA de las 2 señales que esto combina tiene
    evidencia empírica todavía.

    FUENTES EN CONFLICTO (2, no reconciliables sin adaptar una de ellas):
      1. spel_bulk_harvester.py + base_adapter.py (SQL):
         frac(GoldsteinScale < -5) sobre EVENTOS individuales GDELT.
         No portable tal cual: ese ingestion (a nivel de evento) no
         existe todavía en el repo nuevo -- `ingestion/` solo tiene
         DerivAdapter (confirmado con `find ingestion/`).
      2. spel_ingest_incremental.py:
         z-score de entropy_shannon vs ventana de 7d, clip [-3,3]. Sí
         portable -- opera al nivel diario/agregado en el que ya
         trabaja este módulo.

    SÍNTESIS implementada (no es un port 1:1 de ninguna sola fuente):
        mass_panic = (z_entropy >= 2.0) OR (z_goldstein <= -2.0)
      El componente de entropía es la fuente #2, directo.
      El componente de goldstein ADAPTA la fuente #1: usa z-score de
      goldstein_mean (ya está en gdelt_foundation.py::ENTROPY_SCHEMA)
      contra su propia historia, en vez de fracción de eventos --
      el umbral crudo "-5" del legacy es sobre eventos individuales y
      no tiene el mismo significado sobre un promedio diario.
      goldstein_window/current_goldstein son OPCIONALES (ingestion de
      GDELT con goldstein_mean puede no estar conectada todavía) -- si
      faltan, el flag se basa solo en entropía, registrado en `component`.

    Validación pendiente (F2): backtest de cada componente por separado
    contra trades reales. Si uno dispara casi siempre, es ruido -- subir
    su umbral o quitarlo. Los umbrales de 2.0/-2.0 sigma son un punto de
    partida convencional, no un valor calibrado.
    """
    z_entropy = _zscore_last(entropy_window, current_entropy)
    z_goldstein = _zscore_last(goldstein_window, current_goldstein)

    if z_entropy is None and z_goldstein is None:
        return MassPanicResult(
            flag=False, component=MassPanicComponent.NONE,
            z_entropy=None, z_goldstein=None, insufficient_data=True,
        )

    entropy_triggered = z_entropy is not None and z_entropy >= z_entropy_threshold
    goldstein_triggered = z_goldstein is not None and z_goldstein <= z_goldstein_threshold

    if entropy_triggered and goldstein_triggered:
        component = MassPanicComponent.BOTH
    elif entropy_triggered:
        component = MassPanicComponent.ENTROPY
    elif goldstein_triggered:
        component = MassPanicComponent.GOLDSTEIN
    else:
        component = MassPanicComponent.NONE

    flag = entropy_triggered or goldstein_triggered
    if flag:
        logger.info(
            "mass_panic_index: flag=True component=%s z_entropy=%s z_goldstein=%s",
            component.value, z_entropy, z_goldstein,
        )

    return MassPanicResult(
        flag=flag, component=component,
        z_entropy=z_entropy, z_goldstein=z_goldstein,
        insufficient_data=False,
    )


# ─── entropy_fibonacci_lags ─────────────────────────────────────────────────

FIBONACCI_LAG_DAYS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21)


@dataclass(frozen=True)
class FibonacciLagResult:
    """Valores por lag -- None donde falta historia. Un lag faltante
    (ej. no hay 21 días todavía) no invalida los demás -- resultado
    parcial, no todo-o-nada."""
    lags: dict[int, float | None]
    available_lags: tuple[int, ...]
    cadence_days: int


def compute_entropy_fibonacci_lags(
    entropy_history: Sequence[float] | None,
    *,
    lag_days: tuple[int, ...] = FIBONACCI_LAG_DAYS,
) -> FibonacciLagResult:
    """
    fibonacci_lag_{1,2,3,5,8,13,21} -- entropy_shannon rezagada N DÍAS.

    CORRECCIÓN DE ESTA SESIÓN (ver docstring del módulo, hallazgo #3):
    2 turnos atrás se había "confirmado" cadencia de 1 MINUTO para este
    feature, basada en la granularidad OHLCV de Deriv (Pregunta A). Esa
    cadencia es real para Deriv, pero NO aplica acá:
    gdelt_foundation.py dice explícitamente "fibonacci_lag_* -> Entropía
    en lags 1,2,3,5,8,13,21 DÍAS", y su ENTROPY_SCHEMA usa "date": pl.Date
    (no datetime) como clave -- confirma agregación diaria, no intradía.
    spel_backbone_engine.py usa fibonacci_lag_21 + ATR14 para stop-loss
    en contexto de swing diario, consistente con esto. La cadencia de 1m
    de Deriv sigue siendo correcta para OTROS features intradía (OHLCV)
    -- simplemente no para este.

    Args:
        entropy_history: entropy_shannon diaria, orden cronológico, el
            ÚLTIMO elemento es HOY. lag_N = entropy_history[-1-N].
        lag_days: qué lags calcular (default: Fibonacci 1..21).

    Validación pendiente (F2, ver docstring del módulo): las 7 columnas
    pueden ser redundantes (alta colinealidad entre lags cercanos) --
    auditar matriz de correlación antes de tratarlas como features
    definitivas. Acá se calculan las 7 en modo diagnóstico.
    """
    if not entropy_history:
        return FibonacciLagResult(
            lags={n: None for n in lag_days}, available_lags=(), cadence_days=1,
        )

    history = list(entropy_history)
    n_points = len(history)

    lags: dict[int, float | None] = {}
    available: list[int] = []
    for n in lag_days:
        idx = -1 - n
        if -idx <= n_points:
            lags[n] = float(history[idx])
            available.append(n)
        else:
            lags[n] = None

    if len(available) < len(lag_days):
        logger.warning(
            "entropy_fibonacci_lags: historia insuficiente (%d días) para "
            "lags %s -- devueltos como None.",
            n_points, [n for n in lag_days if n not in available],
        )

    return FibonacciLagResult(lags=lags, available_lags=tuple(available), cadence_days=1)


# ─── gold_score_bma ─────────────────────────────────────────────────────────

#: spel_bayesian_core.py::NATIVE_ASSETS -- activos con backbone LSTM real.
NATIVE_ASSETS: frozenset[str] = frozenset({"NVDA", "BTC", "XAU", "NIFTY50"})

#: spel_bayesian_core.py::BMA_WEIGHTS -- comentado ahí como "Regla 13
#: (inamovibles -- cambiarlos requiere bug# asignado)". Se portan tal
#: cual, mismo nombre: SÍ es BMA real, con pesos fijos por diseño del
#: propio proyecto (no una heurística sin marco).
BMA_WEIGHTS: dict[str, dict[str, float]] = {
    "native":    {"godel": 0.40, "te_entropy": 0.30, "backbone": 0.30},
    "synthetic": {"godel": 0.55, "te_entropy": 0.45, "backbone": 0.00},
}

#: spel_bayesian_core.py::KL_DIVERGENCE_THRESHOLD.
KL_DIVERGENCE_THRESHOLD = 0.20


class GoldScoreRegime(str, Enum):
    """TRANSCENDENCE/STRUCTURE/CREATION: umbrales sobre godel_score,
    spel_bayesian_core.py (g>=0.90 / g>=0.33 / si no). Los otros 2 son
    para las ramas de kill signal -- nunca se confunden con los 3
    anteriores (el legacy también los separaba: HIGH_ENTROPY y
    DRIFT_DETECTED eran regímenes distintos de TRANSCENDENCE)."""
    TRANSCENDENCE = "transcendence"
    STRUCTURE = "structure"
    CREATION = "creation"
    GODEL_ACTIVE_KILL = "godel_active_kill"
    DRIFT_DETECTED = "drift_detected"


class GoldScoreAction(str, Enum):
    """Umbrales sobre gold_score compuesto, spel_bayesian_core.py."""
    EXECUTE_STRONG = "execute_strong"
    EXECUTE_WEAK = "execute_weak"
    WATCH = "watch"
    HOLD = "hold"


class GoldScoreKillReason(str, Enum):
    NONE = "none"
    GODEL_ACTIVE = "godel_active"      # entropy>=P90 OR vitality==9
    DRIFT_CONTROL = "drift_control"    # kl_divergence > 0.20


@dataclass(frozen=True)
class GoldScoreResult:
    gold_score: float
    regime: GoldScoreRegime
    action: GoldScoreAction
    kill_signal: bool
    kill_reason: GoldScoreKillReason
    weights_used: dict[str, float]
    asset_type: str  # "native" | "synthetic"


def compute_gold_score_bma(
    godel_score: float,
    te_score: float,
    backbone_score: float,
    asset: str,
    entropy_shannon: float,
    p90_entropy: float,
    vitality_tesla: int,
    kl_divergence: float = 0.0,
) -> GoldScoreResult:
    """
    gold_score -- Bayesian Model Averaging de 3 componentes.

    PORT de spel_bayesian_core.py::compute_gold_score_bma (Regla 13),
    con una diferencia deliberada -- ver SÍNTESIS DE KILL SIGNAL abajo.

        gold_score = w_godel*godel_score + w_te*te_score + w_backbone*backbone_score

    Pesos (BMA_WEIGHTS, "inamovibles" según spel_bayesian_core.py --
    cambiarlos requiere bug# asignado):
        native    (NVDA, BTC, XAU, NIFTY50): 0.40 / 0.30 / 0.30
        synthetic (todo lo demás, ej. EURUSD): 0.55 / 0.45 / 0.00
    Inputs clampeados a [0,1] antes de combinar, igual que el legacy.

    SÍNTESIS DE KILL SIGNAL (diferencia deliberada vs. el legacy, no un
    port 1:1): el legacy original mata la señal con un umbral FIJO
    (shannon_entropy > 0.42) independiente de vitality_tesla. Este mismo
    módulo YA tiene godel_active() -- confirmado con evidencia doble
    (godel_bound.py + Bug#35/36) -- que combina entropy>=P90 OR
    vitality==9. Mantener 2 compuertas distintas para "¿esto es
    demasiado riesgoso?" viola Tamiz 3 (una implementación por
    concepto). Acá se REUTILIZA godel_active() en vez de reimplementar
    el umbral fijo -- kill_signal = godel_active(...) OR kl_divergence > 0.20.
    Si se prefiere el umbral fijo de 0.42 en paralelo, es un cambio de
    una línea -- queda marcado para que no sea una decisión oculta.

    DISCREPANCIA encontrada (registrada, no ocultada): la memoria de
    sesiones anteriores decía "KL divergence > 0.20 -> HOLD (not zero
    score)". La fuente real (spel_bayesian_core.py, rama DRIFT_CONTROL)
    sí pone gold_score en 0.0. Acá se porta lo que dice la fuente.

    Args:
        godel_score, te_score, backbone_score: inputs [0,1].
        asset: nombre del activo -- determina native vs synthetic.
        entropy_shannon, p90_entropy, vitality_tesla: para godel_active().
        kl_divergence: default 0.0.

    Validación pendiente (F2): ¿la síntesis del kill signal (godel_active
    en vez del umbral fijo 0.42) diverge del original en algún caso?
    Backtest contra el histórico de COVID-19 usado para godel_bound.py.
    """
    g = max(0.0, min(1.0, godel_score))
    t = max(0.0, min(1.0, te_score))
    b = max(0.0, min(1.0, backbone_score))

    asset_type = "native" if asset.upper() in NATIVE_ASSETS else "synthetic"
    weights = BMA_WEIGHTS[asset_type]

    is_godel_active = godel_active(entropy_shannon, p90_entropy, vitality_tesla)
    is_drift = kl_divergence > KL_DIVERGENCE_THRESHOLD

    if is_godel_active or is_drift:
        kill_reason = GoldScoreKillReason.GODEL_ACTIVE if is_godel_active else GoldScoreKillReason.DRIFT_CONTROL
        regime = GoldScoreRegime.GODEL_ACTIVE_KILL if is_godel_active else GoldScoreRegime.DRIFT_DETECTED
        logger.info("gold_score_bma: kill_signal=True reason=%s asset=%s", kill_reason.value, asset)
        return GoldScoreResult(
            gold_score=0.0, regime=regime, action=GoldScoreAction.HOLD,
            kill_signal=True, kill_reason=kill_reason,
            weights_used=dict(weights), asset_type=asset_type,
        )

    gold_score = round(
        max(0.0, min(1.0, weights["godel"] * g + weights["te_entropy"] * t + weights["backbone"] * b)),
        6,
    )

    if g >= 0.90:
        regime = GoldScoreRegime.TRANSCENDENCE
    elif g >= 0.33:
        regime = GoldScoreRegime.STRUCTURE
    else:
        regime = GoldScoreRegime.CREATION

    if gold_score >= 0.85:
        action = GoldScoreAction.EXECUTE_STRONG
    elif gold_score >= 0.65:
        action = GoldScoreAction.EXECUTE_WEAK
    elif gold_score >= 0.40:
        action = GoldScoreAction.WATCH
    else:
        action = GoldScoreAction.HOLD

    return GoldScoreResult(
        gold_score=gold_score, regime=regime, action=action,
        kill_signal=False, kill_reason=GoldScoreKillReason.NONE,
        weights_used=dict(weights), asset_type=asset_type,
    )
