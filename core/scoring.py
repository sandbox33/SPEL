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

    CÓMO SE CALCULA ESE p90_entropy: ver compute_godel_p90() -- desde
    GODEL_CRITERIA_VERSION 2.0.0 es un percentil de VENTANA MÓVIL, no
    acumulado. La firma de esta función NO cambió: sigue recibiendo el
    umbral ya resuelto y devolviendo un bool. La máscara, vitality_tesla
    y el OR quedan exactamente como estaban.
    """
    return entropy_shannon >= p90_entropy or vitality_tesla == 9


# ─── p90 de la máscara Gödel: percentil de ventana móvil ────────────────────

#: Ventana del percentil de la máscara Gödel. 252 = días hábiles de un año.
#: Parte del CONTRATO junto con el desplazamiento de un día: cambiar
#: cualquiera de los dos cambia el criterio y obliga a subir
#: GODEL_CRITERIA_VERSION.
GODEL_ROLLING_WINDOW_DAYS = 252

#: Versión del criterio con el que se calculó un p90 de la máscara Gödel.
#:
#: Existe para que un artefacto persistido con el criterio anterior se
#: DETECTE y se recalcule, en vez de mezclarse en silencio con resultados
#: nuevos. Deliberadamente NO forma parte del retorno de godel_active():
#: esa función devuelve bool y cambiarle la firma rompería a todos sus
#: llamadores. La consume quien persiste resultados.
#:
#: Historia:
#:   1.x  (implícita, sin constante) -- percentil ACUMULADO: toda la
#:        historia previa, sin ventana.
#:   2.0.0-rolling_252d -- percentil de ventana móvil de 252 días con
#:        desplazamiento de un día. Ver compute_godel_p90() para la
#:        medición que motivó el cambio.
GODEL_CRITERIA_VERSION = "2.0.0-rolling_252d"


def compute_godel_p90(
    entropy_history: Sequence[float] | None,
    global_default: float,
    *,
    window: int = GODEL_ROLLING_WINDOW_DAYS,
) -> AdaptivePercentileResult:
    """
    El p90_entropy que consume godel_active(), calculado sobre una VENTANA
    MÓVIL de `window` observaciones que TERMINA EN EL DÍA ANTERIOR.

    NO reimplementa el percentil: recorta la historia y llama a
    compute_adaptive_percentile(), que sigue siendo la única
    implementación del percentil adaptativo en el repo. Lo único que
    cambia respecto del criterio anterior es QUÉ historia recibe.

    POR QUÉ SE CAMBIÓ, con números medidos (tools/measure_godel_samples.py
    --compare-modes, sobre BTC 5.893 días de precio y XAU 5.383, entropía
    GDELT 3.998 días por activo, 2015-01-01 a 2025-12-31):

        activo   ACUMULADO   MOVIL   ZSCORE
        BTC            284     611      544
        XAU             68     398      350

    La entropía deriva a la baja de forma monótona (BTC de 1.1726 a
    0.9970 de media entre 2015 y 2025; XAU de 1.3519 a 1.1522). Un
    percentil acumulado arrastra la cola de 2015-2018 para siempre: en
    XAU el P90 de cuatro años seguidos queda por debajo del umbral
    global, y quedan 1.077 días recientes sin una sola muestra en ambos
    activos.

    POR QUÉ MÓVIL Y NO Z-SCORE, también medido y no por preferencia:
      · La entropía normalizada no es normal (Jarque-Bera p ~ 1e-141 en
        BTC y 1e-169 en XAU; skew -0.54 y -0.46; curtosis en exceso
        +1.73 y +2.03), así que el Phi^-1(0.90) = 1.2816 que usa el modo
        ZSCORE impone un umbral entre 21% y 27% por encima del percentil
        90 empírico real (1.0582 y 1.0118).
      · La desviación de la ventana móvil está inflada por la propia
        tendencia: aporta el 38% de la dispersión en BTC y el 47% en XAU.
      El percentil empírico no sufre ninguna de las dos.

    EFECTO SECUNDARIO, más relevante que el conteo: con el criterio
    acumulado la máscara la dominaba vitality_tesla (225 de 284 disparos
    en BTC, participación de la entropía 20.8%). Con ventana móvil la
    entropía pasa a dominar (386 de 611, participación 63.2%; en XAU
    87.2%). La máscara no cambió -- cambió cuál de sus dos ramas la
    sostiene.

    EL DESPLAZAMIENTO DE UN DÍA ES PARTE DEL CONTRATO, no un detalle de
    implementación: `entropy_history` NO incluye el día que se está
    evaluando. Sin ese desplazamiento habría fuga temporal -- un día se
    compararía contra un percentil que él mismo movió. Es la misma
    convención que ya usan el Respaldo A de compute_vitality_tesla y
    _zscore_last en este módulo.

    WARM-UP -- ventana expandible con lag de un día. Para los primeros
    `window` días no hay 252 observaciones previas, así que la ventana es
    todo lo que haya (0...t-1). Eso significa, dicho explícitamente, que
    esos días usan DE FACTO EL CRITERIO ACUMULADO. Es el 6,3% del dataset
    medido y no hay alternativa sin fuga temporal: la única forma de dar
    252 observaciones al día 10 sería tomarlas del futuro. El warm-up se
    acopla al fallback que compute_adaptive_percentile ya tiene por
    debajo de 10 y de 100 observaciones -- no se duplica esa lógica acá.

    Args:
        entropy_history: entropy_shannon en orden cronológico, SIN el día
            que se evalúa. La ventana son las últimas `window`
            OBSERVACIONES de esta lista: si el caller ya filtró días
            inválidos, la ventana abarca más de `window` días de
            calendario. Quien construye la historia decide qué cuenta
            como observación.
        global_default: mismo contrato que compute_adaptive_percentile --
            para P90 no hay default legacy confirmado, debe proveerse
            explícitamente.
        window: tamaño de la ventana. Default GODEL_ROLLING_WINDOW_DAYS.

    Raises:
        ValueError: si window < 1. Una ventana vacía no es un criterio
            más conservador -- devolvería el default global todos los
            días y eso es un fallo silencioso, no una degradación.
    """
    if window < 1:
        raise ValueError(
            f"window debe ser >= 1, recibido: {window}. Una ventana vacía "
            f"dejaría el percentil en global_default todos los días."
        )

    ventana = list(entropy_history)[-window:] if entropy_history else []
    return compute_adaptive_percentile(
        history=ventana, percentile=90.0, global_default=global_default,
    )


# ─── nash_frozen_7d ─────────────────────────────────────────────────────────

#: gdelt_foundation.py::NASH_ROLLING_WINDOW.
NASH_ROLLING_WINDOW_DAYS = 7

#: legacy usa rolling_std(..., min_periods=2) -- con 1 punto no hay varianza.
MIN_WINDOW_FOR_NASH = 2

#: gdelt_foundation.py::NASH_FROZEN_THRESHOLD -- std normalizado por debajo
#: de esto es "congelado" (Equilibrio de Nash, sin movimiento informacional).
NASH_FROZEN_THRESHOLD = 0.15

#: Fix de bug de esta sesión: `entropy_window` debe ser al menos esto
#: veces `window_days` para que la referencia de normalización no
#: colapse con la cola del std (ver docstring de compute_nash_frozen_7d
#: para el caso numérico que confirmó el bug). Sin backtest -- punto de
#: partida razonable, no calibrado.
MIN_REFERENCE_MULTIPLIER = 3


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
    insufficient_reference: bool
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

    BUG CORREGIDO EN ESTA SESIÓN (confirmado con números, no solo
    argumentado): la primera versión de esta función normalizaba con el
    min/max de la MISMA ventana de 7 días usada para el std -- eso
    fuerza el rango normalizado a [0,1] SIEMPRE, sin importar la
    magnitud real de la variación. Micro-ruido de rango real 0.0015
    producía std_normalized=0.33 (falso "no congelado"); con una
    referencia de 60 días el mismo ruido da 0.0024 (correcto,
    "congelado"). Fix: `entropy_window` es ahora la ventana de
    REFERENCIA (tan larga como haya historia -- ideal: todo lo
    disponible, acercándose al "año completo" del legacy), separada de
    `window_days` (7, fijo) que solo define la cola sobre la que se
    calcula el std. Si `entropy_window` no trae bastante historia MÁS
    ALLÁ de esos 7 días, el bug se puede reproducir igual -- por eso
    `insufficient_reference=True` cuando la referencia no es al menos
    MIN_REFERENCE_MULTIPLIER veces más larga que window_days.

    Se descartó coeficiente de variación (alternativa mencionada): el
    umbral NASH_FROZEN_THRESHOLD=0.15 fue calibrado en el legacy sobre
    la escala normalizada [0,1] -- aplicar el mismo 0.15 a un CV (escala
    distinta) sería un número sin sentido, no una migración válida.

    Args:
        entropy_window: TODA la historia disponible de entropy_shannon,
            orden cronológico, el ÚLTIMO elemento es el punto actual.
            Se usa para normalizar (min/max de acá) Y para tomar la cola
            de `window_days` puntos sobre la que se calcula el std.
            Cuantos más puntos más allá de `window_days`, mejor la
            referencia -- ver insufficient_reference.
        window_days: tamaño de la cola para el std (legacy: 7). NO
            afecta la referencia de normalización.
        threshold: por debajo de esto, frozen=True (legacy: 0.15).

    Validación pendiente (F2): ¿0.15 es el umbral correcto para los 4
    activos del proyecto, o hace falta calibrar por activo? ¿Cuánta
    referencia (días) es "suficiente" en la práctica, más allá del
    múltiplo mínimo acá elegido sin backtest?

    HALLAZGO de integración real (ingestion/gdelt_series.py + este
    módulo, no un supuesto): insufficient_reference mide CANTIDAD de
    días, no si esos días tienen RANGO suficiente para normalizar de
    forma estable. Con 30 días reales de entropía casi constante
    (rango total ~0.04), insufficient_reference=False (30 ≥ 21) mientras
    std_normalized dio 0.31 -- muy por encima del umbral -- porque
    normalizar contra un rango chico estira hasta el ruido normal.
    No es un bug de esta función (la fórmula hace exactamente lo que
    el legacy define); es un límite real del criterio de "suficiente"
    que MIN_REFERENCE_MULTIPLIER no captura. Pendiente para F2 junto
    con la calibración del threshold: ¿agregar un piso de rango mínimo
    (e_max - e_min) además del piso de cantidad de días?
    """
    if entropy_window is None or len(entropy_window) == 0:
        logger.warning("nash_frozen_7d: entropy_window vacía o None -- insufficient_data.")
        return NashFrozenResult(
            std_normalized=None, frozen=False, insufficient_data=True,
            insufficient_reference=False,
            source=NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD,
        )

    window = list(entropy_window)
    insufficient_reference = len(window) < window_days * MIN_REFERENCE_MULTIPLIER
    if insufficient_reference:
        logger.warning(
            "nash_frozen_7d: solo %d puntos de referencia (se recomiendan >= %d, "
            "%dx window_days) -- std_normalized puede estar inflado por micro-ruido, "
            "igual que el bug corregido en esta sesión.",
            len(window), window_days * MIN_REFERENCE_MULTIPLIER, MIN_REFERENCE_MULTIPLIER,
        )

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
            insufficient_reference=insufficient_reference,
            source=NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD,
        )

    std_normalized = float(np.std(tail, ddof=1)) if len(tail) > 1 else 0.0
    return NashFrozenResult(
        std_normalized=std_normalized,
        frozen=std_normalized < threshold,
        insufficient_data=False,
        insufficient_reference=insufficient_reference,
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
    #: Fijo en True -- ninguna de las 2 señales que esto combina tiene
    #: backtest todavía (ver docstring de compute_mass_panic_index). No
    #: es un parámetro: no hay forma de que esto sea False hasta que
    #: exista esa validación.
    is_experimental: bool = True


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


@dataclass(frozen=True)
class DeltaLagResult:
    deltas: dict[int, float | None]
    available_lags: tuple[int, ...]


def compute_entropy_delta_lags(
    entropy_history: Sequence[float] | None,
    *,
    lag_days: tuple[int, ...] = FIBONACCI_LAG_DAYS,
) -> DeltaLagResult:
    """
    ΔE_k = E_t - E_{t-k} -- diferencias, no niveles. Formulación
    ADICIONAL a compute_entropy_fibonacci_lags(), no un reemplazo.

    Por qué no se redujo a un subconjunto {1,5,21} ni se reemplazaron
    los niveles por deltas en la función existente: la colinealidad
    entre lags cercanos (Validación pendiente F2 ya documentada en
    compute_entropy_fibonacci_lags) es una hipótesis razonable, pero
    confirmarla necesita una matriz de correlación sobre ENTROPÍA REAL
    -- que no existe todavía (no hay ingestion GDELT corriendo, ver
    docstring del módulo). Elegir {1,5,21} ahora sería exactamente el
    tipo de número sin evidencia que este proyecto evita en cada
    decisión anterior. Niveles y deltas son objetos matemáticos
    distintos (nivel = dónde está la entropía; delta = cuánto cambió) --
    tener ambos disponibles deja que la auditoría de F2 compare cuál
    aporta más, en vez de que esta sesión lo decida a ciegas.

    Un lag_days ya personalizable (compute_entropy_fibonacci_lags(...,
    lag_days=(1,5,21))) cubre la parte de "reducir el subconjunto" sin
    código nuevo -- ver ese parámetro si lo que hace falta es menos
    columnas, no una fórmula distinta.

    Args:
        entropy_history: igual semántica que compute_entropy_fibonacci_lags
            (último elemento = hoy).
        lag_days: qué lags calcular (default: Fibonacci 1..21).

    Validación pendiente (F2): comparar poder predictivo de niveles vs.
    deltas con datos reales, antes de elegir uno como default del
    pipeline de features.
    """
    if not entropy_history:
        return DeltaLagResult(deltas={n: None for n in lag_days}, available_lags=())

    history = list(entropy_history)
    n_points = len(history)
    current = history[-1]

    deltas: dict[int, float | None] = {}
    available: list[int] = []
    for n in lag_days:
        idx = -1 - n
        if -idx <= n_points:
            deltas[n] = float(current - history[idx])
            available.append(n)
        else:
            deltas[n] = None

    return DeltaLagResult(deltas=deltas, available_lags=tuple(available))


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

#: spel_bayesian_core.py::SHANNON_KILL_THRESHOLD -- umbral fijo del
#: legacy original. Reincorporado en esta sesión como red de seguridad
#: independiente de godel_active() -- ver docstring de
#: compute_gold_score_bma para el razonamiento completo.
SHANNON_KILL_THRESHOLD = 0.42


class GoldScoreRegime(str, Enum):
    """TRANSCENDENCE/STRUCTURE/CREATION: umbrales sobre godel_score,
    spel_bayesian_core.py (g>=0.90 / g>=0.33 / si no). Los otros 3 son
    para las ramas de kill signal -- nunca se confunden con los 3
    anteriores (el legacy también los separaba: HIGH_ENTROPY y
    DRIFT_DETECTED eran regímenes distintos de TRANSCENDENCE)."""
    TRANSCENDENCE = "transcendence"
    STRUCTURE = "structure"
    CREATION = "creation"
    GODEL_ACTIVE_KILL = "godel_active_kill"
    DRIFT_DETECTED = "drift_detected"
    HIGH_ENTROPY_LEGACY_KILL = "high_entropy_legacy_kill"


class GoldScoreAction(str, Enum):
    """Umbrales sobre gold_score compuesto, spel_bayesian_core.py."""
    EXECUTE_STRONG = "execute_strong"
    EXECUTE_WEAK = "execute_weak"
    WATCH = "watch"
    HOLD = "hold"


class GoldScoreKillReason(str, Enum):
    NONE = "none"
    GODEL_ACTIVE = "godel_active"                    # entropy>=P90 OR vitality==9
    DRIFT_CONTROL = "drift_control"                  # kl_divergence > 0.20
    LEGACY_ENTROPY_THRESHOLD = "legacy_entropy_threshold"  # entropy > 0.42 fijo


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
    legacy_entropy_threshold: float | None = SHANNON_KILL_THRESHOLD,
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

    SÍNTESIS DE KILL SIGNAL, actualizada en esta sesión: la primera
    versión reemplazaba el umbral fijo del legacy (shannon_entropy >
    0.42) por godel_active() puro, argumentando Tamiz 3 (una
    implementación por concepto). Se reincorpora el umbral fijo como
    RED DE SEGURIDAD INDEPENDIENTE, no como reemplazo de esa decisión:
    godel_active() depende de p90_entropy, que en frío (poca historia)
    puede venir de compute_adaptive_percentile() en modo GLOBAL -- un
    default sin backtest. Si ese default está mal calibrado,
    godel_active() puede fallar en dejar pasar entropías
    moderadas-altas. legacy_entropy_threshold es un chequeo absoluto,
    independiente de esa calibración -- exactamente el rol de una red
    de seguridad, no el de la señal principal.

        kill_signal = godel_active(...) OR kl_divergence > 0.20
                      OR (legacy_entropy_threshold is not None
                          AND entropy_shannon > legacy_entropy_threshold)

    Prioridad si varias disparan a la vez (para kill_reason, todas
    ponen gold_score=0.0 y action=HOLD igual): godel_active primero
    (evidencia doble, dos fuentes distintas) > legacy_entropy_threshold
    (red de seguridad, una sola fuente) > drift_control (mide otra
    cosa -- desvío del modelo, no nivel de entropía).

    legacy_entropy_threshold=None desactiva esta red de seguridad y
    vuelve al comportamiento anterior (solo godel_active + drift).

    DISCREPANCIA encontrada (registrada, no ocultada): la memoria de
    sesiones anteriores decía "KL divergence > 0.20 -> HOLD (not zero
    score)". La fuente real (spel_bayesian_core.py, rama DRIFT_CONTROL)
    sí pone gold_score en 0.0. Acá se porta lo que dice la fuente.

    Args:
        godel_score, te_score, backbone_score: inputs [0,1].
        asset: nombre del activo -- determina native vs synthetic.
        entropy_shannon, p90_entropy, vitality_tesla: para godel_active().
        kl_divergence: default 0.0.
        legacy_entropy_threshold: default SHANNON_KILL_THRESHOLD (0.42).
            None para desactivar.

    Validación pendiente (F2): con datos reales, ¿la red de seguridad
    dispara alguna vez que godel_active() no lo haga ya? El benchmark
    A/B/C de esta sesión compara los 3 casos con datos sintéticos --
    la validación con datos reales sigue pendiente.
    """
    g = max(0.0, min(1.0, godel_score))
    t = max(0.0, min(1.0, te_score))
    b = max(0.0, min(1.0, backbone_score))

    asset_type = "native" if asset.upper() in NATIVE_ASSETS else "synthetic"
    weights = BMA_WEIGHTS[asset_type]

    is_godel_active = godel_active(entropy_shannon, p90_entropy, vitality_tesla)
    is_drift = kl_divergence > KL_DIVERGENCE_THRESHOLD
    is_legacy_kill = (
        legacy_entropy_threshold is not None
        and entropy_shannon > legacy_entropy_threshold
    )

    if is_godel_active or is_legacy_kill or is_drift:
        if is_godel_active:
            kill_reason, regime = GoldScoreKillReason.GODEL_ACTIVE, GoldScoreRegime.GODEL_ACTIVE_KILL
        elif is_legacy_kill:
            kill_reason, regime = GoldScoreKillReason.LEGACY_ENTROPY_THRESHOLD, GoldScoreRegime.HIGH_ENTROPY_LEGACY_KILL
        else:
            kill_reason, regime = GoldScoreKillReason.DRIFT_CONTROL, GoldScoreRegime.DRIFT_DETECTED
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


# ─── gdelt_pipeline_classification ──────────────────────────────────────────

#: gdelt_foundation.py::ASSET_COUNTRY_FILTERS -- port directo. XAU vacío =
#: sin filtro (todo el dataset), no un error de captura.
CORE_COUNTRY_FILTERS: dict[str, tuple[str, ...]] = {
    "NVDA": ("USA", "TWN", "KOR", "CHN", "JPN"),
    "XAU": (),
    "BTC": ("USA", "CHN", "RUS", "PRK", "DEU", "GBR"),
    "NIFTY50": ("IND", "PAK", "CHN", "USA"),
}

#: SIN PRECEDENTE LEGACY -- EURUSD no aparece en ASSET_COUNTRY_FILTERS ni en
#: base_adapter.py::_KEYWORDS (grep confirma: cero menciones en todo el
#: proyecto). DISEÑADO acá, no portado: USA (Fed, lado USD) + DEU (mayor
#: economía de la Eurozona -- GDELT no tiene código de país para "Eurozona"
#: como entidad supranacional, DEU es el proxy más directo del BCE).
GOBIERNO_COUNTRY_FILTERS: tuple[str, ...] = ("USA", "DEU")

#: BUG ENCONTRADO Y CORREGIDO ESTA SESIÓN (confirmado, no supuesto):
#: classify_gdelt_event(asset="EURUSD") lanzaba ValueError SIEMPRE, porque
#: el chequeo original exigía asset en CORE_COUNTRY_FILTERS antes de
#: siquiera llegar al chequeo GOBIERNO -- el docstring documentaba
#: "GOBIERNO: EURUSD" como vía de clasificación, pero el código nunca la
#: dejaba ejecutar. Cero tests la ejercitaban (grep confirma:
#: test_scoring.py nunca llama classify_gdelt_event con asset="EURUSD").
#: Fix: EURUSD (y cualquier futuro par FX sin país nativo propio) se
#: registra acá explícitamente y se clasifica SOLO contra
#: GOBIERNO_COUNTRY_FILTERS, sin pasar por CORE_COUNTRY_FILTERS.
#:
#: GBPUSD/USDJPY/USDCHF/AUDUSD (ya soportados por DerivAdapter para
#: precio) NO están acá todavía -- requeriría decidir qué país no-USA
#: representa a cada banco central (BoE/BoJ/SNB/RBA), decisión de Altair
#: pendiente, no inventada acá.
FX_GOBIERNO_ONLY_ASSETS: frozenset[str] = frozenset({"EURUSD"})


class GdeltPipeline(str, Enum):
    CORE = "core"
    GOBIERNO = "gobierno"
    NONE = "none"


@dataclass(frozen=True)
class GdeltClassificationResult:
    pipeline: GdeltPipeline
    matched_countries: tuple[str, ...]


def classify_gdelt_event(
    actor_countries: Sequence[str | None],
    asset: str,
) -> GdeltClassificationResult:
    """
    Clasifica un evento GDELT como CORE (activo nativo) o GOBIERNO (EURUSD)
    por país de actor -- única vía gratuita confirmada. Actor1Type
    ('BUSINESS'/'GOV') NO existe en ninguna fuente del proyecto: ni
    gdelt_foundation.py (bulk CSV gratis) ni base_adapter.py (ese usa
    _KEYWORDS, pero corre sobre BigQuery -- tiene costo de GCP, descartado
    por requisito explícito de Altair de mantener todo gratuito).

    CORE: XAU/BTC/NVDA/NIFTY50 -- port directo de
    gdelt_foundation.py::ASSET_COUNTRY_FILTERS. XAU con lista vacía usa
    TODO el dataset (así está en el legacy, no un descuido).

    GOBIERNO: EURUSD -- sin precedente legacy, diseñado acá (ver
    GOBIERNO_COUNTRY_FILTERS). Un evento matchea GOBIERNO si su país de
    actor está en (USA, DEU), independientemente del activo -- refleja
    que la política monetaria Fed/BCE mueve el par sin importar contra
    qué otro activo se esté evaluando.

    Args:
        actor_countries: Actor1CountryCode/Actor2CountryCode del evento,
            códigos ISO-3 (pueden venir None si GDELT no los reportó).
        asset: activo CORE contra el que se evalúa (ignorado para el
            chequeo GOBIERNO, que es independiente del activo).

    Un evento puede matchear ambos pipelines a la vez (ej. USA aparece en
    NVDA y en GOBIERNO) -- eso es correcto, no un bug: la clasificación es
    por relevancia, no exclusiva.

    Validación pendiente (F2): ¿DEU solo alcanza para "Eurozona", o hace
    falta FRA/ITA? Sin backtest todavía -- ver docstring del módulo.
    """
    countries = {c for c in actor_countries if c}

    if asset in FX_GOBIERNO_ONLY_ASSETS:
        # Sin país "nativo" propio -- se clasifica SOLO por GOBIERNO,
        # nunca llega a CORE_COUNTRY_FILTERS (ver FX_GOBIERNO_ONLY_ASSETS).
        matched = tuple(sorted(countries & set(GOBIERNO_COUNTRY_FILTERS)))
        pipeline = GdeltPipeline.GOBIERNO if matched else GdeltPipeline.NONE
        return GdeltClassificationResult(pipeline=pipeline, matched_countries=matched)

    core_filter = CORE_COUNTRY_FILTERS.get(asset)
    if core_filter is None:
        raise ValueError(f"Activo '{asset}' sin CORE_COUNTRY_FILTERS configurado")

    is_core = (len(core_filter) == 0) or bool(countries & set(core_filter))
    is_gobierno = bool(countries & set(GOBIERNO_COUNTRY_FILTERS))

    if is_core and is_gobierno:
        matched = tuple(sorted(countries & (set(core_filter) | set(GOBIERNO_COUNTRY_FILTERS))))
        pipeline = GdeltPipeline.CORE  # CORE tiene prioridad si el activo evaluado es CORE
    elif is_core:
        matched = tuple(sorted(countries & set(core_filter))) if core_filter else tuple(sorted(countries))
        pipeline = GdeltPipeline.CORE
    elif is_gobierno:
        matched = tuple(sorted(countries & set(GOBIERNO_COUNTRY_FILTERS)))
        pipeline = GdeltPipeline.GOBIERNO
    else:
        matched = ()
        pipeline = GdeltPipeline.NONE

    return GdeltClassificationResult(pipeline=pipeline, matched_countries=matched)


# ─── adaptive_percentile (multi-tier) ───────────────────────────────────────

#: Por debajo de esto, la rolling no tiene sentido -- puro global.
MIN_OBS_FOR_HYBRID = 10

#: Por encima de esto, la rolling es confiable -- puro rolling.
MIN_OBS_FOR_ROLLING = 100

#: Peso del global en la zona híbrida (10-99 obs). Sin calibrar -- ver
#: Validación pendiente abajo.
HYBRID_WEIGHT_GLOBAL = 0.7


class PercentileSource(str, Enum):
    GLOBAL = "global"
    ROLLING = "rolling"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class AdaptivePercentileResult:
    value: float
    source: PercentileSource
    n_obs: int


def compute_adaptive_percentile(
    history: Sequence[float] | None,
    percentile: float,
    global_default: float,
    *,
    min_obs_for_hybrid: int = MIN_OBS_FOR_HYBRID,
    min_obs_for_rolling: int = MIN_OBS_FOR_ROLLING,
    hybrid_weight_global: float = HYBRID_WEIGHT_GLOBAL,
) -> AdaptivePercentileResult:
    """
    Percentil adaptativo de 3 niveles -- SIN precedente legacy exacto (el
    legacy cargaba P90/P33/P66 desde un JSON pre-calibrado por Altair, no
    calculaba esto en runtime). Diseñado acá, no portado.

    Genérico para cualquier percentil (P90 para godel_active, P33/P66
    para vitality_tesla) -- la lógica de "¿cuánta historia hay?" es la
    misma sin importar cuál percentil se pida.

        n_obs < 10          -> global_default puro (sin historia confiable)
        10 <= n_obs < 100    -> híbrido: 0.7*global + 0.3*rolling
        n_obs >= 100         -> rolling puro (np.percentile sobre history)

    Args:
        history: serie histórica (ej. entropy_shannon), sin incluir el
            punto actual -- mismo criterio que el resto del módulo.
        percentile: 0-100 (ej. 90.0 para P90).
        global_default: valor a usar en el nivel GLOBAL. Para P33/P66,
            usar DEFAULT_GLOBAL_P33/P66 (ya confirmados en el legacy,
            HINC OMNIA CERNO §Vitality_Tesla). Para P90 NO hay default
            legacy confirmado -- debe proveerse explícitamente (ej.
            desde ENTROPY_P90_GLOBAL en governance, si se define).

    Validación pendiente (F2): min_obs_for_hybrid=10, min_obs_for_rolling=100
    y hybrid_weight_global=0.7 son puntos de partida razonables, no
    valores calibrados con backtest -- ninguno de los 3 tiene evidencia
    empírica todavía.
    """
    n_obs = len(history) if history else 0

    if n_obs < min_obs_for_hybrid:
        return AdaptivePercentileResult(
            value=global_default, source=PercentileSource.GLOBAL, n_obs=n_obs,
        )

    rolling_value = float(np.percentile(list(history), percentile))

    if n_obs >= min_obs_for_rolling:
        return AdaptivePercentileResult(
            value=rolling_value, source=PercentileSource.ROLLING, n_obs=n_obs,
        )

    hybrid_value = hybrid_weight_global * global_default + (1 - hybrid_weight_global) * rolling_value
    return AdaptivePercentileResult(
        value=hybrid_value, source=PercentileSource.HYBRID, n_obs=n_obs,
    )
