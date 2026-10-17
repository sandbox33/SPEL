"""
governance/ensayos.py
=====================
El registro único de ensayos (D3; brief del Admin 10, punto 3). Es la única
fuente del N del Deflated Sharpe: CLAUDE.md dice que cada variante probada
cuenta en el N, y antes ese número vivía escrito a mano en cada
pre-registro.

══ EL ARCHIVO ══

`governance/ensayos.jsonl`, una línea JSON por ensayo, con los campos de
CAMPOS_ENSAYO:

  · id                  único, nunca se reusa.
  · hipotesis           la hipótesis o la familia ("H1", "H-ORB-oro", o
                        HIPOTESIS_PREVIA_A_LA_SERIE_H para lo anterior).
  · fecha               ISO (AAAA-MM-DD): cuándo se midió.
  · preregistro_sha256  el sha256 del pre-registro que fijó el criterio, o
                        null si no hubo pre-registro.
  · activo              sobre qué activo(s) se midió.
  · estado              uno de ESTADOS_ENSAYO.
  · cuenta_en_n         si suma a un N de pruebas múltiples: al del DSR si
                        además es una estrategia (P3, abajo); si no, al de su
                        propia familia de pruebas.
  · fuente              dónde está la evidencia (decision-log, run, commit).
  · nota                texto libre.

══ APPEND-ONLY ══

Nunca se edita ni se borra una línea: un ensayo que cambia de estado suma
una línea nueva con otro id que lo cite. `agregar` solo abre el archivo en
modo "a", y tests/test_ensayos.py fija el sha256 de lo sembrado.

══ ESTRATEGIAS (P3) ══

Al N del DSR de una estrategia suman los ensayos que cuentan y son
estrategias, los de `que_cuentan_en_el_dsr`: prueban un Sharpe
seleccionado. Los de H2 no suman: prueban un predictor de volatilidad con
Diebold–Mariano y MCS, y cuentan en el N de su propia familia de pruebas
(research/preregistro_h2.md, sección 7). Lo decidió el auditor el
17-oct-2026 (P3, brief del Admin 11–13-B).

La clase sale de la hipótesis. Una que cuenta y no está en
HIPOTESIS_DE_ESTRATEGIA ni en HIPOTESIS_QUE_NO_SON_ESTRATEGIA hace fallar el
conteo: nadie entra ni sale del N por olvido. H-ORB-oro no está en ninguna:
su única línea no cuenta, y se clasifica cuando se reabra.

══ LO QUE NO ES UN ENSAYO ══

La concordancia TwelveData–Deriv del oro (14-oct-2026) está en el registro
con `cuenta_en_n: false`: compara dos fuentes de datos del mismo activo, no
prueba una estrategia, y no infla el N de nadie. El registro lo dice en su
nota.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

#: El registro. Vive junto a este módulo, en el repo.
RUTA_ENSAYOS = Path(__file__).resolve().parent / "ensayos.jsonl"

#: Los campos de una línea, en el orden en que se escriben.
CAMPOS_ENSAYO: tuple[str, ...] = (
    "id", "hipotesis", "fecha", "preregistro_sha256", "activo", "estado",
    "cuenta_en_n", "fuente", "nota")

#: Los estados posibles de un ensayo.
ESTADOS_ENSAYO = frozenset({"pre-registrado", "en_curso", "fallido", "aprobado",
                            "compuerta_roja"})

#: La familia de los ensayos anteriores a la Serie H, los que
#: research/preregistro_h1.md §9 suma como "ensayos previos".
HIPOTESIS_PREVIA_A_LA_SERIE_H = "previa a la Serie H"

#: P3: las hipótesis cuyos ensayos son estrategias, y suman al N del DSR.
HIPOTESIS_DE_ESTRATEGIA = frozenset({HIPOTESIS_PREVIA_A_LA_SERIE_H, "H1", "H3"})

#: P3: las hipótesis cuyos ensayos no son estrategias. H2 prueba un predictor
#: de volatilidad, no un Sharpe seleccionado.
HIPOTESIS_QUE_NO_SON_ESTRATEGIA = frozenset({"H2"})

#: D11-u: H-ORB-oro se reabre cuando haya esta cantidad de días hábiles de
#: velas M5 de Deriv (brief del Admin 10).
DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO = 756

def _texto(e: dict, campo: str) -> None:
    if not isinstance(e[campo], str) or not e[campo].strip():
        raise ValueError(f"{campo}: hace falta un texto, no {e[campo]!r}")


def validar(e: Any) -> dict:
    """Devuelve la entrada si tiene exactamente los campos y valores
    admitidos; si no, lanza ValueError diciendo cuál falla."""
    if not isinstance(e, dict):
        raise ValueError(f"una entrada es un objeto JSON, no {type(e).__name__}")
    if set(e) != set(CAMPOS_ENSAYO):
        faltan, sobran = set(CAMPOS_ENSAYO) - set(e), set(e) - set(CAMPOS_ENSAYO)
        raise ValueError(f"campos: faltan {sorted(faltan)}, sobran {sorted(sobran)}")
    for campo in ("id", "hipotesis", "activo", "fuente"):
        _texto(e, campo)
    if not isinstance(e["nota"], str):
        raise ValueError(f"nota: hace falta un texto, no {e['nota']!r}")
    try:
        date.fromisoformat(e["fecha"])
    except (TypeError, ValueError):
        raise ValueError(f"fecha={e['fecha']!r}: no es AAAA-MM-DD") from None
    sha = e["preregistro_sha256"]
    if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise ValueError(f"preregistro_sha256={sha!r}: 64 hexadecimales o null")
    if e["estado"] not in ESTADOS_ENSAYO:
        raise ValueError(f"estado={e['estado']!r}: uno de {sorted(ESTADOS_ENSAYO)}")
    if not isinstance(e["cuenta_en_n"], bool):
        raise ValueError(f"cuenta_en_n={e['cuenta_en_n']!r}: true o false")
    return e


def leer(ruta: Optional[Path] = None) -> list[dict]:
    """Todas las entradas, validadas y en el orden del archivo. Lanza si una
    línea no es JSON, no valida, o repite un id."""
    ruta = Path(ruta) if ruta is not None else RUTA_ENSAYOS
    entradas, vistos = [], set()
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), start=1):
        if not linea.strip():
            continue
        try:
            e = validar(json.loads(linea))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{ruta.name}:{n}: {exc}") from exc
        if e["id"] in vistos:
            raise ValueError(f"{ruta.name}:{n}: id repetido {e['id']!r}")
        vistos.add(e["id"])
        entradas.append(e)
    return entradas


def agregar(entrada: dict, ruta: Optional[Path] = None) -> dict:
    """Agrega UNA línea al final. Nunca reescribe: valida la entrada y que
    su id sea nuevo antes de abrir el archivo en modo "a"."""
    ruta = Path(ruta) if ruta is not None else RUTA_ENSAYOS
    validar(entrada)
    previas = leer(ruta) if ruta.exists() else []
    if any(p["id"] == entrada["id"] for p in previas):
        raise ValueError(f"id repetido {entrada['id']!r}: el registro es append-only")
    linea = json.dumps({c: entrada[c] for c in CAMPOS_ENSAYO}, ensure_ascii=False)
    with ruta.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")
    return entrada


def que_cuentan(ruta: Optional[Path] = None) -> list[dict]:
    """Los ensayos que suman a algún N: al del DSR los que son estrategias
    (`que_cuentan_en_el_dsr`), y al de su propia familia los demás (P3)."""
    return [e for e in leer(ruta) if e["cuenta_en_n"]]


def es_estrategia(e: dict) -> bool:
    """P3: si el ensayo prueba una estrategia, según su hipótesis. Una
    hipótesis sin clasificar lanza ValueError."""
    h = e["hipotesis"]
    if h in HIPOTESIS_DE_ESTRATEGIA:
        return True
    if h in HIPOTESIS_QUE_NO_SON_ESTRATEGIA:
        return False
    raise ValueError(f"hipotesis={h!r}: sin clasificar como estrategia o no (P3)")


def que_cuentan_en_el_dsr(ruta: Optional[Path] = None) -> list[dict]:
    """Los ensayos que suman al N del DSR de una estrategia (P3): los que
    cuentan y son estrategias.

    Devuelve líneas, no ensayos. Un ensayo que cambia de estado suma una
    línea nueva (ver APPEND-ONLY), y el registro todavía no dice cuál
    reemplaza a cuál: contar líneas lo contaría dos veces. Hoy ninguno
    cambió de estado."""
    return [e for e in que_cuentan(ruta) if es_estrategia(e)]


def previos_fallidos(ruta: Optional[Path] = None) -> int:
    """Los ensayos fallidos anteriores a la Serie H que cuentan en el N: los
    "3 ensayos previos" de research/preregistro_h1.md §9."""
    return sum(1 for e in que_cuentan(ruta)
               if e["hipotesis"] == HIPOTESIS_PREVIA_A_LA_SERIE_H and e["estado"] == "fallido")


def h_orb_oro_se_puede_reabrir(dias_habiles_m5_deriv: int) -> bool:
    """D11-u: H-ORB-oro, pausada por D11, se reabre con al menos
    DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO días hábiles de M5 de
    Deriv."""
    if isinstance(dias_habiles_m5_deriv, bool) or not isinstance(dias_habiles_m5_deriv, int) \
            or dias_habiles_m5_deriv < 0:
        raise ValueError(f"dias_habiles_m5_deriv={dias_habiles_m5_deriv!r}: un conteo de días")
    return dias_habiles_m5_deriv >= DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO
