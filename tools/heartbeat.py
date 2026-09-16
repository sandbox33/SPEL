"""
tools/heartbeat.py
===================
Esqueleto de la Fase 6 (motor de streaming) -- NO es el motor de trading
todavía. Es la prueba mínima, verificable, de que la cadena completa
"GitHub Actions dispara solo -> corre Python real -> produce un resultado
real" funciona sin depender de que Abraham abra el teléfono.

Qué SÍ hace hoy: llama a `core.monte_carlo.run_monte_carlo_validation()`
(código real, 22 tests, no un mock) sobre valores SINTÉTICOS por activo --
no hay todavía un adapter de Deriv para índices sintéticos
(`ingestion/deriv.py` no existe, ver BLUEPRINT.md Fase 6), así que no hay
precio ni volatilidad reales que pasarle. Imprime un resultado por activo,
con timestamp, a stdout -- visible en el log de cada corrida en la pestaña
Actions de GitHub.

Qué NO hace: no coloca órdenes, no lee `governance/secrets.py`, no toca
`execution/`.

══ POR QUÉ ESTE ARCHIVO NO SE ARCHIVA, aunque lo parezca ══

Se auditó el 9-sep-2026 como candidato a retiro (era el único módulo del
repo sin tests) y la conclusión fue la contraria. Queda escrito acá porque
es lo único que impide que el próximo que lo audite lo archive por parecer
inútil:

  1. `.github/workflows/heartbeat.yml` es el ÚNICO andamiaje de `schedule:`
     probado del repo. Su bloque `cron` está comentado a propósito, pero el
     resto del workflow ya funciona: checkout, setup-python, install, correr
     un script. Los otros dos workflows (`tests.yml`, `live-tests.yml`)
     corren pytest, no un entry point de negocio.
  2. El próximo paso concreto que fijó ESTADO.md es escribir el entry point
     de GDELT que falta (`ingestion/run_gdelt.py`, que un comentario de
     `tests.yml` da por existente y no existe) y decidir si un `schedule:`
     lo dispara. **La plantilla de ese workflow es este.** Archivar heartbeat
     borraría el molde una semana antes de necesitarlo.
  3. `core/monte_carlo.py` NO queda huérfano si esto desaparece, pero
     tampoco al revés: BLUEPRINT.md (Hallazgo 7) lo nombra como pieza
     designada, y su propio docstring dice que audita `gold_score_bma()`
     DESPUÉS de calculado -- consumidor que existe desde el PR #19. Este
     script es su primer llamador, no su única razón de ser.

══ LOS VALORES SON SINTÉTICOS, Y SE DICE EN CADA LÍNEA ══

`SYNTHETIC_INPUTS` no son precios ni volatilidades reales. El modo
sintético es el DEFAULT (`--dry-run`), no una opción que haya que
acordarse de activar: invertir ese default es lo que hace que el modo
honesto no dependa de la memoria de nadie.

Cada línea de resultado lleva su propia marca `[SINTÉTICO]`, no solo el
encabezado -- alguien que lea el log de Actions puede copiar una línea
suelta, y una línea suelta sin marca se lee como un dato.

NO sale con código distinto de 0 por correr sintético. El legacy
(`spel_orchestrator_v10.py`, líneas 577-604) sí marca el job en rojo
cuando detecta un placeholder, pero ahí se trata de SECRETOS ausentes,
que son un fallo real. Acá los sintéticos son el estado esperado y
declarado; un rojo permanente en Actions entrena a ignorar el rojo.

══ EL VEREDICTO DEL MONTE CARLO ES DEGENERADO, Y NO SE MAQUILLA ══

Hallazgo del 9-sep, medido: con `base_gold_score=0.70` y
`SUCCESS_SCORE_THRESHOLD=0.85`, la dispersión GBM a 15 minutos mueve el
score simulado ±0.0003. Ninguna trayectoria cruza el umbral, así que
`success_rate` sale 0.0000 y `mc_approved` sale False **para los cinco
activos, siempre**. El heartbeat viene imprimiendo el mismo veredicto
desde que se escribió.

Eso NO invalida su función: la prueba de vida es que la cadena corre y
produce números reales, y eso sigue siendo cierto. Lo que no hay que
leerle es contenido: el veredicto no varía porque no puede.

No se "arregla" subiendo `base_gold_score` a 0.85-0.90 para que aparezca
algún True. Ese número no saldría de ninguna medición -- sería elegirlo
para que la salida se vea interesante, que es exactamente lo que este
proyecto no hace. Cuando exista `ingestion/deriv.py`, el
`base_gold_score` va a venir de `compute_gold_score_bma()` y el veredicto
va a significar algo. Hay un test que fija esta degeneración para que sea
visible en la suite y no una sorpresa.

Uso:
    python tools/heartbeat.py              # sintético (default)
    python tools/heartbeat.py --seed 42    # sintético, otra semilla
    python tools/heartbeat.py --real       # exit 2: no existe el feed
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.monte_carlo import run_monte_carlo_validation  # noqa: E402

#: Marca que acompaña a CADA línea de resultado. Ver el docstring: el
#: encabezado solo no alcanza porque las líneas se leen sueltas.
SYNTHETIC_MARK = "[SINTÉTICO]"

#: Semilla por defecto. Fijarla hace que dos corridas con los mismos
#: inputs den el mismo resultado, que es lo que separa "el heartbeat
#: cambió porque el código cambió" de "cambió porque es aleatorio".
#: `run_monte_carlo_validation` acepta `seed` desde que se portó; este
#: script no lo pasaba, y por eso su salida era irreproducible.
DEFAULT_SEED = 20260909

#: SINTÉTICOS -- no son precios ni volatilidades reales. Existen solo para
#: que la función tenga algo que procesar mientras `ingestion/deriv.py`
#: (Fase 6, pendiente) no exista. Reemplazar acá cuando ese adapter esté
#: listo, no antes.
#:
#: Se llamaban PLACEHOLDER_INPUTS. El nombre nuevo dice lo mismo sin
#: sugerir que el valor daría igual: un placeholder se reemplaza por
#: cualquier cosa, un sintético tiene que ser al menos plausible para que
#: la función no lance.
#:
#: Plausible NO es informativo: con estos valores el veredicto del Monte
#: Carlo es constante (ver el hallazgo en el docstring del módulo). Se
#: dejan como están a propósito -- moverlos para que el veredicto varíe
#: sería elegir un número por su efecto en la salida.
SYNTHETIC_INPUTS: dict[str, dict[str, float]] = {
    "BTC":     {"current_price": 60_000.0, "volatility": 0.35, "base_gold_score": 0.70},
    "XAU":     {"current_price": 2_400.0,  "volatility": 0.12, "base_gold_score": 0.70},
    "NVDA":    {"current_price": 900.0,    "volatility": 0.28, "base_gold_score": 0.70},
    "NIFTY50": {"current_price": 24_000.0, "volatility": 0.15, "base_gold_score": 0.70},
    "EURUSD":  {"current_price": 1.08,     "volatility": 0.07, "base_gold_score": 0.70},
}

#: Por qué `--real` no funciona todavía, en las palabras exactas que el
#: usuario ve. Nombra el archivo que falta, no un "no implementado".
REAL_MODE_BLOCKED_REASON = (
    "--real no está disponible: no existe ingestion/deriv.py, que es la "
    "fuente de precio y volatilidad para los índices sintéticos de Deriv "
    "(BLUEPRINT.md, Fase 6). Sin ese adapter no hay dato real que pasarle "
    "a run_monte_carlo_validation, y este script no inventa uno. Corre sin "
    "--real para el modo sintético."
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Heartbeat de SPEL -- prueba de vida de la cadena "
                    "Actions -> Python -> resultado. Modo sintético por "
                    "defecto.",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Modo sintético. ES EL DEFAULT y está acá para poder pedirlo "
             "explícitamente; no hace falta pasarlo.",
    )
    p.add_argument(
        "--real", action="store_true",
        help="Usar el feed real. Reservado para cuando exista "
             "ingestion/deriv.py; hoy sale con código 2 y dice por qué.",
    )
    p.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"Semilla del Monte Carlo. Default {DEFAULT_SEED}. Fijarla "
             f"hace la salida reproducible entre corridas.",
    )
    return p


def run_heartbeat(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.real:
        print(f"ERROR: {REAL_MODE_BLOCKED_REASON}", file=sys.stderr)
        return 2

    ts = datetime.now(timezone.utc).isoformat()
    print(f"[heartbeat] {ts} -- SPEL Fase 6 esqueleto, valores {SYNTHETIC_MARK} "
          f"(sin feed real todavía), seed={args.seed}")

    for asset, inputs in SYNTHETIC_INPUTS.items():
        result = run_monte_carlo_validation(
            asset=asset, iterations=1000, seed=args.seed, **inputs
        )
        print(
            f"[heartbeat]   {SYNTHETIC_MARK} {asset:8s} "
            f"mc_approved={result.mc_approved!s:5s} "
            f"success_rate={result.success_rate:.4f} "
            f"p5/p50/p95={result.p5_score:.4f}/{result.p50_score:.4f}/{result.p95_score:.4f}"
        )

    print(f"[heartbeat] {ts} -- corrida completa, {len(SYNTHETIC_INPUTS)} "
          f"activos procesados, todos {SYNTHETIC_MARK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_heartbeat())
