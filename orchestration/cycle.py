"""
orchestration/cycle.py
========================
El orquestador -- el ciclo diario que corre sobre varios activos a la vez.

QUÉ EMITE HOY: el RÉGIMEN MEDIDO, y nada más. Por activo:
`entropy_state` (el tercil de entropía: bajo / medio / alto),
`godel_active` (si la entropía superó el borde del tercil superior),
`compute_godel_p66` (de dónde salió ese borde) y `compute_vitality_tesla`.
Las cuatro dependen SOLO de la serie GDELT persistida
(`ingestion/gdelt_series.py`) -- ninguna necesita precio, ni modelo, ni
nada que todavía no exista.

══ LO QUE ESTE MÓDULO DEJÓ DE CALCULAR, el 16-sep-2026 ══

`gold_score`, `godel_score` y `nash_frozen_7d` salieron de acá. El código
no se borró: vive en `research/gold_score_chain.py`, con todos sus tests,
importable y corrible. Igual `te_score` y `backbone_score`, que se fueron a
`research/price_signals.py`.

EL MOTIVO, en una línea: `gold_score` dependía de `godel_score`, que
dependía de `val_dir`, que sale de un LSTM que no existe. Y el hallazgo del
PR #19 lo empeora -- el término `w_godel * godel_score` no puede aportar a
ningún gold_score distinto de cero, porque el kill por máscara Gödel y el
componente Gödel se anulan mutuamente por construcción. `price_signals` se
fue por un motivo distinto y del mismo tipo: su tesis direccional se midió
el 4-sep y se refutó.

O sea que este ciclo venía calculando, todos los días, un número que no
podía significar nada -- y lo emitía con una advertencia de cinco líneas
pegada para que nadie lo usara. Un número que hay que acompañar de un
cartel que dice "no usar" no es una salida del sistema: es ruido con
escolta. Se retiró el número y se retiró el cartel.

CONDICIÓN DE REVERSIÓN, escrita y concreta: si Fase 2 entrena el LSTM que
produce `val_dir`, la cadena vuelve. Devolverla es mover los símbolos de
`research/` a `core/` y restituir cinco líneas de import acá. Ver la
entrada del 16-sep en decision-log.md.

QUÉ ACTIVOS CUBRE, y por qué esa lista exacta: los 5 con clasificación
GDELT real y funcional -- NVDA/XAU/BTC/NIFTY50 (CORE_COUNTRY_FILTERS) +
EURUSD (FX_GOBIERNO_ONLY_ASSETS). Los 5 Índices de Volatilidad
(VOL10..VOL100, ingestion/adapters.py) quedan FUERA a propósito: GDELT no
aplica sobre ellos por diseño (BLUEPRINT.md, Fase 6, Hallazgo 1 -- son
inmunes a noticias reales), y todavía no existe una vía de scoring sin
GDELT para ese tipo de activo.

COLD START: un activo sin ningún día persistido todavía es un caso VÁLIDO,
no un error -- se reporta con data_status="cold_start_no_data", nunca con
un valor inventado. `compute_vitality_tesla` ya tiene su propia cascada
para degradar con poca historia (Tier C); este módulo no reimplementa esa
lógica, la usa.

CRITERIO DE LA MÁSCARA (GODEL_CRITERIA_VERSION 4.0.0-entropy_state_p66):
YA NO ES UN OR. La máscara es `entropy > p66`, el borde del tercil superior
de la entropía sobre ventana móvil de 252 observaciones que termina el día
anterior -- ver core.scoring.godel_active().

Eso NO es un umbral nuevo. La fórmula anterior era `(e >= p90) OR
(vitality == 9)`, y bajo la definición del legacy `vitality == 9` equivale
a `e > p66`; como p90 >= p66 siempre, la primera rama estaba implicada por
la segunda y nunca cambió un resultado. El sistema operaba de facto con P66
y el nombre `p90_entropy` lo ocultaba.

vitality_tesla se sigue calculando y reportando; simplemente no es una
compuerta de trading.

UNA PRECISIÓN SOBRE ESAS 252 OBSERVACIONES, para que el número no se lea
como algo que no es: acá la historia son días de CALENDARIO de la serie
GDELT (`read_series`), así que 252 observaciones son ~252 días corridos
(~8,3 meses). La medición que fijó el 252 corrió sobre el join con OHLCV,
indexado por días de MERCADO, donde 252 es un año hábil. La ventana es la
misma en observaciones y el port es fiel; el tramo de calendario que abarca
no lo es. Si en algún momento se quiere "un año" en ambos lados, eso es una
recalibración del número -- con su medición -- y no un ajuste que
corresponda hacer acá en silencio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from core.scoring import (
    GODEL_CRITERIA_VERSION,
    VitalityResult,
    compute_godel_p66,
    compute_vitality_tesla,
    entropy_state,
    godel_active,
)
from ingestion.gdelt_series import read_series

logger = logging.getLogger("spel.orchestration.cycle")

#: Los 5 activos con classify_gdelt_event() real y funcional hoy. Ver
#: docstring del módulo para por qué esta lista exacta, ni más ni menos.
DEFAULT_CYCLE_ASSETS: tuple[str, ...] = ("NVDA", "XAU", "BTC", "NIFTY50", "EURUSD")


@dataclass(frozen=True)
class AssetCycleResult:
    """
    El régimen de un activo en un día. Ningún campo numérico se inventa
    cuando no hay datos suficientes; `data_status` manda.

    NO TRAE gold_score NI nash_frozen desde el 16-sep-2026 -- ver el
    docstring del módulo. No es que salgan en None: es que no son parte de
    lo que este ciclo emite. Un campo `gold_score: None` invitaría a
    preguntarse qué falta para poblarlo, y lo que falta es un LSTM que no
    está en el horizonte de esta fase.
    """

    asset: str
    data_status: str  # "ok" | "cold_start_no_data"
    n_days_history: int
    vitality_tesla: VitalityResult | None
    #: Tercil de entropía: ENTROPY_STATE_LOW/_MID/_HIGH, o None en warm-up
    #: y en cold start. Es la capa de ESTADO, sin decisión de trading --
    #: ver core.scoring.entropy_state().
    entropy_state: int | None
    #: `entropy > p66`. La única compuerta que este ciclo evalúa.
    godel_is_active: bool | None
    #: El umbral contra el que se evaluó, para que `godel_is_active` sea
    #: auditable sin recalcular: un booleano suelto no dice de qué borde
    #: salió.
    p66_entropy: float | None
    #: Con qué criterio de percentil se calculó `godel_is_active`, SELLADO
    #: en el momento del cálculo (core.scoring.GODEL_CRITERIA_VERSION).
    #:
    #: LA COMPROBACIÓN NO EXISTE TODAVÍA, y decirlo importa: un campo
    #: sellado que nadie verifica da falsa sensación de protección. Este
    #: campo solo DEJA CONSTANCIA de con qué criterio salió el número.
    #: Comparar la versión leída de un artefacto contra la del módulo, y
    #: recalcular si difieren, es trabajo de la capa que persista estos
    #: resultados -- que hoy no existe.
    #:
    #: En cold start (`godel_is_active is None`) no se aplicó ningún
    #: criterio: el campo trae la versión de este build, no la de un
    #: cálculo que no ocurrió.
    godel_criteria_version: str = GODEL_CRITERIA_VERSION


def _build_windows(asset: str) -> tuple[list, list[float], list[float], float | None]:
    """
    Lee la serie persistida y arma las 3 ventanas que las funciones de
    core/scoring.py necesitan. Días con insufficient_events=True (entropy
    None) se tratan como si no existieran para efectos de estas funciones
    -- ninguna puede usar un entropy_shannon inventado, y mezclar "días
    válidos para entropy" con "todos los días para n_events" produciría una
    ventana con longitudes inconsistentes entre sí. Elección documentada,
    no un descuido.

    Devuelve: (dias_validos_completos, entropy_window_sin_actual,
               n_events_window_con_actual, current_entropy_o_None)
    """
    series = read_series(asset)
    valid = [r for r in series if r.entropy_shannon is not None]
    if not valid:
        return [], [], [], None

    current_entropy = valid[-1].entropy_shannon
    entropy_window_sin_actual = [r.entropy_shannon for r in valid[:-1]]
    n_events_window_con_actual = [float(r.n_events) for r in valid]
    return valid, entropy_window_sin_actual, n_events_window_con_actual, current_entropy


def run_scoring_cycle(
    assets: Sequence[str] = DEFAULT_CYCLE_ASSETS,
    *,
    p66_entropy_global_default: float,
) -> dict[str, AssetCycleResult]:
    """
    Corre la anotación de régimen para cada activo en `assets`, a partir de
    lo que haya persistido en ingestion/gdelt_series.py. No toca red, no
    toca ingestion en vivo -- ese es trabajo de `ingestion/run_gdelt.py`;
    este módulo solo consume lo ya persistido.

    PERDIÓ DOS PARÁMETROS el 16-sep-2026: `closes_por_activo` y
    `val_dir_por_activo`. Los dos alimentaban la cadena gold_score, que se
    retiró a `research/` -- ver el docstring del módulo. No se dejaron
    aceptados-y-ignorados: un parámetro que se acepta y no hace nada es
    peor que uno que no existe, porque el llamador cree que sirvió de algo.

    Args:
        assets: activos a procesar. Default: DEFAULT_CYCLE_ASSETS (los 5
            con classify_gdelt_event() funcional).
        p66_entropy_global_default: SIN valor por defecto a propósito --
            compute_adaptive_percentile() documenta explícitamente que para
            estos umbrales NO hay default legacy confirmado y deben
            proveerse. Inventar uno acá sería exactamente el tipo de
            certeza fabricada que este proyecto evita. El caller debe
            proveerlo de forma consciente (y documentar de dónde salió).
            Sigue siendo el default de arranque en frío: con la ventana
            móvil se usa en los primeros días, no en régimen.

    Raises:
        ValueError: si algún asset en `assets` no tiene
            classify_gdelt_event() configurado (typo, o activo genuinamente
            no soportado) -- falla temprano y claro, no degrada en silencio.
    """
    from core.scoring import CORE_COUNTRY_FILTERS, FX_GOBIERNO_ONLY_ASSETS

    results: dict[str, AssetCycleResult] = {}

    for asset in assets:
        if asset not in CORE_COUNTRY_FILTERS and asset not in FX_GOBIERNO_ONLY_ASSETS:
            raise ValueError(
                f"'{asset}' no tiene classify_gdelt_event() configurado -- "
                f"no está en CORE_COUNTRY_FILTERS ni en FX_GOBIERNO_ONLY_ASSETS. "
                f"¿Typo, o un activo que genuinamente todavía no se agregó?"
            )

        valid_days, entropy_hist, n_events_window, current_entropy = _build_windows(asset)

        if current_entropy is None:
            logger.info("cycle: %s sin historia GDELT persistida todavía (cold start)", asset)
            results[asset] = AssetCycleResult(
                asset=asset, data_status="cold_start_no_data", n_days_history=0,
                vitality_tesla=None, entropy_state=None, godel_is_active=None,
                p66_entropy=None,
            )
            continue

        # `n_events_window` va COMPLETA a propósito: desde la versión 3.0.0
        # el recorte a 252 observaciones lo hace compute_vitality_tesla()
        # adentro, con el mismo `_ventana_movil` que usa el percentil de
        # entropía. Recortar acá además sería un segundo mecanismo, y el de
        # core es el que está medido.
        vitality = compute_vitality_tesla(
            n_events_window=n_events_window,
            entropy_window=entropy_hist,
            current_entropy=current_entropy,
        )
        # VENTANA MÓVIL, no acumulado (GODEL_CRITERIA_VERSION 2.0.0):
        # `entropy_hist` es toda la historia sin el día actual, y
        # compute_godel_p66() se queda con sus últimas 252 observaciones.
        # Pasarle la historia entera -- que es lo que este ciclo hacía hasta
        # la versión 1.x -- arrastra la cola vieja y deja días recientes sin
        # muestra. Ver compute_godel_p66() para la medición.
        p66 = compute_godel_p66(
            entropy_hist, global_default=p66_entropy_global_default,
        )
        # vitality_tesla ya NO entra: es un estado, no una compuerta.
        godel = godel_active(
            entropy_shannon=current_entropy,
            p66_entropy=p66.value,
        )
        # El tercil completo, no solo el borde superior. `godel_is_active`
        # dice si estamos por encima de p66; `estado` distingue además el
        # tercil bajo del medio, que es información que la máscara tira.
        estado = entropy_state(entropy_hist + [current_entropy])

        results[asset] = AssetCycleResult(
            asset=asset, data_status="ok", n_days_history=len(valid_days),
            vitality_tesla=vitality, entropy_state=estado,
            godel_is_active=godel, p66_entropy=p66.value,
            # Sellado en el momento del cálculo, no heredado del default:
            # es este godel el que se calculó con este criterio.
            godel_criteria_version=GODEL_CRITERIA_VERSION,
        )
        logger.info(
            "cycle: %s vitality=%d(%s) entropy_state=%s godel_active=%s "
            "p66=%.4f (%d días)",
            asset, vitality.value, vitality.tier_used.value, estado, godel,
            p66.value, len(valid_days),
        )

    return results
