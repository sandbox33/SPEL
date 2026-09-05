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

QUÉ NO CALCULA, a propósito, y por qué: `gold_score` (compute_gold_score_bma
en core/scoring.py) necesita `godel_score`, `te_score` y `backbone_score`
como inputs -- el propio docstring del módulo scoring.py lo dice:
"Pendiente (fear_momentum, backbone_score real / TE real -- acá son
inputs externos al gold_score, no calculados por este módulo todavía)".
No existe ninguna función en el repo que produzca esos 3 valores desde
datos reales. Inventar un valor placeholder para poder "mostrar un
gold_score" sería exactamente el tipo de "código que funciona pero miente
sobre lo que hace" que motivó el reinicio del 13 de agosto -- así que
este módulo no lo hace. `gold_score` sale siempre None, con la razón
exacta en `gold_score_blocked_reason`.

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

CRITERIO DE LA MÁSCARA (GODEL_CRITERIA_VERSION 3.0.0-rolling_252d_vitality):
las DOS ramas del OR usan ventana móvil de 252 observaciones.
  · El P90 de entropía, desde la versión 2.0.0 -- ver
    core.scoring.compute_godel_p90(). Hasta la 1.x este ciclo le pasaba a
    compute_adaptive_percentile() TODA la historia previa, el criterio
    acumulado que dejó 1.077 días recientes sin una sola muestra.
  · El tercil de n_events del nivel primario de vitality_tesla, desde la
    3.0.0 -- ver core.scoring.compute_vitality_tesla(). Arrastraba la
    misma deriva: la tasa de días en vitality==9 iba de 63% a 11% según
    el año, cuando un tercil debe dar ~33% estable.
Este ciclo no cambió para la 3.0.0: sigue pasando la ventana completa de
n_events y el recorte vive en core/scoring.py, del mismo lado que el de
la entropía.

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
from typing import Sequence

from core.scoring import (
    GODEL_CRITERIA_VERSION,
    NashFrozenResult,
    VitalityResult,
    compute_godel_p90,
    compute_nash_frozen_7d,
    compute_vitality_tesla,
    godel_active,
)
from ingestion.gdelt_series import read_series

logger = logging.getLogger("spel.orchestration.cycle")

#: Los 5 activos con classify_gdelt_event() real y funcional hoy. Ver
#: docstring del módulo para por qué esta lista exacta, ni más ni menos.
DEFAULT_CYCLE_ASSETS: tuple[str, ...] = ("NVDA", "XAU", "BTC", "NIFTY50", "EURUSD")

#: Motivo fijo, reusado en todo resultado -- gold_score está bloqueado
#: por la MISMA razón para cualquier activo, no varía caso a caso.
GOLD_SCORE_BLOCKED_REASON = (
    "gold_score_bma() requiere godel_score/te_score/backbone_score reales "
    "como input -- ninguno tiene función que lo calcule todavía en este "
    "repo (ver docstring de core/scoring.py, línea 6: 'Pendiente'). No se "
    "inventa un valor placeholder para simular que esto ya funciona."
)


@dataclass(frozen=True)
class AssetCycleResult:
    """Resultado de un ciclo para un activo. gold_score es SIEMPRE None
    hoy -- ver GOLD_SCORE_BLOCKED_REASON. Ningún campo numérico se
    inventa cuando no hay datos suficientes; data_status manda."""

    asset: str
    data_status: str  # "ok" | "cold_start_no_data" | "cold_start_current_day_invalid"
    n_days_history: int
    vitality_tesla: VitalityResult | None
    nash_frozen: NashFrozenResult | None
    godel_is_active: bool | None
    gold_score: None
    gold_score_blocked_reason: str
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
    p90_entropy_global_default: float,
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
        p90_entropy_global_default: SIN valor por defecto a propósito --
            compute_adaptive_percentile() documenta explícitamente que
            "para P90 NO hay default legacy confirmado, debe proveerse
            explícitamente". Inventar uno acá sería exactamente el tipo
            de certeza fabricada que este proyecto evita. El caller debe
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
                vitality_tesla=None, nash_frozen=None, godel_is_active=None,
                gold_score=None, gold_score_blocked_reason=GOLD_SCORE_BLOCKED_REASON,
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
        # compute_godel_p90() se queda con sus últimas 252 observaciones.
        # Pasarle la historia entera -- que es lo que este ciclo hacía
        # hasta la versión 1.x -- arrastra la cola vieja y deja días
        # recientes sin muestra. Ver compute_godel_p90() para la medición.
        p90 = compute_godel_p90(
            entropy_hist, global_default=p90_entropy_global_default,
        )
        godel = godel_active(
            entropy_shannon=current_entropy,
            p90_entropy=p90.value,
            vitality_tesla=vitality.value,
        )

        results[asset] = AssetCycleResult(
            asset=asset, data_status="ok", n_days_history=len(valid_days),
            vitality_tesla=vitality, nash_frozen=nash, godel_is_active=godel,
            gold_score=None, gold_score_blocked_reason=GOLD_SCORE_BLOCKED_REASON,
            # Sellado en el momento del cálculo, no heredado del default:
            # es este godel el que se calculó con este criterio.
            godel_criteria_version=GODEL_CRITERIA_VERSION,
        )
        logger.info(
            "cycle: %s vitality=%d(%s) nash_frozen=%s godel_active=%s (%d días)",
            asset, vitality.value, vitality.tier_used.value, nash.frozen, godel,
            len(valid_days),
        )

    return results
