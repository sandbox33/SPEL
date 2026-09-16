"""
research/gold_score_chain.py
==============================
La cadena gold_score, RETIRADA del camino caliente el 16-sep-2026. El
código es el mismo: se movió, no se reescribió ni se recortó.

QUÉ ES ESTO Y POR QUÉ NO ESTÁ EN core/

`compute_gold_score_bma` depende de `compute_godel_score`, que depende de
`val_dir`, que sale de un LSTM entrenado que NO EXISTE. Eso por sí solo ya
haría de la cadena un camino muerto, pero hay algo peor y es medido -- el
hallazgo del PR #19:

    `compute_gold_score_bma` mata el score a 0.0 cuando la máscara Gödel
    dispara. Y `compute_godel_score` vale 0.0 cuando la máscara NO dispara.
    O sea que el término `w_godel * godel_score` no puede aportar a ningún
    gold_score distinto de cero, pase lo que pase: cuando el componente
    tendría valor, el kill lo anula; cuando el kill no actúa, el componente
    vale cero.

No es un bug que se arregle moviendo el archivo, y por eso el archivo se
mueve: dejarlo en `core/` lo hacía parecer parte del motor. Acá queda
visible como lo que es -- una hipótesis suspendida, con su código y sus
tests intactos para el día que se retome.

`core/price_signals.py` viajó al mismo lugar (`research/price_signals.py`)
por un motivo distinto y del mismo tipo: su tesis direccional se midió el
4-sep-2026 y se refutó. Ver el acta en decision-log.md.

CONDICIÓN DE REVERSIÓN, y es concreta: si Fase 2 entrena el LSTM que
produce `val_dir`, esta cadena vuelve a `core/`. No hace falta reescribir
nada -- basta con mover los símbolos de vuelta y devolverle a
`orchestration/cycle.py` las cinco líneas de import. La entrada de
decision-log.md del 16-sep deja la condición escrita.

LO ÚNICO QUE CAMBIÓ AL MOVER, dicho para que el diff sea auditable:
  · este docstring de módulo,
  · los imports de arriba, que antes los proveía core/scoring.py,
  · `godel_active` ahora se importa de core.scoring en vez de estar en el
    mismo archivo. Se quedó allá a propósito: sigue siendo la máscara que
    el ciclo diario evalúa, y es el único símbolo que esta cadena todavía
    le pide al motor.
Ni una línea de lógica. Los tests que se movieron con el código son los
mismos y pasan sin editarlos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np

from core.scoring import godel_active

logger = logging.getLogger(__name__)

#: LOS DOS TEXTOS DE ADVERTENCIA QUE VIVÍAN EN orchestration/cycle.py.
#: Viajaron con la cadena en vez de borrarse con el cableado que los usaba:
#: son la descripción de POR QUÉ este gold_score no sirve como señal, y esa
#: descripción sigue siendo cierta y sigue haciendo falta el día que alguien
#: retome la cadena. Hoy no los importa nadie -- están acá para que el texto
#: no se pierda, no porque haga falta un llamador.
GOLD_SCORE_SIN_PRECIO_REASON = (
    "gold_score no se calculó para este activo: te_score y backbone_score "
    "necesitan una serie de cierres, y este ciclo solo lee la serie GDELT "
    "persistida. Pasar `closes_por_activo` a run_scoring_cycle() lo "
    "desbloquea. Las tres funciones de componente existen "
    "(research/price_signals.py y research.gold_score_chain.compute_godel_score)."
)

GOLD_SCORE_SIN_PODER_PREDICTIVO = (
    "Este gold_score SE CALCULA pero NO PREDICE. te_score y backbone_score "
    "no sobrevivieron corrección por multiplicidad (Bonferroni ni "
    "Benjamini-Hochberg; holdout p=0.4133 y p=0.5921; un backtest en BTC "
    "perdió 99.2% del capital). godel_score vale 0.0 sin LSTM. Los pesos "
    "0.40/0.30/0.30 nunca se calibraron. No usar como señal operativa."
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






# ─── godel_score: el tercer input de gold_score ─────────────────────────────

#: Default de `val_dir` en el legacy cuando NO hay inferencia disponible
#: (spel_score_engine.py, las tres ramas OFFLINE de `_run_inference`).
#: 0.5 es "sin skill direccional": una moneda.
#:
#: Se documenta pero NO se usa para fabricar un godel_score. En el legacy
#: ese 0.5 nunca llega a la fórmula: las mismas ramas que lo devuelven
#: ponen `godel_activo=False`, y entonces el score sale 0.0 igual. Ver
#: compute_godel_score().
VAL_DIR_SIN_INFERENCIA = 0.5


@dataclass(frozen=True)
class GodelScoreResult:
    """Nunca un float pelado, por la misma razón que VitalityResult y
    NashFrozenResult: `value == 0.0` significa DOS cosas distintas -- que
    la máscara no disparó, o que disparó pero no hay modelo que diga con
    cuánta confianza. Sin `reason`, las dos se ven idénticas en un
    artefacto persistido."""
    value: float             # [0,1] -- el componente Gödel del gold_score
    godel_is_active: bool
    has_inference: bool      # False = no hay val_dir de un modelo real
    reason: str


def compute_godel_score(
    godel_is_active: bool,
    val_dir: float | None,
) -> GodelScoreResult:
    """
    El componente Gödel del gold_score. PORT LITERAL de
    `spel_score_engine.py::SpelScoreEngine.compute`, línea 94:

        val_dir     = inference_result.get("val_dir", 0.5)
        godel_score = float(godel_active) * val_dir if godel_active else 0.0

    O sea: `val_dir` si la máscara disparó, `0.0` si no. El
    `float(godel_active) *` es un no-op dentro de la rama (vale 1.0);
    se porta el efecto, no el ruido.

    SE AUDITÓ LA IMPLEMENTACIÓN, NO EL COMENTARIO. Esa distinción no es
    retórica en este repo: el mismo error se cometió tres veces --
    `vitality_tesla` (44,8% de coincidencia con los datos reales),
    `compute_mass_panic_index` (4,1%) y `compute_entropy_fibonacci_lags`
    (0,0%), las tres veces por seguir un comentario o un doc en vez del
    código que efectivamente corrió. (Las dos últimas se retiraron el
    9-sep por no tener consumidor; se las cita como precedente de la
    auditoría, no como código vivo.)

    QUÉ ES `val_dir`: la confianza direccional que sale de la inferencia
    de un LSTM entrenado (`capa_c_inference.SPELInferenceEngine`). Es una
    accuracy en [0,1], donde 0.5 es "sin skill".

    ══ NO HAY LSTM EN ESTE REPO, Y ESO NO SE DISIMULA ══

    `val_dir=None` significa "no hay inferencia disponible", y entonces
    esta función devuelve 0.0 con `has_inference=False`.

    Eso NO es una invención: es exactamente lo que el legacy hace en
    nuestra situación. Sus tres ramas OFFLINE de `_run_inference` (sin
    torch, sin checkpoint cargado, o excepción) devuelven
    `godel_activo=False` junto con `val_dir=0.5`, y la fórmula da 0.0.
    El legacy corriendo sin modelo produce godel_score = 0.0, y este port
    hace lo mismo.

    La alternativa —pasar el 0.5 y devolverlo cuando la máscara dispara—
    sería fabricar un número de modelo sin modelo. `core/price_signals.py`
    ya había dejado esa decisión por escrito ("no con un valor inventado
    mientras tanto") y este PR la sostiene, no la revierte.

    CONSECUENCIA, dicha en voz alta porque importa: mientras no exista el
    LSTM, el componente Gödel aporta 0.0 SIEMPRE, y por lo tanto el peso
    de 0.40 (activos nativos) o 0.55 (sintéticos) no contribuye nada. El
    gold_score queda acotado por la suma de los otros dos pesos -- 0.60 en
    nativos, 0.45 en sintéticos -- así que NO PUEDE alcanzar los umbrales
    de EXECUTE_WEAK (0.65) ni EXECUTE_STRONG (0.85). Un sistema sin modelo
    no puede emitir una orden de ejecución por esta vía, y eso es
    deseable, no un defecto a corregir bajando umbrales.

    ══ HALLAZGO: EL COMPONENTE ES INERTE AUNQUE APAREZCA EL LSTM ══

    Y no solo mientras no haya modelo. Con `compute_gold_score_bma` tal
    como está hoy, el término `w_godel * godel_score` NO PUEDE aportar a
    ningún gold_score distinto de cero, ni con un val_dir de 1.0:

      · Si la máscara dispara, `compute_gold_score_bma` levanta el
        kill_signal por `godel_active` y pone el score en 0.0, sin mirar
        el componente.
      · Si la máscara NO dispara, esta función devuelve 0.0 por la rama
        `else` del legacy.

    Los dos casos son exhaustivos.

    LA CAUSA NO ESTÁ EN NINGUNA DE LAS DOS FUENTES LEGACY:
      · `spel_score_engine.py` usa `godel_active` para PONDERAR (es esta
        misma fórmula) y no mata por él.
      · `spel_bayesian_core.py` mata solo por Shannon > 0.42 y KL > 0.20,
        y nunca llama a `godel_active`.
    La rama de kill por `godel_active` es un agregado del port, ya
    identificado como tal en la auditoría del PR #17 -- que lo dejó
    explícitamente como tarea aparte. Este patch NO la toca: cambiar la
    lógica de `compute_gold_score_bma` es una decisión de criterio con su
    propia medición, no un efecto colateral de portar una fórmula.

    Hay un test que fija este comportamiento
    (`test_HALLAZGO_el_componente_godel_nunca_aporta_al_gold_score_final`)
    para que, si algún día cambia, sea a conciencia.

    Args:
        godel_is_active: salida de `godel_active()`.
        val_dir: confianza direccional del modelo, en [0,1]. `None`
            cuando no hay inferencia -- que es el caso de este repo hoy.

    Returns:
        GodelScoreResult. `value` es 0.0 en dos casos distintos y
        `reason` los separa.
    """
    if not godel_is_active:
        return GodelScoreResult(
            value=0.0, godel_is_active=False,
            has_inference=val_dir is not None,
            reason="la máscara no disparó: godel_active=False",
        )

    if val_dir is None:
        logger.info(
            "godel_score: máscara activa pero sin inferencia disponible "
            "(val_dir=None) -- componente en 0.0, igual que el legacy sin torch."
        )
        return GodelScoreResult(
            value=0.0, godel_is_active=True, has_inference=False,
            reason=(
                "la máscara disparó pero no hay val_dir: ningún modelo "
                "entrenado sirve inferencia en este repo. No se sustituye "
                "por un valor neutro -- ver compute_godel_score()."
            ),
        )

    return GodelScoreResult(
        value=float(val_dir), godel_is_active=True, has_inference=True,
        reason="val_dir de inferencia real",
    )


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
    p66_entropy: float,
    kl_divergence: float = 0.0,
    legacy_entropy_threshold: float | None = SHANNON_KILL_THRESHOLD,
) -> GoldScoreResult:
    """
    gold_score -- Bayesian Model Averaging de 3 componentes.

    ╔══════════════════════════════════════════════════════════════════╗
    ║  ESTO NO ES UNA SEÑAL OPERATIVA. QUE CALCULE NO ES QUE PREDIGA.  ║
    ╚══════════════════════════════════════════════════════════════════╝

    Dos de sus tres inputs FUERON MEDIDOS y NO son significativos:

      · `te_score` y `backbone_score` (core/price_signals.py): corrección
        por multiplicidad con Bonferroni Y con Benjamini-Hochberg dio
        CERO supervivientes. En holdout, p = 0,4133 y p = 0,5921. Un
        backtest sobre BTC perdió el 99,2% del capital.
      · `godel_score` (compute_godel_score) vale 0.0 mientras no exista un
        LSTM que sirva `val_dir`, que es hoy y hasta que Fase 2 lo
        resuelva.

    Y LOS PESOS TAMPOCO SE MIDIERON NUNCA. El 0.40/0.30/0.30 viene del
    legacy marcado como "inamovible (Regla 13)", pero esa etiqueta
    documenta una decisión de gobernanza, no un ajuste empírico: no hay
    backtest, validación cruzada ni optimización detrás de esos tres
    números. Un promedio ponderado de tres componentes sin poder
    predictivo demostrado, con pesos sin calibrar, no adquiere poder
    predictivo por combinarlos.

    Para qué sirve entonces: el criterio de cierre de Fase 1 pedía que el
    Gold Score SE CALCULE de punta a punta con funciones reales, y esto lo
    cumple. Cualquier uso operativo necesita antes que Fase 2 produzca
    componentes con significancia medida. Quien lea un número de acá y lo
    tome por una recomendación está leyendo mal la función.

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
    godel_active() depende de p66_entropy, que en frío (poca historia)
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
        entropy_shannon, p66_entropy: para godel_active(). `vitality_tesla`
            dejó de ser parámetro en la versión 4.0.0 -- ver godel_active().
            La LÓGICA de esta función no cambió: solo lo que se le pasa.
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

    is_godel_active = godel_active(entropy_shannon, p66_entropy)
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


