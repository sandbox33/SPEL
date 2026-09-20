"""
tools/calibrar_umbral_entropia.py
===================================
Mide el umbral de entropía por activo desde la serie GDELT persistida, y lo
publica con todo lo necesario para reproducir la medición y para detectar
cuándo caducó.

Es el primer parámetro del sistema con procedencia. El registro de
constantes (config/constantes.json, PR #28) dejó a la vista que NINGÚN valor
numérico de SPEL tiene evidencia medida: 64 de 91 son
`provisional_sin_evidencia` y las que dicen `medido` miden comportamientos
de fuentes, no números. Este tool produce el primero que sí.

══ POR QUÉ POR ACTIVO Y NO UNO GLOBAL ══

No existe ni existió nunca un `p66_entropy_global_default`. La medición del
19-sep-2026 mostró que tampoco puede existir:

    BTC  p66 = 1,131801   (n = 4.880)
    XAU  p66 = 1,298946   (n = 4.879)

La diferencia de 0,167 no es una propiedad de los activos. Sale de
`CORE_COUNTRY_FILTERS["XAU"] = ()` -- sin filtro de país, XAU agrega el
dataset GDELT completo (~117k eventos/día contra ~45k de BTC), y la entropía
de Shannon crece con la riqueza del soporte: más eventos distintos, más bins
de tono poblados, más `H`.

**El umbral es por activo por construcción del filtro, no por naturaleza del
activo.** Buscar "el" default global es buscar algo que no puede existir
mientras los filtros sean distintos entre sí -- y el de XAU es vacío a
propósito, port literal de `gdelt_foundation.py::ASSET_COUNTRY_FILTERS`.

══ EL UMBRAL QUE ESTO PUBLICA NO ES EL UMBRAL DE PRODUCCIÓN ══

Hay que decirlo antes que nada porque es lo que alguien va a malinterpretar:
para un activo con historia suficiente, el umbral que manda es el de la
VENTANA MÓVIL de 252 días que calcula `compute_godel_p66()` en cada corrida,
y el valor de este archivo **nunca se lee**.

Se publica por dos motivos, ninguno de los cuales es "usarlo como umbral":

  1. RESPALDO DE ARRANQUE EN FRÍO. `run_scoring_cycle` exige
     `p66_entropy_global_default` sin default, y hoy el único número que
     circula para eso es un fixture de `tests/test_scoring.py` (1,19). Esto
     lo reemplaza por algo medido.
  2. EVIDENCIA DE LA DISTRIBUCIÓN. Saber que BTC y XAU difieren en 0,167 y
     por qué es un hecho del sistema que antes no estaba escrito en ningún
     lado.

Sin esta advertencia alguien lo va a cablear en seis meses.

══ CONTRATO ══

READ-ONLY POR DEFECTO. Sin `--write` imprime el reporte y no toca disco.
Con `--write` escribe `config/calibracion_activos.json` y nada más -- no
ingiere ni escribe un solo día de serie.

"NO HAY DATOS SUFICIENTES" ES UN RESULTADO, NO UN ERROR. Exit 0 siempre que
la medición se complete, con el veredicto como campo. Exit != 0 solo para
fallo real: ruta inexistente, archivo ilegible, excepción no controlada.
Ningún activo sin datos se rellena ni se interpola -- mismo criterio que
`tools/measure_godel_samples.py`, del que este tool copia el contrato.

NO REIMPLEMENTA EL PERCENTIL. Llama a `compute_adaptive_percentile()` con
`GODEL_MASK_PERCENTILE`, las mismas dos cosas que usa producción. Si el tool
calculara el percentil de otra manera mediría otra cosa -- que es
literalmente la advertencia que `measure_godel_samples.py` ya dejó escrita, y
el defecto que este repo tuvo cuando el tool medía un P90 contra una máscara
que operaba en P66. Hay un test que compara los dos valores sobre la misma
ventana.

DESCARTA LOS DÍAS SIN ENTROPÍA, igual que `orchestration/cycle.py::_build_windows`:
un día con `insufficient_events=True` trae `entropy_shannon=None` y no se
puede meter en un percentil. El `n` que se publica es el de días VÁLIDOS,
nunca el de filas -- publicar el de filas inflaría la confianza en la
medición con días que no aportaron ningún número.

Uso:
    python tools/calibrar_umbral_entropia.py                  # read-only
    python tools/calibrar_umbral_entropia.py --assets BTC XAU
    python tools/calibrar_umbral_entropia.py --format json
    python tools/calibrar_umbral_entropia.py --write          # escribe el JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scoring import (  # noqa: E402
    GODEL_MASK_PERCENTILE,
    GODEL_ROLLING_WINDOW_DAYS,
    MIN_OBS_FOR_ROLLING,
    compute_adaptive_percentile,
)
from ingestion.gdelt_series import read_series  # noqa: E402
from orchestration.cycle import DEFAULT_CYCLE_ASSETS  # noqa: E402

logger = logging.getLogger("spel.tools.calibrar_umbral_entropia")

#: Versión del script, que viaja en el JSON. Sube cuando cambia CÓMO se
#: mide -- una medición vieja y una nueva tienen que poder distinguirse sin
#: mirar el commit.
VERSION_SCRIPT = "1.0.0"

#: Esquema del archivo de salida.
SCHEMA_VERSION = "1.0.0"

#: Dónde se escribe con --write. Stream CONFIG: versionado en git, revisable
#: en un PR. Igual que config/constantes.json.
SALIDA_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "calibracion_activos.json"

#: Pisos del veredicto. `MEDIDO` es la ventana de producción: por debajo, el
#: umbral que se publica se calculó sobre menos historia de la que el motor
#: usa en régimen, y eso hay que decirlo en vez de publicarlo liso.
N_MINIMO_MEDIDO = GODEL_ROLLING_WINDOW_DAYS
#: El piso de `MIN_OBS_FOR_ROLLING` en core.scoring: por debajo de eso el
#: propio motor no confía en un percentil rolling. SE IMPORTA, no se repite
#: el literal -- la primera versión de este archivo escribía `100` con un
#: comentario que decía "se reusa", que es la clase de duplicación que se
#: separa sin que nadie lo note. Mismo arreglo que ROLLING_WINDOW_DEFAULT.
N_MINIMO_PARCIAL = MIN_OBS_FOR_ROLLING


class Estado:
    MEDIDO = "medido"
    PARCIAL = "parcial"
    INSUFICIENTE = "insuficiente"
    SIN_SERIE = "sin_serie"


@dataclass(frozen=True)
class CalibracionActivo:
    """Nunca un float pelado. `umbral_historia_completa` sin `n_validos` y
    sin `estado` es un número que no se puede juzgar: 1,13 sobre 4.880 días
    y 1,13 sobre 12 son cosas distintas y se ven iguales."""
    asset: str
    estado: str
    n_validos: int
    n_filas: int
    umbral_historia_completa: Optional[float] = None
    umbral_ultimos_252: Optional[float] = None
    rango: Optional[list[str]] = None
    sha256_serie: Optional[str] = None
    fuente_percentil: Optional[str] = None
    motivo: Optional[str] = None


@dataclass
class ReporteCalibracion:
    percentil: float
    medido_utc: str
    version_script: str
    commit: str
    activos: list[CalibracionActivo] = field(default_factory=list)
    zonas_grises: list[str] = field(default_factory=list)
    peticiones_admin: list[str] = field(default_factory=list)


def _commit_actual() -> str:
    """El commit contra el que se midió. Si no hay git, se dice -- no se
    inventa un hash ni se deja vacío en silencio."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent.parent)
        return out.stdout.strip() or "desconocido"
    except (OSError, subprocess.SubprocessError):
        return "desconocido"


def _sha256_serie(entropias: Sequence[float], dias: Sequence[str]) -> str:
    """Huella de la serie MEDIDA (días válidos y sus entropías), no del
    archivo. Es lo que permite saber después si una medición corresponde a
    la serie que hoy está en disco o a una anterior: sin esto el JSON
    envejece sin avisar.

    Se hashea el par (día, entropía) y no solo las entropías: dos series con
    los mismos valores en días distintos NO son la misma serie."""
    h = hashlib.sha256()
    for dia, e in zip(dias, entropias):
        h.update(f"{dia}:{e!r}\n".encode("utf-8"))
    return h.hexdigest()


def calibrar_activo(asset: str, *, global_default: float) -> CalibracionActivo:
    """
    Mide un activo. NUNCA lanza por falta de datos: devuelve el estado.

    `global_default` solo se usa para satisfacer la firma de
    `compute_adaptive_percentile` -- con historia suficiente la función no lo
    mira, y cuando sí lo miraría estamos en INSUFICIENTE y no publicamos
    umbral. Se pasa explícito igual, porque la función no tiene default a
    propósito.
    """
    serie = read_series(asset)
    if not serie:
        return CalibracionActivo(
            asset=asset, estado=Estado.SIN_SERIE, n_validos=0, n_filas=0,
            motivo=f"no hay serie persistida para {asset} en el stream METRICS.")

    validos = [r for r in serie if r.entropy_shannon is not None]
    n_filas, n_validos = len(serie), len(validos)

    if n_validos < N_MINIMO_PARCIAL:
        return CalibracionActivo(
            asset=asset, estado=Estado.INSUFICIENTE,
            n_validos=n_validos, n_filas=n_filas,
            rango=[str(serie[0].day), str(serie[-1].day)],
            motivo=(
                f"{n_validos} día(s) con entropía sobre {n_filas} fila(s): por "
                f"debajo de {N_MINIMO_PARCIAL}, que es el piso con que el propio "
                f"motor (MIN_OBS_FOR_ROLLING) deja de confiar en un percentil "
                f"rolling. No se publica umbral."))

    entropias = [r.entropy_shannon for r in validos]
    dias = [str(r.day) for r in validos]

    completa = compute_adaptive_percentile(
        history=entropias, percentile=GODEL_MASK_PERCENTILE,
        global_default=global_default)
    ultimos = compute_adaptive_percentile(
        history=entropias[-GODEL_ROLLING_WINDOW_DAYS:],
        percentile=GODEL_MASK_PERCENTILE, global_default=global_default)

    estado = Estado.MEDIDO if n_validos >= N_MINIMO_MEDIDO else Estado.PARCIAL
    motivo = None
    if estado == Estado.PARCIAL:
        motivo = (
            f"{n_validos} días válidos: alcanza para medir, pero es menos que "
            f"la ventana de {N_MINIMO_MEDIDO} que usa producción en régimen. "
            f"El umbral se publica MARCADO, no liso.")

    return CalibracionActivo(
        asset=asset, estado=estado, n_validos=n_validos, n_filas=n_filas,
        umbral_historia_completa=completa.value,
        umbral_ultimos_252=ultimos.value,
        rango=[dias[0], dias[-1]],
        sha256_serie=_sha256_serie(entropias, dias),
        fuente_percentil=completa.source.value
        if hasattr(completa.source, "value") else str(completa.source),
        motivo=motivo)


def calibrar(assets: Sequence[str], *, global_default: float) -> ReporteCalibracion:
    reporte = ReporteCalibracion(
        percentil=GODEL_MASK_PERCENTILE,
        medido_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        .replace("+00:00", "Z"),
        version_script=VERSION_SCRIPT,
        commit=_commit_actual())
    reporte.activos = [calibrar_activo(a, global_default=global_default)
                       for a in assets]
    reporte.zonas_grises = _zonas_grises(reporte.activos)
    reporte.peticiones_admin = _peticiones_admin(reporte.activos)
    return reporte


# ══════════════════════════════════════════════════════════════════════════
#  Zonas grises y peticiones al Admin -- SECCIONES FIJAS, no opcionales
# ══════════════════════════════════════════════════════════════════════════

def _zonas_grises(activos: Sequence[CalibracionActivo]) -> list[str]:
    """Qué no se pudo medir y POR QUÉ, con el número que lo respalda.

    Es una sección fija y no una lista que aparece cuando hay algo: un
    reporte que omite la sección cuando está vacía entrena a no buscarla, y
    el día que tenga contenido nadie la va a leer."""
    out = []
    for a in activos:
        if a.estado == Estado.MEDIDO:
            continue
        if a.estado == Estado.SIN_SERIE:
            out.append(f"{a.asset}: sin serie persistida en el stream METRICS "
                       f"(0 filas). Sin umbral.")
        elif a.estado == Estado.INSUFICIENTE:
            out.append(f"{a.asset}: {a.n_validos}/{a.n_filas} días con entropía "
                       f"-- por debajo de {N_MINIMO_PARCIAL}. Sin umbral.")
        elif a.estado == Estado.PARCIAL:
            out.append(f"{a.asset}: {a.n_validos} días válidos, menos que la "
                       f"ventana de producción ({N_MINIMO_MEDIDO}). Umbral "
                       f"publicado pero marcado `parcial`.")
    if not out:
        out.append("Ninguna: los activos pedidos se midieron con historia "
                   "suficiente.")
    return out


def _peticiones_admin(activos: Sequence[CalibracionActivo]) -> list[str]:
    """Decisiones que este tool NO puede tomar, con la evidencia que las
    motiva. Es la semilla del reporte que el sistema tiene que aprender a
    emitir solo: el tool mide y describe; elegir es de otro.

    Las dos de hoy están medidas y son estructurales -- ninguna se arregla
    ingiriendo más días."""
    from core.scoring import FX_GOBIERNO_ONLY_ASSETS, GOBIERNO_COUNTRY_FILTERS
    from ingestion.adapters import _DERIV_SYMBOL_MAP

    out = []

    fx = sorted(FX_GOBIERNO_ONLY_ASSETS)
    out.append(
        f"FILTRO GOBIERNO INSUFICIENTE PARA {', '.join(fx)}. "
        f"GOBIERNO_COUNTRY_FILTERS = {GOBIERNO_COUNTRY_FILTERS} no produce "
        f"suficientes eventos: los días medidos quedaron por debajo de "
        f"MIN_EVENTS_FOR_VALID_DAY tras filtrar, así que entran a la serie "
        f"con entropy_shannon=None y no cuentan. NINGÚN VOLUMEN DE INGESTA LO "
        f"ARREGLA -- el filtro es de dos países y el problema es el filtro. "
        f"Decisión de Admin: ampliar GOBIERNO_COUNTRY_FILTERS (y con qué "
        f"criterio), o aceptar que los pares FX no tienen máscara Gödel.")

    con_serie = sorted(a.asset for a in activos if a.estado != Estado.SIN_SERIE)
    en_deriv = sorted(set(_DERIV_SYMBOL_MAP) & set(a.asset for a in activos))
    sin_deriv = [a for a in con_serie if a not in _DERIV_SYMBOL_MAP]
    if sin_deriv or en_deriv:
        out.append(
            f"DESALINEACIÓN ENTRE LO MEDIBLE Y LO OPERABLE. Con serie GDELT: "
            f"{con_serie or 'ninguno'}. En _DERIV_SYMBOL_MAP (operables): "
            f"{en_deriv or 'ninguno'}. Los que tienen datos para calibrar no "
            f"son los que se pueden operar, y viceversa. Decisión de Admin: "
            f"agregar los símbolos que faltan al adapter, o aceptar que la "
            f"calibración cubre activos que hoy no se operan.")
    return out


# ══════════════════════════════════════════════════════════════════════════
#  Reporte y CLI
# ══════════════════════════════════════════════════════════════════════════

ADVERTENCIA = (
    "ESTE UMBRAL NO ES EL DE PRODUCCIÓN. Para un activo con historia "
    "suficiente manda la ventana móvil de 252 días que compute_godel_p66() "
    "recalcula en cada corrida, y este valor NUNCA se lee. Se publica como "
    "respaldo de arranque en frío y como evidencia de la distribución.")


def render_text(r: ReporteCalibracion, *, write: bool) -> str:
    out = [
        "═══ CALIBRACIÓN DEL UMBRAL DE ENTROPÍA POR ACTIVO ═══",
        ("MODO ESCRITURA -- se escribió config/calibracion_activos.json."
         if write else
         "READ-ONLY -- no se tocó disco. Usar --write para escribir."),
        f"percentil: {r.percentil}   commit: {r.commit}   "
        f"script: v{r.version_script}",
        f"medido: {r.medido_utc}",
        "",
        f"!! {ADVERTENCIA}",
        "",
    ]
    for a in r.activos:
        out.append(f"── {a.asset} " + "─" * max(0, 58 - len(a.asset)))
        out.append(f"  estado: {a.estado}   n_validos: {a.n_validos}   "
                   f"n_filas: {a.n_filas}")
        if a.umbral_historia_completa is not None:
            out.append(f"  umbral historia completa: {a.umbral_historia_completa:.6f}")
            out.append(f"  umbral últimos {GODEL_ROLLING_WINDOW_DAYS}:      "
                       f"{a.umbral_ultimos_252:.6f}")
            out.append(f"  rango: {a.rango[0]} .. {a.rango[1]}   "
                       f"fuente: {a.fuente_percentil}")
            out.append(f"  sha256: {a.sha256_serie[:16]}…")
        if a.motivo:
            out.append(f"  · {a.motivo}")
        out.append("")

    out.append("── ZONAS GRISES " + "─" * 46)
    out.extend(f"  · {z}" for z in r.zonas_grises)
    out.append("")
    out.append("── PETICIONES AL ADMIN " + "─" * 39)
    if r.peticiones_admin:
        for p in r.peticiones_admin:
            out.append(f"  · {p}")
    else:
        out.append("  · Ninguna.")
    return "\n".join(out)


def a_documento(r: ReporteCalibracion) -> dict:
    """La forma que se escribe a disco. `activos` va como objeto por activo
    y no como lista: el consumidor busca por nombre."""
    activos = {}
    for a in r.activos:
        d = {k: v for k, v in asdict(a).items()
             if k != "asset" and v is not None}
        activos[a.asset] = d
    return {
        "schema_version": SCHEMA_VERSION,
        "generado_por": "tools/calibrar_umbral_entropia.py",
        "version_script": r.version_script,
        "commit": r.commit,
        "medido_utc": r.medido_utc,
        "percentil": r.percentil,
        "advertencia": ADVERTENCIA,
        "activos": activos,
        "zonas_grises": r.zonas_grises,
        "peticiones_admin": r.peticiones_admin,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibrar_umbral_entropia",
        description="Mide el umbral de entropía por activo desde la serie "
                    "GDELT persistida. Read-only por defecto.")
    p.add_argument("--assets", nargs="+", default=list(DEFAULT_CYCLE_ASSETS),
                   help=f"Activos a calibrar. Default: "
                        f"{', '.join(DEFAULT_CYCLE_ASSETS)}.")
    p.add_argument("--global-default", type=float, default=1.0,
                   help="Solo para satisfacer la firma de "
                        "compute_adaptive_percentile. Con historia suficiente "
                        "no se usa, y sin ella no se publica umbral.")
    p.add_argument("--write", action="store_true",
                   help="Escribe config/calibracion_activos.json. SIN este "
                        "flag no se toca disco.")
    p.add_argument("--salida", default=None,
                   help="Ruta de salida alternativa. Solo con --write.")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.salida and not args.write:
        print("ERROR: --salida solo tiene sentido con --write.", file=sys.stderr)
        return 2

    reporte = calibrar(args.assets, global_default=args.global_default)

    if args.write:
        destino = Path(args.salida) if args.salida else SALIDA_DEFAULT
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(a_documento(reporte), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    if args.format == "json":
        print(json.dumps(a_documento(reporte), indent=2, ensure_ascii=False))
    else:
        print(render_text(reporte, write=args.write))

    # Exit 0 aunque no haya datos: "no hay datos suficientes" es un
    # resultado y viaja en `estado`. Ver el contrato en el docstring.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
