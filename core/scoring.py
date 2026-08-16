"""
core/scoring.py
================
Primera pieza de la capa de scoring: `vitality_tesla` (cascada de 3 niveles)
y la condición Gödel. El resto de `core/scoring.py` (BMA completo, TE,
backbone, mass_panic_index, nash_frozen_7d, fear_momentum) se agrega en
incrementos posteriores — cada uno bloqueado por su propia decisión de
fórmula, todavía sin resolver. Ver SPEL_PERSISTENCE_STATE.md.

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
