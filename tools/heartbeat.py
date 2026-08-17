"""
tools/heartbeat.py
===================
Esqueleto de la Fase 6 (motor de streaming) -- NO es el motor de trading
todavía. Es la prueba mínima, verificable, de que la cadena completa
"GitHub Actions dispara solo -> corre Python real -> produce un resultado
real" funciona sin depender de que Abraham abra el teléfono.

Qué SÍ hace hoy: llama a `core.monte_carlo.run_monte_carlo_validation()`
(código real, 22 tests, no un mock) sobre valores de PLACEHOLDER por
activo -- no hay todavía un adapter de Deriv para índices sintéticos
(`ingestion/deriv.py` no existe, ver BLUEPRINT.md Fase 6), así que no hay
precio ni volatilidad reales que pasarle. Imprime un resultado por activo,
con timestamp, a stdout -- visible en el log de cada corrida en la pestaña
Actions de GitHub.

Qué NO hace: no coloca órdenes, no lee `governance/secrets.py`, no toca
`execution/`. Cuando exista `ingestion/deriv.py`, este script (o su
sucesor) reemplaza los PLACEHOLDER_INPUTS por una llamada real al feed de
Deriv -- ese es el próximo paso real, no este.

Uso:
    python tools/heartbeat.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.monte_carlo import run_monte_carlo_validation  # noqa: E402

#: PLACEHOLDER -- no son precios ni volatilidades reales. Existen solo
#: para que la función tenga algo que procesar mientras
#: `ingestion/deriv.py` (Fase 6, pendiente) no exista. Reemplazar acá
#: cuando ese adapter esté listo, no antes.
PLACEHOLDER_INPUTS: dict[str, dict[str, float]] = {
    "BTC":     {"current_price": 60_000.0, "volatility": 0.35, "base_gold_score": 0.70},
    "XAU":     {"current_price": 2_400.0,  "volatility": 0.12, "base_gold_score": 0.70},
    "NVDA":    {"current_price": 900.0,    "volatility": 0.28, "base_gold_score": 0.70},
    "NIFTY50": {"current_price": 24_000.0, "volatility": 0.15, "base_gold_score": 0.70},
    "EURUSD":  {"current_price": 1.08,     "volatility": 0.07, "base_gold_score": 0.70},
}


def run_heartbeat() -> int:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[heartbeat] {ts} -- SPEL Fase 6 esqueleto, valores PLACEHOLDER (sin feed real todavía)")

    for asset, inputs in PLACEHOLDER_INPUTS.items():
        result = run_monte_carlo_validation(asset=asset, iterations=1000, **inputs)
        print(
            f"[heartbeat]   {asset:8s} mc_approved={result.mc_approved!s:5s} "
            f"success_rate={result.success_rate:.4f} "
            f"p5/p50/p95={result.p5_score:.4f}/{result.p50_score:.4f}/{result.p95_score:.4f}"
        )

    print(f"[heartbeat] {ts} -- corrida completa, {len(PLACEHOLDER_INPUTS)} activos procesados")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_heartbeat())
