"""
tools/import_gdelt_entropy.py
==============================
Importa la entropía GDELT histórica desde parquet a la serie canónica JSONL.

EL HUECO QUE CIERRA. `tools/measure_godel_samples.py` lee la serie GDELT con
`read_series()`, que espera JSONL en `drive_root()/metrics/gdelt_series/{asset}.jsonl`.
La entropía histórica del proyecto vive como parquet en Drive y nunca se
importó a esa ruta. Y `read_series()` devuelve lista vacía si el archivo no
existe — no lanza. O sea: sin este import, la medición reporta
`gdelt_days=0` y un solapamiento nulo, y eso se lee como un resultado válido
en vez de como "faltan los datos". Un cero que parece medición es peor que
un error.

RELACIÓN CON `tools/audit_data_lake.py`. Aquel inventaría estos mismos
parquets y es read-only POR DISEÑO ("Este módulo no arregla nada. Reporta").
Este tool es el paso de escritura que aquel deliberadamente no hace. No se
duplica su lógica: se reusan sus helpers de lectura y normalización de fecha
(`data_lake_root`, `_to_iso_date`, `_parse_iso`), ya auditados contra los
archivos reales.

  NOTA DE ACOPLAMIENTO: `_to_iso_date` y `_parse_iso` son privados de aquel
  módulo. Se importan igual porque duplicarlos sería peor —serían dos
  normalizaciones de fecha que pueden divergir, que es exactamente la clase
  de defecto que originó todo este trabajo—. Si aparece un tercer consumidor,
  el paso correcto es moverlos a un módulo común, no copiarlos otra vez.

SE ESCRIBE POR LA API PÚBLICA, NUNCA FORMATEANDO JSONL A MANO. `append_day()`
es el único camino de escritura. Formatear la línea acá duplicaría
`_result_to_line()` y crearía dos formatos que pueden divergir en silencio.

DRY-RUN POR DEFECTO. Sin `--write` no se toca ningún archivo: se reporta qué
se escribiría. Un import que escribe por omisión es un import que corrompe
por omisión.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

# Mismo idiom que los otros tools del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.gdelt_aggregation import (  # noqa: E402
    MIN_EVENTS_FOR_VALID_DAY,
    DailyAggregationResult,
)
from ingestion.gdelt_series import append_day, read_series  # noqa: E402
from tools.audit_data_lake import (  # noqa: E402
    _parse_iso,
    _to_iso_date,
    data_lake_root,
)

logger = logging.getLogger("spel.tools.import_gdelt_entropy")

#: Patrón por defecto del archivo de origen.
PATRON_DEFAULT = "{asset}_gdelt_entropy.parquet"

#: Columnas del origen que mapean directo al destino (7 de 8; `day` se deriva
#: de `date` y `insufficient_events` de `n_events`).
_COLUMNAS_DIRECTAS = (
    "asset", "entropy_shannon", "zipf_concentration",
    "goldstein_mean", "tone_variance", "n_events",
)

#: Columnas que el origen trae y el destino NO tiene. No se inventan en
#: destino ni se descartan en silencio: se cuenta cuántas filas las traían y
#: se reporta. `vitality_tesla` en particular lo RECALCULA el consumidor
#: (tools/measure_godel_samples.py, de forma causal por día), así que
#: arrastrar el valor del parquet sería introducir un segundo origen de
#: verdad para el mismo dato.
_COLUMNAS_SOBRANTES = ("nash_frozen_7d", "vitality_tesla")

#: Archivos que NUNCA son entrada, aunque un patrón los alcance.
#: Verificado: los `*_2026_entropy.parquet` traen 2 columnas contra 9 del
#: resto, sin timezone, y NIFTY50_2026 arranca en 2015-10-17 pese a lo que
#: dice su nombre. No son una versión más nueva de nada — están corruptos.
#: El guardián es explícito y no depende del patrón: si alguien pasa un
#: `--pattern` más laxo, el archivo sigue quedando afuera.
_EXCLUIDOS_SUFIJO = ("_2026_entropy.parquet",)


@dataclass(frozen=True)
class AssetImport:
    """Lo que se importaría (o se importó) para un activo."""
    asset: str
    source_path: Optional[str] = None
    rows_read: int = 0
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    #: Días ya presentes en la serie canónica. No se reescriben: `append_day`
    #: es append puro y volver a escribirlos duplicaría líneas.
    already_present: int = 0
    to_write: int = 0
    written: int = 0
    #: Filas con n_events < MIN_EVENTS_FOR_VALID_DAY. Se importan con
    #: insufficient_events=True y las 4 señales en None — ver
    #: `_a_resultado()` para por qué se nulan en vez de arrastrarse.
    insufficient_rows: int = 0
    signals_nulled: int = 0
    #: Filas que traían cada columna sobrante. Se reporta, no se descarta en
    #: silencio.
    dropped_fields: dict[str, int] = field(default_factory=dict)
    #: Huecos de calendario dentro del rango. Son DATO, no error: no se
    #: rellenan ni se interpolan.
    calendar_days: int = 0
    missing_days: int = 0
    missing_runs: list[str] = field(default_factory=list)
    unexpected_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    status: str = "OK"
    notes: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
#  Fecha: conversión EXPLÍCITA a día UTC
# ══════════════════════════════════════════════════════════════════════════

def a_dia_utc(valor: Any) -> Optional[date]:
    """
    Convierte el `date` tz-aware UTC del parquet al `date` plano que espera
    `DailyAggregationResult.day`.

    LA CONVERSIÓN ES EXPLÍCITA A PROPÓSITO. Tomar `.date()` de un timestamp
    tz-aware devuelve el día EN SU ZONA, no en UTC. Con una zona al oeste,
    `2015-01-01 23:00-05:00` es `2015-01-02 04:00Z`: el día correcto es el 2,
    y un `.date()` ingenuo daría el 1. Un corrimiento de un día desalinea el
    join OHLCV↔GDELT entero y nadie lo nota hasta que el modelo no aprende.
    Por eso: si viene con zona, se pasa a UTC primero; recién después se toma
    el día.

    Para las formas sin zona se delega en el `_to_iso_date` de
    `audit_data_lake`, ya auditado contra los parquets reales.
    """
    if valor is None:
        return None

    # pandas.Timestamp y datetime exponen tzinfo; numpy.datetime64 no tiene
    # zona por construcción y cae al camino delegado.
    tz = getattr(valor, "tzinfo", None)
    if tz is not None and isinstance(valor, datetime):
        return valor.astimezone(timezone.utc).date()

    return _parse_iso(_to_iso_date(valor))


# ══════════════════════════════════════════════════════════════════════════
#  Lectura del parquet
# ══════════════════════════════════════════════════════════════════════════

def es_archivo_excluido(path: Path) -> bool:
    """Guardián independiente del patrón — ver `_EXCLUIDOS_SUFIJO`."""
    return any(path.name.endswith(s) for s in _EXCLUIDOS_SUFIJO)


def leer_parquet(path: Path) -> list[dict]:
    """
    Lee el parquet a una lista de dicts. pyarrow se importa de forma
    perezosa, igual que en `tools/audit_data_lake.py`: no está en
    `requirements.txt` (el motor no usa Parquet) y importarlo arriba rompería
    este módulo donde no esté instalado.
    """
    import pyarrow.parquet as pq  # lazy: ver docstring
    return pq.read_table(path).to_pylist()


def _rachas(faltantes: Sequence[date]) -> list[str]:
    """Agrupa días faltantes en rachas contiguas, para que 18 días seguidos
    se lean como un hueco y no como 18 hallazgos sueltos."""
    if not faltantes:
        return []
    rachas: list[str] = []
    inicio = anterior = faltantes[0]
    for d in faltantes[1:]:
        if (d - anterior).days == 1:
            anterior = d
            continue
        rachas.append(_fmt_racha(inicio, anterior))
        inicio = anterior = d
    rachas.append(_fmt_racha(inicio, anterior))
    return rachas


def _fmt_racha(a: date, b: date) -> str:
    return str(a) if a == b else f"{a}..{b} ({(b - a).days + 1} días)"


# ══════════════════════════════════════════════════════════════════════════
#  Mapeo origen -> destino
# ══════════════════════════════════════════════════════════════════════════

def _a_resultado(fila: dict, dia: date, asset: str) -> tuple[DailyAggregationResult, bool]:
    """
    Traduce una fila del parquet a `DailyAggregationResult`.

    Devuelve (resultado, se_nularon_señales).

    POR QUÉ SE NULAN LAS SEÑALES CUANDO `insufficient_events=True`. El
    productor canónico (`gdelt_aggregation.py:143-148`) devuelve las cuatro
    señales en `None` cuando `n_events < MIN_EVENTS_FOR_VALID_DAY`, y el
    docstring de la dataclass lo dice explícito: "el campo bool manda, no un
    valor 0.0 disfrazado de dato real". Los consumidores están escritos sobre
    ese invariante — `orchestration/cycle.py::_build_windows` filtra por
    `entropy_shannon is not None`, así que arrastrar el valor del parquet
    haría que esos días contaran como válidos mientras la bandera dice lo
    contrario. Producir un registro que el productor canónico nunca produce
    es precisamente el tipo de inconsistencia silenciosa que este proyecto
    viene corrigiendo.

    No se descarta en silencio: `signals_nulled` cuenta cuántas filas
    perdieron valores por esta regla y el reporte lo muestra.
    """
    n_events = int(fila.get("n_events") or 0)
    insuficiente = n_events < MIN_EVENTS_FOR_VALID_DAY

    def señal(nombre: str) -> Optional[float]:
        valor = fila.get(nombre)
        return None if valor is None else float(valor)

    traia_señales = any(fila.get(c) is not None for c in
                        ("entropy_shannon", "zipf_concentration",
                         "goldstein_mean", "tone_variance"))

    if insuficiente:
        return DailyAggregationResult(
            day=dia, asset=asset,
            entropy_shannon=None, zipf_concentration=None,
            goldstein_mean=None, tone_variance=None,
            n_events=n_events, insufficient_events=True,
        ), traia_señales

    return DailyAggregationResult(
        day=dia, asset=asset,
        entropy_shannon=señal("entropy_shannon"),
        zipf_concentration=señal("zipf_concentration"),
        goldstein_mean=señal("goldstein_mean"),
        tone_variance=señal("tone_variance"),
        n_events=n_events, insufficient_events=False,
    ), False


# ══════════════════════════════════════════════════════════════════════════
#  Import por activo
# ══════════════════════════════════════════════════════════════════════════

def importar_asset(
    asset: str, *, lake_root: Path, patron: str, write: bool,
) -> AssetImport:
    """
    Importa un activo. Con `write=False` (default) no toca ningún archivo.

    IDEMPOTENCIA — el caso que encontró la auditoría es MIXTO, y por eso el
    tool hace lo que hace:
      · `append_day()` es append PURO y no deduplica. Su docstring lo dice y
        delega en el lector: "nunca deduplica ni ordena (eso es
        responsabilidad de read_series() al leer)".
      · `read_series()` SÍ deduplica por `day`, última ocurrencia gana.
    O sea: correr el import dos veces ya era idempotente SEMÁNTICAMENTE (la
    serie leída es la misma), pero NO físicamente — el JSONL duplicaba su
    tamaño y cada lectura posterior pagaba ese costo.
    La solución no reimplementa deduplicación (contradiría el contrato
    documentado de `read_series`): se consulta la serie existente por la API
    pública y solo se agregan los días que faltan.
    """
    notas: list[str] = []
    nombre = patron.format(asset=asset)
    path = lake_root / nombre

    if es_archivo_excluido(path):
        return AssetImport(asset, status="EXCLUIDO", source_path=str(path),
                           notes=[f"{path.name} está en la lista de excluidos: "
                                  f"corrupto verificado, no es entrada de nada."])
    if not path.is_file():
        return AssetImport(asset, status="SIN_ORIGEN", source_path=str(path),
                           notes=[f"No existe {path}. Revisar --lake-root o "
                                  f"--pattern."])

    filas = leer_parquet(path)
    if not filas:
        return AssetImport(asset, status="VACIO", source_path=str(path),
                           notes=["El parquet no tiene filas."])

    columnas = sorted({c for f in filas for c in f})
    esperadas = set(_COLUMNAS_DIRECTAS) | {"date"} | set(_COLUMNAS_SOBRANTES)
    faltan_col = sorted(c for c in ("date", "n_events") if c not in columnas)
    if faltan_col:
        return AssetImport(
            asset, status="ESQUEMA_INCOMPATIBLE", source_path=str(path),
            rows_read=len(filas), missing_columns=faltan_col,
            unexpected_columns=sorted(set(columnas) - esperadas),
            notes=[f"Faltan columnas imprescindibles: {faltan_col}. "
                   f"Columnas presentes: {columnas}"],
        )

    descartados = {
        c: sum(1 for f in filas if f.get(c) is not None)
        for c in _COLUMNAS_SOBRANTES if c in columnas
    }

    ya_presentes = {r.day for r in read_series(asset)}

    resultados: list[DailyAggregationResult] = []
    dias: list[date] = []
    sin_fecha = 0
    insuficientes = nuladas = 0

    for fila in filas:
        dia = a_dia_utc(fila.get("date"))
        if dia is None:
            sin_fecha += 1
            continue
        dias.append(dia)
        resultado, se_nulo = _a_resultado(fila, dia, asset)
        if resultado.insufficient_events:
            insuficientes += 1
            if se_nulo:
                nuladas += 1
        resultados.append(resultado)

    if sin_fecha:
        notas.append(f"{sin_fecha} fila(s) sin fecha legible: se saltearon. "
                     f"No se inventó una fecha para ellas.")

    dias_ordenados = sorted(set(dias))
    calendario = 0
    faltantes: list[date] = []
    if dias_ordenados:
        primero, ultimo = dias_ordenados[0], dias_ordenados[-1]
        calendario = (ultimo - primero).days + 1
        presentes = set(dias_ordenados)
        faltantes = [primero + timedelta(days=k) for k in range(calendario)
                     if primero + timedelta(days=k) not in presentes]

    pendientes = [r for r in resultados if r.day not in ya_presentes]
    escritos = 0
    if write:
        for r in pendientes:
            append_day(r)   # única vía de escritura: la API pública
            escritos += 1

    return AssetImport(
        asset=asset, source_path=str(path), rows_read=len(filas),
        first_date=str(dias_ordenados[0]) if dias_ordenados else None,
        last_date=str(dias_ordenados[-1]) if dias_ordenados else None,
        already_present=sum(1 for r in resultados if r.day in ya_presentes),
        to_write=len(pendientes), written=escritos,
        insufficient_rows=insuficientes, signals_nulled=nuladas,
        dropped_fields=descartados,
        calendar_days=calendario, missing_days=len(faltantes),
        missing_runs=_rachas(faltantes),
        unexpected_columns=sorted(set(columnas) - esperadas),
        missing_columns=[],
        status="ESCRITO" if write else "DRY_RUN", notes=notas,
    )


# ══════════════════════════════════════════════════════════════════════════
#  Reporte
# ══════════════════════════════════════════════════════════════════════════

def render_text(imports: Sequence[AssetImport], *, write: bool) -> str:
    out = [
        "═══ IMPORT DE ENTROPÍA GDELT: parquet → serie canónica ═══",
        ("MODO ESCRITURA — se escribió en la serie canónica."
         if write else
         "DRY-RUN — no se tocó ningún archivo. Usar --write para escribir."),
        "",
    ]
    for m in imports:
        out.append(f"── {m.asset} " + "─" * max(0, 58 - len(m.asset)))
        out.append(f"  estado: {m.status}")
        if m.source_path:
            out.append(f"  origen: {m.source_path}")
        if m.status in ("DRY_RUN", "ESCRITO"):
            out.append(f"  filas leídas: {m.rows_read}   "
                       f"rango: {m.first_date} .. {m.last_date}")
            out.append(f"  ya presentes en la serie: {m.already_present}   "
                       f"a escribir: {m.to_write}   escritas: {m.written}")
            out.append(f"  calendario del rango: {m.calendar_days} días   "
                       f"faltantes: {m.missing_days}")
            for racha in m.missing_runs:
                out.append(f"      hueco: {racha}")
            if m.insufficient_rows:
                out.append(f"  n_events < {MIN_EVENTS_FOR_VALID_DAY}: "
                           f"{m.insufficient_rows} fila(s) → insufficient_events=True"
                           f" ({m.signals_nulled} con señales nuladas)")
            for campo, cuantas in sorted(m.dropped_fields.items()):
                out.append(f"  campo del origen sin destino: {campo} "
                           f"({cuantas} fila(s) lo traían) — el consumidor lo recalcula")
            if m.unexpected_columns:
                out.append(f"  columnas inesperadas: {m.unexpected_columns}")
        for nota in m.notes:
            out.append(f"  · {nota}")
        out.append("")

    total = sum(m.written for m in imports)
    # Pendiente = lo que faltaba MENOS lo que se acaba de escribir. Sumar
    # `to_write` a secas diría "3998 escritos y 3998 pendientes" en la misma
    # línea, que es incoherente y no es lo que pasó.
    pend = sum(m.to_write - m.written for m in imports)
    out.append(f"TOTAL: {total} día(s) escritos, {pend} pendiente(s) de escribir.")
    if not write and pend:
        out.append("  Nada de esto se escribió todavía — falta --write.")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="import_gdelt_entropy",
        description="Importa la entropía GDELT histórica desde parquet a la "
                    "serie canónica JSONL. Dry-run por defecto.",
    )
    p.add_argument("--assets", nargs="+", required=True)
    p.add_argument("--lake-root", default=None,
                   help="Raíz del data lake. Si falta, se usa "
                        "SPEL_DATA_LAKE_ROOT. Sin fallback a una ruta fija, "
                        "misma regla que tools/audit_data_lake.py.")
    p.add_argument("--pattern", default=PATRON_DEFAULT,
                   help=f"Patrón del archivo de origen. Default: {PATRON_DEFAULT}")
    p.add_argument("--write", action="store_true",
                   help="Escribe de verdad. SIN este flag es dry-run: un "
                        "import que escribe por omisión corrompe por omisión.")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        raiz = data_lake_root(args.lake_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not raiz.is_dir():
        print(f"ERROR: la raíz del data lake no existe: {raiz}", file=sys.stderr)
        return 2

    imports = [
        importar_asset(a, lake_root=raiz, patron=args.pattern, write=args.write)
        for a in args.assets
    ]

    if args.format == "json":
        print(json.dumps(
            {"lake_root": str(raiz), "pattern": args.pattern,
             "write": args.write,
             "min_events_for_valid_day": MIN_EVENTS_FOR_VALID_DAY,
             "assets": [asdict(m) for m in imports]},
            indent=2, ensure_ascii=False,
        ))
    else:
        print(render_text(imports, write=args.write))

    # Exit 0 siempre que el import se complete. Un activo sin origen o con
    # esquema incompatible es un resultado del reporte, no un fallo del
    # proceso. Exit != 0 se reserva para fallo real de invocación.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
