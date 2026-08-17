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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from core.scoring import (
    NashFrozenResult,
    VitalityResult,
    compute_adaptive_percentile,
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

        vitality = compute_vitality_tesla(
            n_events_window=n_events_window,
            entropy_window=entropy_hist,
            current_entropy=current_entropy,
        )
        # nash_frozen_7d usa toda la ventana disponible (incluye el punto
        # actual) como referencia -- ver docstring de compute_nash_frozen_7d.
        nash = compute_nash_frozen_7d(entropy_window=entropy_hist + [current_entropy])
        p90 = compute_adaptive_percentile(
            history=entropy_hist, percentile=90.0,
            global_default=p90_entropy_global_default,
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
        )
        logger.info(
            "cycle: %s vitality=%d(%s) nash_frozen=%s godel_active=%s (%d días)",
            asset, vitality.value, vitality.tier_used.value, nash.frozen, godel,
            len(valid_days),
        )

    return results
