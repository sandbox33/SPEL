"""
research/cycle_gold_score.py
==============================
El CABLEADO de gold_score que vivía dentro de `orchestration/cycle.py`,
retirado el 16-sep-2026 junto con las funciones que componía.

POR QUÉ EXISTE ESTE ARCHIVO, que no es obvio. Las funciones de la cadena se
fueron a `research/gold_score_chain.py` y sus tests unitarios se mudaron sin
tocar un assert. Pero diez tests de `tests/test_cycle.py` no probaban las
funciones sueltas: probaban la COMPOSICIÓN -- que el ciclo lee la serie
GDELT, calcula el p66, arma los tres componentes con cierres reales y los
combina en un número exacto (`GOLD_SCORE_ESPERADO = 0.539239`).

Esos tests no podían mudarse a `research/` sin el cableado, porque lo que
verifican es el cableado. Y borrarlos para "simplificar" habría perdido
justamente el test de punta a punta que fijaba el valor -- el más caro de
reconstruir y el único que atrapa un cambio de fórmula.

Así que el cableado se mueve también. Este módulo es el bloque que se quitó
de `run_scoring_cycle`, con el mismo código: lee las mismas ventanas (reusa
`_build_windows` del ciclo vivo, no una copia), calcula el mismo p66 y
compone los mismos tres componentes.

NO LO IMPORTA NADA DEL MOTOR, y eso se verifica en
`research/tests/test_aislamiento.py`. La dirección de la dependencia es
research -> orchestration, nunca al revés.

CONDICIÓN DE REVERSIÓN: si Fase 2 entrena el LSTM que produce `val_dir`,
este bloque vuelve adentro de `run_scoring_cycle`. Ver decision-log.md,
entrada del 16-sep-2026.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from core.scoring import (
    GODEL_CRITERIA_VERSION,
    VitalityResult,
    compute_godel_p66,
    compute_vitality_tesla,
    godel_active,
)
from orchestration.cycle import DEFAULT_CYCLE_ASSETS, _build_windows
from research.gold_score_chain import (
    GOLD_SCORE_SIN_PODER_PREDICTIVO,
    GOLD_SCORE_SIN_PRECIO_REASON,
    GoldScoreResult,
    NashFrozenResult,
    compute_godel_score,
    compute_gold_score_bma,
    compute_nash_frozen_7d,
)
from research.price_signals import (
    compute_backbone_score,
    compute_transfer_entropy_proxy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetCycleResultConGold:
    """La forma que tenía `AssetCycleResult` ANTES del retiro. Se conserva
    entera -- incluidos `nash_frozen` y los tres campos de gold_score --
    porque los tests que se mudaron acá afirman sobre todos ellos, y
    recortarla habría obligado a editar sus asserts."""

    asset: str
    data_status: str
    n_days_history: int
    vitality_tesla: VitalityResult | None
    nash_frozen: NashFrozenResult | None
    godel_is_active: bool | None
    gold_score: GoldScoreResult | None
    gold_score_blocked_reason: str | None
    godel_criteria_version: str = GODEL_CRITERIA_VERSION
    gold_score_warning: str | None = None


def run_scoring_cycle_con_gold_score(
    assets: Sequence[str] = DEFAULT_CYCLE_ASSETS,
    *,
    p66_entropy_global_default: float,
    closes_por_activo: Mapping[str, Sequence[float]] | None = None,
    val_dir_por_activo: Mapping[str, float] | None = None,
) -> dict[str, AssetCycleResultConGold]:
    """
    `run_scoring_cycle` tal como era hasta el 16-sep-2026, con la anotación
    de gold_score puesta. Misma firma, mismos parámetros -- incluidos
    `closes_por_activo` y `val_dir_por_activo`, que el ciclo vivo ya no
    acepta.
    """
    from core.scoring import CORE_COUNTRY_FILTERS, FX_GOBIERNO_ONLY_ASSETS

    results: dict[str, AssetCycleResultConGold] = {}

    for asset in assets:
        if asset not in CORE_COUNTRY_FILTERS and asset not in FX_GOBIERNO_ONLY_ASSETS:
            raise ValueError(
                f"'{asset}' no tiene classify_gdelt_event() configurado -- "
                f"no está en CORE_COUNTRY_FILTERS ni en FX_GOBIERNO_ONLY_ASSETS. "
                f"¿Typo, o un activo que genuinamente todavía no se agregó?"
            )

        valid_days, entropy_hist, n_events_window, current_entropy = _build_windows(asset)

        if current_entropy is None:
            results[asset] = AssetCycleResultConGold(
                asset=asset, data_status="cold_start_no_data", n_days_history=0,
                vitality_tesla=None, nash_frozen=None, godel_is_active=None,
                gold_score=None,
                gold_score_blocked_reason=(
                    "sin historia GDELT persistida: no hay máscara que "
                    "evaluar, así que tampoco hay componente Gödel."
                ),
            )
            continue

        vitality = compute_vitality_tesla(
            n_events_window=n_events_window,
            entropy_window=entropy_hist,
            current_entropy=current_entropy,
        )
        nash = compute_nash_frozen_7d(entropy_window=entropy_hist + [current_entropy])
        p66 = compute_godel_p66(
            entropy_hist, global_default=p66_entropy_global_default,
        )
        godel = godel_active(
            entropy_shannon=current_entropy,
            p66_entropy=p66.value,
        )

        # ── gold_score: los tres componentes, con funciones reales ──
        closes = (closes_por_activo or {}).get(asset)
        gold, motivo_bloqueo, aviso = None, GOLD_SCORE_SIN_PRECIO_REASON, None

        if closes is not None:
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

        results[asset] = AssetCycleResultConGold(
            asset=asset, data_status="ok", n_days_history=len(valid_days),
            vitality_tesla=vitality, nash_frozen=nash, godel_is_active=godel,
            gold_score=gold, gold_score_blocked_reason=motivo_bloqueo,
            godel_criteria_version=GODEL_CRITERIA_VERSION,
            gold_score_warning=aviso,
        )

    return results
