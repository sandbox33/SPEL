"""
orchestration/cycle.py
========================
El orquestador -- el "pegamento" que corre el ciclo de scoring sobre
varios activos a la vez. BLUEPRINT.md lo marca como el bloqueante real
siguiente de Fase 1 (2,818 líneas legacy, 0% portado hasta este patch).

QUÉ CALCULA HOY, de verdad, con funciones reales (no inventadas para esta
ocasión): `vitality_tesla`, `nash_frozen_7d`, `godel_active` -- las 3
dependen SOLO de la serie GDELT persistida (`ingestion/gdelt_series.py`),
ninguna necesita precio/OHLCV todavía.

`gold_score` YA SE CALCULA, con las tres funciones de componente reales:
`compute_godel_score` (core/scoring.py), `compute_transfer_entropy_proxy`
y `compute_backbone_score` (core/price_signals.py). Eso cierra el criterio
de Fase 1 de BLUEPRINT.md.

Hace falta pasarle `closes_por_activo`: este módulo lee solo la serie
GDELT persistida, y dos de los tres componentes necesitan precio. Sin
cierres, `gold_score` sale None con el motivo en
`gold_score_blocked_reason` -- que ahora dice la verdad (faltan datos, no
faltan funciones).

QUE SE CALCULE NO ES QUE PREDIGA, y este módulo no deja que se confunda:
todo resultado con gold_score trae `gold_score_warning` pegado. te_score y
backbone_score fueron medidos y NO son significativos (cero supervivientes
a Bonferroni y a Benjamini-Hochberg; holdout p=0.4133 y p=0.5921; un
backtest en BTC perdió el 99,2% del capital), godel_score vale 0.0
mientras no exista el LSTM, y los pesos 0.40/0.30/0.30 nunca se
calibraron. Ver el docstring de compute_gold_score_bma().

QUÉ ACTIVOS CUBRE, y por qué esa lista exacta: los 5 activos con
clasificación GDELT real y funcional -- NVDA/XAU/BTC/NIFTY50
(CORE_COUNTRY_FILTERS) + EURUSD (FX_GOBIERNO_ONLY_ASSETS, arreglado en
el patch anterior a este mismo). Los 5 Índices de Volatilidad
(VOL10..VOL100, ingestion/adapters.py) quedan FUERA a propósito: GDELT
no aplica sobre ellos por diseño (BLUEPRINT.md, Fase 6, Hallazgo 1 --
son inmunes a noticias reales), y todavía no existe una vía de scoring
sin GDELT para ese tipo de activo -- agregarla acá sería inventar
diseño nuevo sin que Altair lo haya decidido.

COLD START: un activo sin ningún día persistido todavía (la serie GDELT
recién empieza a acumularse esta semana) es un caso VÁLIDO, no un error
-- se reporta con data_status="cold_start_no_data", nunca con un valor
inventado. `compute_vitality_tesla` ya tiene su propia cascada para
degradar con poca historia (Tier C); este módulo no reimplementa esa
lógica, la usa.

CRITERIO DE LA MÁSCARA (GODEL_CRITERIA_VERSION 4.0.0-entropy_state_p66):
YA NO ES UN OR. La máscara es `entropy > p66`, el borde del tercil
superior de la entropía sobre ventana móvil de 252 observaciones que
termina el día anterior -- ver core.scoring.godel_active().

Eso NO es un umbral nuevo. La fórmula anterior era `(e >= p90) OR
(vitality == 9)`, y bajo la definición del legacy `vitality == 9`
equivale a `e > p66`; como p90 >= p66 siempre, la primera rama estaba
implicada por la segunda y nunca cambió un resultado. El sistema operaba
de facto con P66 y el nombre `p90_entropy` lo ocultaba.

Lo que sí cambia es DE DÓNDE sale ese umbral: antes llegaba por
vitality_tesla, que en este repo se calculaba sobre n_events -- una
desviación del legacy, que lo define como tercil de ENTROPÍA. Ahora sale
del tercil de entropía directamente. Ver core.scoring.entropy_state() y
compute_vitality_tesla().

vitality_tesla se sigue calculando y se sigue reportando en
AssetCycleResult; simplemente no es una compuerta de trading.

UNA PRECISIÓN SOBRE ESAS 252 OBSERVACIONES, para que el número no se lea
como algo que no es: acá la historia son días de CALENDARIO de la serie
GDELT (`read_series`), así que 252 observaciones son ~252 días corridos
(~8,3 meses). La medición que fijó el 252 corrió sobre el join con OHLCV,
indexado por días de MERCADO, donde 252 es un año hábil. La ventana es la
misma en observaciones y el port es fiel; el tramo de calendario que
abarca no lo es. Si en algún momento se quiere "un año" en ambos lados,
eso es una recalibración del número -- con su medición -- y no un ajuste
que corresponda hacer acá en silencio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from core.scoring import (
    GODEL_CRITERIA_VERSION,
    GoldScoreResult,
    NashFrozenResult,
    VitalityResult,
    compute_godel_p66,
    compute_godel_score,
    compute_gold_score_bma,
    compute_nash_frozen_7d,
    compute_vitality_tesla,
    godel_active,
)
from core.price_signals import (
    compute_backbone_score,
    compute_transfer_entropy_proxy,
)
from ingestion.gdelt_series import read_series

logger = logging.getLogger("spel.orchestration.cycle")

#: Los 5 activos con classify_gdelt_event() real y funcional hoy. Ver
#: docstring del módulo para por qué esta lista exacta, ni más ni menos.
DEFAULT_CYCLE_ASSETS: tuple[str, ...] = ("NVDA", "XAU", "BTC", "NIFTY50", "EURUSD")

#: ~~GOLD_SCORE_BLOCKED_REASON~~ -- ERA FACTUALMENTE FALSA y se corrigió.
#:
#: Decía: "gold_score_bma() requiere godel_score/te_score/backbone_score
#: reales como input -- NINGUNO tiene función que lo calcule todavía".
#: Los tres la tienen:
#:   · te_score y backbone_score, desde el 18 de agosto
#:     (core/price_signals.py).
#:   · godel_score, desde este patch (core.scoring.compute_godel_score).
#: El texto viejo sobrevivió a la existencia de dos de las tres funciones
#: sin que nadie lo notara -- la misma clase de afirmación desactualizada
#: que este proyecto ya encontró en BLUEPRINT.md ("GDELT 0% portado").
#:
#: Lo que SÍ falta es de otro tipo, y por eso el nombre cambió: no falta
#: una función, faltan DATOS DE PRECIO. Este módulo lee solo la serie
#: GDELT persistida; te_score y backbone_score necesitan `closes`.
GOLD_SCORE_SIN_PRECIO_REASON = (
    "gold_score no se calculó para este activo: te_score y backbone_score "
    "necesitan una serie de cierres, y este ciclo solo lee la serie GDELT "
    "persistida. Pasar `closes_por_activo` a run_scoring_cycle() lo "
    "desbloquea. Las tres funciones de componente existen "
    "(core/price_signals.py y core.scoring.compute_godel_score)."
)

#: Advertencia que viaja pegada a todo gold_score calculado. No es
#: decorativa: dos de los tres componentes fueron MEDIDOS y no son
#: significativos, y los pesos nunca se midieron. Ver el docstring de
#: compute_gold_score_bma() para los números.
GOLD_SCORE_SIN_PODER_PREDICTIVO = (
    "Este gold_score SE CALCULA pero NO PREDICE. te_score y backbone_score "
    "no sobrevivieron corrección por multiplicidad (Bonferroni ni "
    "Benjamini-Hochberg; holdout p=0.4133 y p=0.5921; un backtest en BTC "
    "perdió 99.2% del capital). godel_score vale 0.0 sin LSTM. Los pesos "
    "0.40/0.30/0.30 nunca se calibraron. No usar como señal operativa."
)


@dataclass(frozen=True)
class AssetCycleResult:
    """Resultado de un ciclo para un activo. Ningún campo numérico se
    inventa cuando no hay datos suficientes; data_status manda.

    `gold_score` es None cuando no se pudo calcular (falta precio o falta
    historia), y un GoldScoreResult cuando sí. Los dos casos se
    distinguen sin ambigüedad: `gold_score_blocked_reason` está poblado
    solo en el primero, `gold_score_warning` solo en el segundo."""

    asset: str
    data_status: str  # "ok" | "cold_start_no_data" | "cold_start_current_day_invalid"
    n_days_history: int
    vitality_tesla: VitalityResult | None
    nash_frozen: NashFrozenResult | None
    godel_is_active: bool | None
    gold_score: GoldScoreResult | None
    gold_score_blocked_reason: str | None
    #: Con qué criterio de percentil se calculó `godel_is_active`, SELLADO
    #: en el momento del cálculo (core.scoring.GODEL_CRITERIA_VERSION).
    #:
    #: LA COMPROBACIÓN NO EXISTE TODAVÍA, y decirlo importa: un campo
    #: sellado que nadie verifica da falsa sensación de protección. Este
    #: campo solo DEJA CONSTANCIA de con qué criterio salió el número.
    #: Comparar la versión leída de un artefacto contra la del módulo, y
    #: recalcular si difieren, es trabajo de la capa que persista estos
    #: resultados -- que hoy no existe: `run_scoring_cycle` devuelve un
    #: dict en memoria y nada escribe un AssetCycleResult a disco. Cuando
    #: esa capa nazca, la comprobación vive ahí, no acá.
    #:
    #: En cold start (`godel_is_active is None`) no se aplicó ningún
    #: criterio: el campo trae la versión de este build, no la de un
    #: cálculo que no ocurrió.
    godel_criteria_version: str = GODEL_CRITERIA_VERSION
    #: Poblado SOLO cuando `gold_score` no es None. Viaja pegado al número
    #: a propósito: un gold_score suelto en un log o en un artefacto se
    #: lee como una recomendación, y no lo es. Ver
    #: GOLD_SCORE_SIN_PODER_PREDICTIVO.
    gold_score_warning: str | None = None


def _build_windows(asset: str) -> tuple[list, list[float], list[float], float | None]:
    """
    Lee la serie persistida y arma las 3 ventanas que las funciones de
    core/scoring.py necesitan. Días con insufficient_events=True (entropy
    None) se tratan como si no existieran para efectos de estas 3
    funciones -- ninguna de las 3 puede usar un entropy_shannon
    inventado, y mezclar "días válidos para entropy" con "todos los días
    para n_events" produciría una ventana con longitudes inconsistentes
    entre sí. Elección documentada, no un descuido.

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
    closes_por_activo: Mapping[str, Sequence[float]] | None = None,
    val_dir_por_activo: Mapping[str, float] | None = None,
) -> dict[str, AssetCycleResult]:
    """
    Corre vitality_tesla + nash_frozen_7d + godel_active para cada activo
    en `assets`, a partir de lo que haya persistido en
    ingestion/gdelt_series.py. No toca red, no toca ingestion en vivo --
    ese es trabajo de otro paso (tools/heartbeat.py o un futuro caller),
    este módulo solo consume lo ya persistido.

    Args:
        assets: activos a procesar. Default: DEFAULT_CYCLE_ASSETS (los 5
            con classify_gdelt_event() funcional).
        p66_entropy_global_default: SIN valor por defecto a propósito --
            compute_adaptive_percentile() documenta explícitamente que
            para estos umbrales NO hay default legacy confirmado y deben
            proveerse. Inventar uno acá sería exactamente el tipo de
            certeza fabricada que este proyecto evita. El caller debe
            proveerlo de forma consciente (y documentar de dónde salió).
            Sigue siendo el default de arranque en frío: con la ventana
            móvil se usa en los primeros días, no en régimen.
            SE RENOMBRÓ en la versión 4.0.0 (era `p90_entropy_global_default`)
            porque el umbral que alimenta es el del tercil superior, no un
            P90. El nombre viejo describía un término que nunca cambió un
            resultado -- ver core.scoring.godel_active().
        closes_por_activo: serie de cierres por activo, orden cronológico.
            Es lo único que separa a `gold_score` de calcularse: te_score y
            backbone_score la necesitan. Este módulo NO la va a buscar --
            sigue sin tocar red ni ingestion en vivo; el caller decide de
            dónde salen los cierres. Un activo ausente del mapa reporta
            `gold_score=None` con motivo, no un error.
        val_dir_por_activo: confianza direccional de un modelo entrenado,
            por activo. HOY NADIE LA PRODUCE en este repo, y el default de
            None es lo correcto: `compute_godel_score` devuelve 0.0 con
            `has_inference=False` en vez de fabricar un número. El
            parámetro existe para que, cuando Fase 2 entregue un modelo,
            enchufarlo no requiera tocar la firma.

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
                vitality_tesla=None, nash_frozen=None, godel_is_active=None,
                gold_score=None,
                gold_score_blocked_reason=(
                    "sin historia GDELT persistida: no hay máscara que "
                    "evaluar, así que tampoco hay componente Gödel."
                ),
            )
            continue

        # `n_events_window` va COMPLETA a propósito: desde la versión
        # 3.0.0 el recorte a 252 observaciones lo hace
        # compute_vitality_tesla() adentro, con el mismo `_ventana_movil`
        # que usa el P90 de entropía. Recortar acá además sería un segundo
        # mecanismo, y el de core es el que está medido.
        vitality = compute_vitality_tesla(
            n_events_window=n_events_window,
            entropy_window=entropy_hist,
            current_entropy=current_entropy,
        )
        # nash_frozen_7d usa toda la ventana disponible (incluye el punto
        # actual) como referencia -- ver docstring de compute_nash_frozen_7d.
        nash = compute_nash_frozen_7d(entropy_window=entropy_hist + [current_entropy])
        # VENTANA MÓVIL, no acumulado (GODEL_CRITERIA_VERSION 2.0.0):
        # `entropy_hist` es toda la historia sin el día actual, y
        # compute_godel_p66() se queda con sus últimas 252 observaciones.
        # Pasarle la historia entera -- que es lo que este ciclo hacía
        # hasta la versión 1.x -- arrastra la cola vieja y deja días
        # recientes sin muestra. Ver compute_godel_p66() para la medición.
        #
        # P66 y no P90 (versión 4.0.0): el umbral de la máscara es el
        # borde del tercil superior de entropía. No es un criterio nuevo
        # -- es el que el sistema venía usando de facto, con otro nombre.
        # Ver godel_active() para la demostración.
        p66 = compute_godel_p66(
            entropy_hist, global_default=p66_entropy_global_default,
        )
        # vitality_tesla ya NO entra: es un estado, no una compuerta.
        # Se sigue calculando y reportando en el resultado.
        godel = godel_active(
            entropy_shannon=current_entropy,
            p66_entropy=p66.value,
        )

        # ── gold_score: los tres componentes, con funciones reales ──
        closes = (closes_por_activo or {}).get(asset)
        gold, motivo_bloqueo, aviso = None, GOLD_SCORE_SIN_PRECIO_REASON, None

        if closes is not None:
            # godel_score: PORT del legacy. val_dir=None mientras no haya
            # LSTM -> componente en 0.0, igual que el legacy sin torch.
            componente_godel = compute_godel_score(
                godel_is_active=godel,
                val_dir=(val_dir_por_activo or {}).get(asset),
            )
            te = compute_transfer_entropy_proxy(closes)
            backbone = compute_backbone_score(closes)

            gold = compute_gold_score_bma(
                godel_score=componente_godel.value,
                te_score=te.value,
                backbone_score=backbone.value,
                asset=asset,
                entropy_shannon=current_entropy,
                p66_entropy=p66.value,
            )
            motivo_bloqueo, aviso = None, GOLD_SCORE_SIN_PODER_PREDICTIVO
            logger.info(
                "cycle: %s gold_score=%.4f (godel=%.4f has_inference=%s, "
                "te=%.4f insuf=%s, backbone=%.4f insuf=%s) action=%s -- %s",
                asset, gold.gold_score, componente_godel.value,
                componente_godel.has_inference,
                te.value, te.insufficient_data,
                backbone.value, backbone.insufficient_data,
                gold.action.value, GOLD_SCORE_SIN_PODER_PREDICTIVO,
            )

        results[asset] = AssetCycleResult(
            asset=asset, data_status="ok", n_days_history=len(valid_days),
            vitality_tesla=vitality, nash_frozen=nash, godel_is_active=godel,
            gold_score=gold, gold_score_blocked_reason=motivo_bloqueo,
            # Sellado en el momento del cálculo, no heredado del default:
            # es este godel el que se calculó con este criterio.
            godel_criteria_version=GODEL_CRITERIA_VERSION,
            gold_score_warning=aviso,
        )
        logger.info(
            "cycle: %s vitality=%d(%s) nash_frozen=%s godel_active=%s (%d días)",
            asset, vitality.value, vitality.tier_used.value, nash.frozen, godel,
            len(valid_days),
        )

    return results
