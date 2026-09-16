"""
core/scoring.py
================
`entropy_state` (Capa 1), condición Gödel, `vitality_tesla` (cascada
B->A->C), `nash_frozen_7d`, `godel_score` y `gold_score_bma`.
Pendiente (fear_momentum, backbone_score real / TE real -- acá son
inputs externos al gold_score, no calculados por este módulo todavía):
ver ESTADO.md.

RETIRADAS EL 9-SEP-2026, por no tener ningún consumidor en producción:
`compute_godel_p90`, `compute_mass_panic_index`,
`compute_entropy_fibonacci_lags` y `compute_entropy_delta_lags`. El código
completo vive en la rama `archive/core-scoring-pre-retiro-20260909`; el
motivo de cada una, en `decision-log.md`. Los hallazgos #1 y #3 de abajo se
conservan porque son auditorías del LEGACY que siguen siendo ciertas y le
sirven a quien retome esos features -- no describen código de este módulo.

HALLAZGOS DE ESTA SESIÓN (verificados contra fuente, no supuestos --
"Modo Investigador": diagnosticar antes de construir):

  #1 mass_panic_index tiene 2 fórmulas legacy INCOMPATIBLES, no 1:
     - spel_bulk_harvester.py + base_adapter.py (SQL): frac(GoldsteinScale
       < -5) sobre eventos GDELT individuales. Requiere ingestion a nivel
       de evento que no existe en el repo nuevo todavía.
     - spel_ingest_incremental.py: z-score de entropy_shannon vs ventana
       de 7d. Sí portable al nivel agregado en que ya trabaja este módulo.
     El port sintetizaba ambas y estaba marcado EXPERIMENTAL: NINGUNA de
     las 2 tiene evidencia empírica (a diferencia de B en vitality_tesla,
     que sí la tiene). Esa función se retiró el 9-sep por no tener
     consumidor, y además coincidía solo 4,1% con el legacy. El hallazgo
     queda porque el conflicto de fórmulas es del legacy y sobrevive a la
     función: quien retome mass_panic_index tiene que resolverlo igual.

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
     NOTA (9-sep): la función que implementaba esto se retiró, y con un
     hallazgo que corrige a este mismo párrafo -- el legacy
     `add_fibonacci_lags` desplaza `log_return`, NO entropía; el port
     había seguido el comentario de gdelt_foundation.py en vez del código,
     y la coincidencia era del 0,0%. Lo de "en DÍAS" sigue siendo cierto;
     lo que se desplaza, no era lo que este párrafo decía.

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
     ESA FUNCIÓN YA NO VIVE ACÁ: se retiró a research/gold_score_chain.py
     el 16-sep-2026 (ver su docstring y decision-log.md). El punto queda
     escrito porque describe una decisión de port que sigue siendo cierta,
     y porque quien busque gold_score en este archivo tiene que encontrar
     adónde se fue en vez de un silencio.

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
  B replica el legacy exacto: la ventana INCLUYE el punto actual y el
  percentil se calcula de forma auto-referencial -- así es como lo hacía
  spel_ingest_incremental.py::compute_entropy_features.
  A no tiene precedente legacy, así que usa la convención más limpia:
  la ventana es histórica (NO incluye el punto actual), y el valor actual
  se compara contra los percentiles de esa historia. Si en algún momento
  se prefiere unificar la convención, es un cambio de una línea -- queda
  marcado acá para que no sea una inconsistencia silenciosa.
  PRECISIÓN (versión 3.0.0): B ya NO calcula el percentil sobre toda la
  ventana que recibe, sino sobre sus últimas GODEL_ROLLING_WINDOW_DAYS
  observaciones. Eso cambió el TAMAÑO de la ventana, no su carácter
  auto-referencial: el punto actual sigue siendo su último elemento. La
  diferencia de convención con A sigue siendo la misma de siempre.

CONDICIÓN GÖDEL -- confirmada con evidencia doble, no solo una fuente:
  godel_active = entropy_shannon > p66_entropy   (versión 4.0.0)
  Era `(entropy >= p90) OR (vitality == 9)`. Bajo la definición del
  legacy esas dos ramas no eran independientes: `vitality == 9` es
  `entropy > p66`, y como p90 >= p66 la primera implicaba la segunda.
  El OR colapsaba al tercil superior y la rama del P90 nunca cambió un
  resultado -- el sistema operó de facto con P66 desde el principio.
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


# ─── Ventana móvil: un solo mecanismo para las dos ramas de la máscara ──────

#: Ventana móvil de los estadísticos de la máscara Gödel. 252 = días
#: hábiles de un año.
#:
#: La usan el umbral de la máscara (compute_godel_p66, y antes su gemela
#: compute_godel_p90, retirada el 9-sep) y el tercil de n_events del nivel
#: primario de vitality_tesla (desde la 3.0.0). Es una sola constante --
#: el defecto que ambas corrigen es el mismo (una serie con tendencia
#: comparada contra su propia cola vieja) y tener dos ventanas distintas
#: obligaría a justificar por qué difieren.
GODEL_ROLLING_WINDOW_DAYS = 252


def _ventana_movil(historia: Sequence[float] | None, window: int) -> list[float]:
    """
    Las últimas `window` OBSERVACIONES de `historia`. Nada más.

    Existe para que las dos ramas de la máscara recorten igual: cuando el
    tercil de vitality_tesla se pasó a ventana móvil, copiar el `[-window:]`
    del umbral de la máscara habría creado dos mecanismos paralelos que
    pueden divergir en silencio. Es una función de tres líneas justamente
    porque el recorte es todo lo que comparten -- qué se hace después con
    esa ventana (percentil adaptativo vs. tercil) es distinto en cada rama
    y sigue viviendo en cada una.

    Observaciones, no días de calendario: si el caller ya filtró días
    inválidos, la ventana abarca más de `window` días corridos. Quien
    construye la historia decide qué cuenta como observación.

    Raises:
        ValueError: si window < 1. Una ventana vacía no es un criterio más
            conservador: deja el estadístico sin datos todos los días, y
            eso es un fallo silencioso, no una degradación.
    """
    if window < 1:
        raise ValueError(
            f"window debe ser >= 1, recibido: {window}. Una ventana vacía "
            f"dejaría el estadístico sin observaciones todos los días."
        )
    return list(historia)[-window:] if historia else []

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
    n_events_rolling_window: int = GODEL_ROLLING_WINDOW_DAYS,
) -> VitalityResult:
    """
    Cascada de 3 niveles para vitality_tesla. Ver docstring del módulo
    para la justificación completa de cada nivel. La cascada, su orden de
    degradación y los umbrales P33/P66 no cambiaron nunca; lo único que
    cambió (versión 3.0.0) es el TAMAÑO de la ventana del nivel primario.

    ══ DESDE LA VERSIÓN 4.0.0 ESTA FUNCIÓN NO ALIMENTA LA MÁSCARA ══

    Sigue existiendo como señal y se puede seguir consultando, pero
    `godel_active()` ya no la recibe. La razón, y la auditoría que la
    respalda, importan más que el cambio:

    EL NIVEL PRIMARIO SOBRE n_events FUE UNA DESVIACIÓN DEL LEGACY. El
    legacy tiene DOS fórmulas incompatibles para vitality_tesla, y este
    port eligió la equivocada:

      · gdelt_foundation.py::add_nash_and_tesla -- TERCIL DE ENTROPÍA:
            p33 = df["entropy_shannon"].quantile(0.33)
            p66 = df["entropy_shannon"].quantile(0.66)
            vitality = 3 if e <= p33 else (6 if e <= p66 else 9)
        Es la que produce la columna `vitality_tesla` del ENTROPY_SCHEMA,
        o sea la que está en los parquets.

      · spel_ingest_incremental.py::compute_entropy_features -- TERCIL DE
        n_events, sobre `current['n_events']`. Es la que este repo portó,
        citándola como "la única con evidencia empírica real".

    Cuál produjo los datos que el proyecto usa quedó zanjado midiendo
    contra la columna real de los parquets, 3.998 días: el tercil de
    ENTROPÍA coincide 99,8%; el de n_events, 44,8% y 49,3%. Los parquets
    salieron de gdelt_foundation.py. La cita de "evidencia empírica" del
    port apuntaba a la fórmula que NO generó esos datos.

    POR QUÉ SE REVIRTIÓ, más allá de la fidelidad: con la fórmula del
    legacy, `vitality == 9` equivale a `entropy > p66`, así que la segunda
    rama del OR quedaba lógicamente implicada por la primera y el solape
    era del 100% -- exactamente el "respaldo redundante" que la
    documentación decía que era. Con n_events el solape cayó a 43-51% y
    esa rama pasó a aportar el 72% de los disparos: un respaldo se había
    convertido en la señal dominante sin que nadie lo decidiera.

    Y hay una razón que ninguna ventana móvil arregla: n_events mide
    cobertura mediática de GDELT, que tiene tendencia estructural propia
    (expansión del número de fuentes, no actividad real del mundo). Esa
    tendencia vive DENTRO de la ventana, así que recortarla no la elimina
    -- ver la medición de la versión 3.0.0, que bajó la tasa de nueves de
    ~61% a ~44% y no a un tercio.

    PENDIENTE, explícito: si n_events se conserva como señal propia,
    debería normalizarse (fracción del total diario o z-score móvil) antes
    de tener cualquier consumidor. No se hizo en este PR porque su único
    consumidor era la máscara de la que se lo está sacando: normalizarlo
    ahora sería un cambio de comportamiento sin nadie que lo consuma y sin
    medición que lo respalde.

    POR QUÉ EL NIVEL PRIMARIO USA VENTANA MÓVIL, con números medidos sobre
    la serie real (4.880 días, 2013-04-01 a 2026-09-03, recalculando esta
    misma función con la ventana que usaba producción):

        % de días con vitality == 9
                BTC    XAU            BTC    XAU
        2013   62.9   63.3     2020  18.3   12.8
        2014   41.8   43.9     2021  22.5   21.6
        2015   52.3   60.5     2022  25.8   20.6
        2016   42.6   38.5     2023  45.1   51.1
        2017   21.1   19.7     2024  24.3   19.4
        2018   31.2   30.4     2025  28.8   24.8
        2019   20.5   20.3     2026  10.6   11.0

    Un tercil debe dar ~33% estable. Iba de 63% a 11%. NO es arranque en
    frío: el nivel primario se usó en 4.878 de los 4.880 días. Es que
    n_events tiene tendencia y el tercil se calculaba contra TODA la
    historia previa, así que un día se comparaba contra un volumen de
    cobertura mediática de hace diez años.

    Efecto sobre la máscara: el fold 1 de BTC disparaba al 66,7%, con 511
    de 545 disparos por vitalidad y solo 13 por entropía. Eso infla el OOF
    de 330 (folds 3-5) a 1185 y cruza el umbral de 620 sin señal real
    detrás -- un n que se ve suficiente y no lo es.

    Es la misma corrección que la versión 2.0.0 le hizo al P90 de
    entropía, con el mismo mecanismo (`_ventana_movil`) y la misma
    constante (GODEL_ROLLING_WINDOW_DAYS): las dos ramas del OR sufrían
    la misma deriva.

    LA VENTANA SIGUE INCLUYENDO EL PUNTO ACTUAL. El P90 de entropía usa la
    ventana previa SIN el día que evalúa; acá no, y la diferencia es
    deliberada, no un olvido: `n_events_window` es auto-referencial por
    diseño heredado del legacy (spel_ingest_incremental.py::
    compute_entropy_features), está documentado como tal desde el port
    original, y sacar el día propio cambiaría la semántica del nivel
    primario -- no solo su ventana. Este cambio es de TAMAÑO de ventana,
    nada más. Ver la NOTA en el docstring del módulo, "DIFERENCIA DE
    CONVENCIÓN ENTRE B Y A".

    RESPALDO 1 (A) NO SE TOCÓ, y conviene saberlo: su tercil de entropía
    tiene la misma exposición estructural (percentil sobre toda la ventana
    que reciba). Queda fuera de este cambio porque la medición lo pone en
    2 de 4.880 días -- corregirlo sin poder medir el efecto sería mover un
    umbral a ciegas. Pendiente explícito, no un descuido.

    Args:
        n_events_window: ventana de conteo de eventos GDELT, INCLUYENDO
            el punto actual como último elemento (igual que el legacy).
            Se recortan sus últimas `n_events_rolling_window`
            observaciones. None o con menos de MIN_WINDOW_FOR_PERCENTILE
            puntos -> cae a A.
        entropy_window: ventana HISTÓRICA de entropy_shannon, sin incluir
            el punto actual. None o insuficiente -> cae a C.
        current_entropy: entropy_shannon del punto actual -- siempre
            requerido, es lo único que necesita el nivel C (cold-start).
        global_p33 / global_p66: percentiles globales fijos para el nivel C.
        n_events_rolling_window: tamaño de la ventana móvil del nivel
            primario. Default GODEL_ROLLING_WINDOW_DAYS. Nombrado por su
            rama a propósito: NO aplica al Respaldo A.

    WARM-UP: con menos de `n_events_rolling_window` observaciones la
    ventana es toda la historia disponible -- de facto el criterio
    acumulado, igual que en compute_godel_p66(). No hay alternativa sin
    inventar observaciones que no existen. El piso de
    MIN_WINDOW_FOR_PERCENTILE sigue mandando por debajo de 3 puntos.

    Raises:
        InvalidThresholdError: si global_p33 >= global_p66.
        ValueError: si n_events_rolling_window < 1 (lo lanza
            `_ventana_movil`).
    """
    if global_p33 >= global_p66:
        raise InvalidThresholdError(
            f"global_p33 ({global_p33}) debe ser estrictamente menor que "
            f"global_p66 ({global_p66})."
        )

    # PRIMARIA (B) -- tercil de n_events sobre VENTANA MÓVIL, todavía
    # auto-referencial (el punto actual sigue siendo su último elemento).
    if n_events_window is not None and len(n_events_window) >= MIN_WINDOW_FOR_PERCENTILE:
        # El recorte va ANTES del piso de MIN_WINDOW_FOR_PERCENTILE en
        # sentido lógico pero no puede cambiarlo: `_ventana_movil` nunca
        # devuelve menos puntos de los que ya había salvo que se recorte,
        # y el recorte solo achica hacia 252, que es >> 3.
        ventana = _ventana_movil(n_events_window, n_events_rolling_window)
        current_n = ventana[-1]
        p33 = float(np.percentile(ventana, 33))
        p66 = float(np.percentile(ventana, 66))
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


# ─── entropy_state: la Capa 1, estado sin decisión de trading ───────────────

#: Los tres estados. Son un ESTADO del régimen informacional, no una señal:
#: ninguno significa "operar" ni "no operar". Numerados 0/1/2 y no 3/6/9 a
#: propósito -- el 3/6/9 es la escala de vitality_tesla y confundir las dos
#: es lo que llevó a que un estado terminara actuando como filtro.
ENTROPY_STATE_LOW = 0
ENTROPY_STATE_MID = 1
ENTROPY_STATE_HIGH = 2

#: Terciles del legacy: gdelt_foundation.py::TESLA_PERCENTILE_THRESHOLDS
#: = (33.0, 66.0). Port literal, incluido el `<=` (no `<`).
ENTROPY_STATE_PERCENTILES: tuple[float, float] = (33.0, 66.0)

#: El percentil que define el umbral de la máscara Gödel: el borde del
#: tercil superior. UNA sola fuente de verdad -- la usan
#: compute_godel_p66(), entropy_state() y el tool que mide la máscara. Que
#: el tool midiera un percentil distinto del que usa producción sería
#: medir otra cosa y no notarlo.
GODEL_MASK_PERCENTILE = ENTROPY_STATE_PERCENTILES[1]

#: Cómo se representa un día SIN ventana completa: `None`, no un estado.
#:
#: Un día de warm-up no tiene un estado "bajo" ni "medio" -- no tiene
#: estado, porque no hay contra qué compararlo. Devolver 0 o 1 ahí
#: mezclaría días medidos con días adivinados en la misma columna, y
#: cualquier distribución calculada sobre esa columna estaría contaminada
#: sin que se note. `None` obliga a que quien agregue decida qué hacer con
#: ellos; los tests de distribución de este módulo los excluyen
#: explícitamente antes de contar.
ENTROPY_STATE_WARMUP = None


def entropy_state(
    entropy_series: Sequence[float] | None,
    window: int = GODEL_ROLLING_WINDOW_DAYS,
) -> int | None:
    """
    Estado del régimen de entropía del último día de `entropy_series`:
    ENTROPY_STATE_LOW / MID / HIGH, o None si no hay ventana completa.

    FUNCIÓN PURA Y SIN DECISIONES DE TRADING. Describe en qué tercil de su
    propia historia reciente cae la entropía de hoy. No dice si operar. Esa
    separación es el punto de esta capa: mezclar estado y filtro es lo que
    convirtió un respaldo documentado como "red de seguridad redundante" en
    la señal dominante de la máscara.

    PORT DE gdelt_foundation.py::add_nash_and_tesla (rama vitality_tesla),
    verificado con `git show` sobre origin/archive/legacy-pre-20260813:

        p33 = df["entropy_shannon"].quantile(0.33)
        p66 = df["entropy_shannon"].quantile(0.66)
        vitality = 3 if e <= p33 else (6 if e <= p66 else 9)

    Es TERCIL DE ENTROPÍA, no de n_events. El port original de este repo lo
    implementó sobre n_events y esa desviación no estaba documentada -- ver
    compute_vitality_tesla() para la auditoría completa y la evidencia.

    DESVIACIÓN DEL LEGACY, deliberada y con motivo (misma disciplina que
    nash_frozen_7d): el legacy calcula p33/p66 sobre TODO EL AÑO de una
    sola vez -- `add_nash_and_tesla` corre en batch sobre el resultado
    anual ya completo. Eso es look-ahead: el estado del 3 de enero se
    decide con entropía de diciembre. Es admisible en un pipeline de
    etiquetado histórico y NO lo es en algo que alimenta una decisión en
    vivo. Acá los terciles salen de una ventana móvil causal que termina
    el día ANTERIOR, con el mismo `_ventana_movil` que usan las otras dos
    ramas. Portar el batch literal habría roto el primero de los Tamices.

    LA FRONTERA TEMPORAL: `entropy_series[-1]` es el día que se clasifica y
    NO entra en su propia ventana. Los terciles salen de
    `entropy_series[:-1]`, recortada a sus últimas `window` observaciones.
    Sin ese desplazamiento un día movería el estadístico contra el que se
    lo compara. Es la convención del Respaldo A, ya documentada en el
    docstring del módulo como "la más limpia".

    WARM-UP: mientras haya menos de `window` observaciones ANTERIORES al
    día, devuelve ENTROPY_STATE_WARMUP (None). A diferencia de
    compute_godel_p66(), que degrada a ventana expandible, acá no se
    degrada: un tercil es una afirmación sobre la posición relativa dentro
    de una distribución, y con media ventana esa posición no es comparable
    con la de un día en régimen. Un umbral degradado sigue siendo un
    umbral; un estado degradado sería una etiqueta distinta con el mismo
    nombre.

    Args:
        entropy_series: entropy_shannon en orden cronológico. El ÚLTIMO
            elemento es el día que se clasifica.
        window: observaciones de la ventana. Default
            GODEL_ROLLING_WINDOW_DAYS, la misma de las otras dos ramas.

    Returns:
        0, 1 o 2 -- o None si no hay ventana completa.

    Raises:
        ValueError: si window < 1 (lo lanza `_ventana_movil`).
    """
    if not entropy_series or len(entropy_series) < 2:
        # Sin al menos un día previo no hay ventana ninguna.
        _ventana_movil(entropy_series, window)   # valida `window` igual
        return ENTROPY_STATE_WARMUP

    hoy = float(entropy_series[-1])
    previos = _ventana_movil(entropy_series[:-1], window)

    if len(previos) < window:
        return ENTROPY_STATE_WARMUP

    p33 = float(np.percentile(previos, ENTROPY_STATE_PERCENTILES[0]))
    p66 = float(np.percentile(previos, ENTROPY_STATE_PERCENTILES[1]))
    if hoy <= p33:
        return ENTROPY_STATE_LOW
    if hoy <= p66:
        return ENTROPY_STATE_MID
    return ENTROPY_STATE_HIGH


# ─── Condición Gödel ────────────────────────────────────────────────────────

def godel_active(entropy_shannon: float, p66_entropy: float) -> bool:
    """
    Filtro de la máscara Gödel: ¿la entropía de hoy está en el TERCIL
    SUPERIOR de su historia reciente?

        godel_active = entropy_shannon > p66_entropy

    EL PARÁMETRO SE LLAMABA `p90_entropy` Y ESO ERA UNA MENTIRA. Vale la
    pena dejar escrito por qué, porque el nombre viejo ocultó durante todo
    el proyecto qué criterio estaba corriendo de verdad.

    La fórmula anterior era `(entropy >= p90) OR (vitality_tesla == 9)`.
    Bajo la definición del legacy -- que es tercil de ENTROPÍA, ver
    entropy_state() -- `vitality_tesla == 9` equivale exactamente a
    `entropy > p66`. Y como p90 >= p66 siempre, por definición de
    percentil, el primer término IMPLICA el segundo:

        (e >= p90)  ⟹  (e > p66)      =>   A ∨ B  =  B

    El OR colapsaba a la rama de vitality. La rama del P90 nunca cambió un
    resultado: no existe un día que dispare por P90 y no dispare ya por el
    tercil superior. Medido sobre 5.000 días sintéticos con la definición
    del legacy: la rama P90 dispara el 10,0% de los días, la rama del
    tercil el 34,0%, el OR el 34,0%, y los días con P90 sin tercil son
    CERO.

    Es decir: EL SISTEMA OPERÓ DE FACTO CON P66 DESDE EL PRINCIPIO. El
    nombre `p90_entropy` describía un término inerte. Esta función no
    cambia el comportamiento efectivo de la máscara -- lo nombra.

    Lo que sí cambió es de dónde sale el umbral. Antes llegaba por la rama
    de vitality_tesla, que en este repo se calculaba sobre n_events (una
    desviación del legacy, ver compute_vitality_tesla). Ahora sale del
    tercil de entropía, que es lo que el legacy define. Ver
    compute_godel_p66() y entropy_state().

    LA COMPARACIÓN ES ESTRICTA (`>`, no `>=`), y eso también es port: el
    legacy asigna 9 cuando el valor NO cumple `e <= p66`. Un `>=` movería
    de tercil a los días que caen exactamente sobre el borde.

    `p66_entropy` debe calcularse SOLO con días anteriores al que se
    evalúa -- nunca incluir el día propio ni datos de validación/test
    (regla de integridad temporal, uno de los 4 Tamices Irrompibles).
    compute_godel_p66() ya lo garantiza.

    Args:
        entropy_shannon: entropía del día que se evalúa.
        p66_entropy: umbral del tercil superior de su historia reciente.

    NOTA SOBRE `vitality_tesla`: ya no es parámetro. Sigue existiendo como
    señal (compute_vitality_tesla) pero no alimenta el filtro -- ver el
    docstring de esa función para por qué un estado no debe ser una
    compuerta de trading.
    """
    return entropy_shannon > p66_entropy


# ─── Umbrales de la máscara Gödel: percentil de ventana móvil ───────────────

#: GODEL_ROLLING_WINDOW_DAYS vive arriba, junto a `_ventana_movil()`: desde
#: la versión 3.0.0 la comparten todas las ramas.

#: Versión del criterio con el que se calculó la máscara Gödel.
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
#:   2.0.0-rolling_252d -- P90 de entropía sobre ventana móvil de 252
#:        observaciones con desplazamiento de un día. Ver
#:        compute_godel_p66() para la medición que lo motivó.
#:   3.0.0-rolling_252d_vitality -- la MISMA ventana móvil aplicada al
#:        tercil de n_events del nivel primario de vitality_tesla, que
#:        arrastraba la misma deriva sin corregir. Ver
#:        compute_vitality_tesla() para la medición. Mayor y no menor
#:        porque cambia el valor de vitality de días ya calculados: un
#:        artefacto de la 2.x no es comparable con uno de la 3.x.
#:   4.0.0-entropy_state_p66 -- se separa el ESTADO del FILTRO. La máscara
#:        deja de ser un OR: es `entropy > p66` sobre el tercil de
#:        ENTROPÍA (la definición del legacy), y vitality_tesla sale del
#:        filtro. Mayor porque cambia de dónde sale el umbral: antes
#:        llegaba por el tercil de n_events, ahora por el de entropía.
#:        El comportamiento EFECTIVO de la máscara no cambia de umbral
#:        conceptual (ya era P66, ver godel_active) pero sí de serie.
GODEL_CRITERIA_VERSION = "4.0.0-entropy_state_p66"


def compute_godel_p66(
    entropy_history: Sequence[float] | None,
    global_default: float,
    *,
    window: int = GODEL_ROLLING_WINDOW_DAYS,
) -> AdaptivePercentileResult:
    """
    El umbral que consume godel_active(): el borde del TERCIL SUPERIOR de
    la entropía, sobre ventana móvil que termina el día anterior.

    Hasta el 9-sep tuvo una gemela, `compute_godel_p90`, idéntica salvo
    por el percentil que pedía. Se retiró: quedó sin consumidor al pasar
    la máscara a P66, y mantener dos funciones que solo difieren en una
    constante invita a que alguien use la que no corresponde.

    Por qué 66 y no 90: ver godel_active(). En resumen, la máscara ya
    operaba de facto con P66 -- la rama del P90 estaba lógicamente
    implicada por la del tercil superior y nunca cambió un resultado.
    Esta función nombra el umbral que el sistema venía usando.

    RELACIÓN CON entropy_state(): con ventana completa las dos coinciden
    exactamente. `entropy_state(serie) == ENTROPY_STATE_HIGH` es
    equivalente a `godel_active(serie[-1], compute_godel_p66(serie[:-1],
    ...).value)` siempre que haya >= MIN_OBS_FOR_ROLLING observaciones, que
    es el único régimen donde entropy_state() devuelve algo distinto de
    None. Hay un test que fija esa equivalencia: si se rompe, el filtro
    dejó de preguntar lo que la Capa 1 responde.

    Fuera de ese régimen las dos difieren a propósito: entropy_state()
    devuelve None (no hay estado sin ventana), mientras que esta función
    degrada al híbrido/global de compute_adaptive_percentile, porque un
    umbral degradado sigue siendo un umbral usable y un filtro tiene que
    responder algo todos los días.

    Args:
        entropy_history: entropy_shannon en orden cronológico, SIN el día
            que se evalúa.
        global_default: valor de arranque en frío. Mismo contrato que
            compute_adaptive_percentile.
        window: tamaño de la ventana. Default GODEL_ROLLING_WINDOW_DAYS.

    Raises:
        ValueError: si window < 1 (lo lanza `_ventana_movil`).
    """
    return compute_adaptive_percentile(
        history=_ventana_movil(entropy_history, window),
        percentile=GODEL_MASK_PERCENTILE, global_default=global_default,
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
