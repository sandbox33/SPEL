"""
tools/audit_data_lake.py
=========================
Audita el data lake legacy (Parquet) y produce un inventario verificado
de qué datos existen realmente, con qué schema, y cubriendo qué fechas.

POR QUÉ ESTE ARCHIVO EXISTE, y por qué es un tool y no un script suelto:
la causa raíz del estancamiento del LSTM (Altair, 2026-08-18) fue que los
parquets fuente tenían columnas de fecha en formatos distintos entre sí,
alimentando entrenamiento sin que nadie lo detectara a tiempo.
`ingestion/training_dataset.py` (patch 0031) resuelve eso para los datos
NUEVOS (Deriv entrega epoch, sin ambigüedad posible). Pero los datos que
YA existen en `07_DATA_LAKE/processed/` no pasaron por ese camino -- y son
la única historia profunda que el proyecto tiene hoy.

Este módulo no arregla nada. Reporta. Es deliberadamente read-only: nunca
escribe, mueve, ni normaliza un parquet -- solo produce el manifiesto que
permite DECIDIR qué normalizar, con evidencia en la mano en vez de
suposiciones. La normalización es un paso posterior y separado, con su
propia decisión de Altair (Modo Investigador).

HALLAZGO REAL que motivó escribirlo (verificado el 2026-08-18 leyendo dos
parquets reales del Drive de Altair, no supuesto):

  BTC_2024_entropy.parquet  -> 9 columnas, `date` = timestamp[ms, UTC]
                               rango real 2024-01-01 .. 2024-12-31 (coincide
                               con el nombre)
  BTC_2026_entropy.parquet  -> 2 columnas, `date` = large_string (!!)
                               rango real 2024-09-09 .. 2026-03-08 -- 18
                               meses, solapando los archivos 2024 y 2025

Es decir: dentro de la MISMA carpeta, del MISMO activo, hay archivos con
tipo de fecha distinto (timestamp vs string), con juegos de columnas
distintos (9 vs 2), y con rangos que se solapan mientras el nombre del
archivo dice otra cosa. Eso es exactamente la clase de bug que hay que
cazar ANTES de entrenar, no después de cuatro meses.

Los 4 chequeos que hace, uno por cada forma real de que esto falle:
  1. SCHEMA DRIFT   -- archivos del mismo (activo, stream) con columnas o
                       dtypes distintos entre sí.
  2. NOMBRE MIENTE  -- el año en el nombre del archivo no coincide con el
                       rango real de fechas de su contenido.
  3. SOLAPAMIENTO   -- dos archivos del mismo (activo, stream) cubren días
                       en común -- ¿cuál gana? Hoy nadie lo define.
  4. HUECOS         -- días faltantes dentro del rango cubierto, y el hueco
                       entre el último día disponible y hoy (= cuánto hay
                       que descargar para ponerse al día).

Uso:
    python tools/audit_data_lake.py --root /content/drive/MyDrive/Legacy/SPEL/07_DATA_LAKE/processed
    python tools/audit_data_lake.py --root <ruta> --json data_manifest.json

Sin --root, usa DATA_LAKE_ROOT_ENV_VAR (SPEL_DATA_LAKE_ROOT) si está, y si
no falla con un mensaje claro -- NUNCA adivina una ruta de Colab
hardcodeada (misma regla que governance/persistence.py::drive_root()).

pyarrow: se importa de forma perezosa DENTRO de las funciones que lo
necesitan, no a nivel de módulo. Razón concreta: requirements.txt no lo
incluye (el repo nuevo nunca necesitó Parquet -- ingestion/gdelt_series.py
eligió JSONL a propósito, ver su docstring), así que importarlo arriba
rompería `import tools.audit_data_lake` en un entorno donde no está,
incluido el CI. Los tests que necesitan pyarrow se saltean solos si falta.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

#: Override explícito de la raíz del data lake -- mismo mecanismo que
#: SPEL_DRIVE_ROOT en governance/persistence.py, no uno nuevo inventado.
DATA_LAKE_ROOT_ENV_VAR = "SPEL_DATA_LAKE_ROOT"

#: Nombres de columna candidatos para la fecha, en orden de preferencia.
#: El legacy usa 'date' en todos los parquets auditados hasta ahora; los
#: otros están acá para que un archivo con otra convención se reporte como
#: hallazgo en vez de romper el auditor.
DATE_COLUMN_CANDIDATES: tuple[str, ...] = ("date", "timestamp", "datetime", "time")


@dataclass
class FileAudit:
    """Auditoría de UN archivo parquet. Todos los campos salen de leer el
    archivo -- ninguno se infiere del nombre, excepto `year_in_filename`,
    que existe justamente para poder CONTRASTARLO contra el contenido."""

    path: str
    asset: str
    stream: str                      # ohlcv | entropy | gdelt | desconocido
    size_bytes: int
    n_rows: int
    columns: list[str]
    dtypes: dict[str, str]
    date_column: Optional[str]
    date_dtype: Optional[str]
    date_is_string: bool             # el hallazgo #1 -- string en vez de fecha real
    date_min: Optional[str]          # ISO 8601, o None si no se pudo determinar
    date_max: Optional[str]
    year_in_filename: Optional[int]
    filename_matches_content: Optional[bool]   # None si no hay año en el nombre
    n_missing_days: Optional[int]    # días faltantes dentro de [date_min, date_max]
    error: Optional[str] = None      # si el archivo no se pudo leer, se dice acá


@dataclass
class GroupFinding:
    """Un hallazgo a nivel de grupo (activo + stream), no de archivo suelto."""

    kind: str          # schema_drift | overlap | filename_mismatch | date_as_string
    asset: str
    stream: str
    detail: str
    files: list[str] = field(default_factory=list)


@dataclass
class LakeAudit:
    root: str
    generated_utc: str
    n_files: int
    files: list[FileAudit]
    findings: list[GroupFinding]
    coverage: dict[str, dict[str, Any]]   # (asset/stream) -> rango y días al día de hoy


def data_lake_root(explicit: Optional[str] = None) -> Path:
    """
    Raíz del data lake. Prioridad -- MISMO orden que
    governance/persistence.py::drive_root(), no un mecanismo nuevo:
      1. argumento explícito (--root)
      2. env var SPEL_DATA_LAKE_ROOT
      3. error claro -- nunca una ruta de Colab hardcodeada de fallback.

    El punto 3 es deliberado y distinto de drive_root(): persistence.py
    puede caer a una ruta local porque escribe datos nuevos; acá no hay
    fallback razonable, auditar "la carpeta equivocada" en silencio sería
    peor que fallar.
    """
    if explicit:
        return Path(explicit)
    from_env = os.environ.get(DATA_LAKE_ROOT_ENV_VAR)
    if from_env:
        return Path(from_env)
    raise ValueError(
        "No se indicó la raíz del data lake. Usá --root <ruta> o exportá "
        f"{DATA_LAKE_ROOT_ENV_VAR}. No se asume ninguna ruta por defecto a "
        "propósito -- auditar la carpeta equivocada en silencio es peor que fallar."
    )


def _year_from_filename(name: str) -> Optional[int]:
    """Extrae un año de 4 dígitos del nombre (BTC_2024_entropy.parquet -> 2024).
    Devuelve None si no hay ninguno o si hay más de uno (ambiguo -- no se
    adivina cuál es el "correcto")."""
    import re

    years = re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", name)
    if len(years) == 1:
        return int(years[0])
    return None


def _infer_asset_and_stream(path: Path, root: Path) -> tuple[str, str]:
    """Deduce (activo, stream) de la ruta relativa. El layout legacy real es
    `processed/{ASSET}/{stream}/[subdir/]archivo.parquet` -- se lee de ahí,
    no del nombre del archivo, porque el nombre ya demostró ser poco
    confiable (ver docstring del módulo)."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return ("desconocido", "desconocido")
    asset = parts[0] if len(parts) >= 1 else "desconocido"
    stream = parts[1] if len(parts) >= 2 else "desconocido"
    return (asset, stream)


def _to_iso_date(value: Any) -> Optional[str]:
    """Normaliza a 'YYYY-MM-DD' cualquiera de las formas en que una fecha
    puede venir de un parquet (str, date, datetime, pd.Timestamp). Devuelve
    None si no se puede -- nunca inventa una fecha ni lanza."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    # pandas.Timestamp y numpy.datetime64 exponen .date() o se convierten
    for attr in ("date", "to_pydatetime"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                result = fn()
                return result.isoformat()[:10] if hasattr(result, "isoformat") else None
            except Exception:
                pass
    return None


def _parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def audit_file(path: Path, root: Path) -> FileAudit:
    """
    Audita un parquet. NUNCA lanza -- cualquier fallo de lectura se reporta
    en el campo `error` del resultado. Un archivo corrupto no debe tirar
    abajo la auditoría de los otros 40 (mismo principio que ya rige
    ingestion/gdelt.py: una fila mala no aborta el día).
    """
    asset, stream = _infer_asset_and_stream(path, root)
    base = FileAudit(
        path=str(path), asset=asset, stream=stream,
        size_bytes=path.stat().st_size if path.exists() else 0,
        n_rows=0, columns=[], dtypes={}, date_column=None, date_dtype=None,
        date_is_string=False, date_min=None, date_max=None,
        year_in_filename=_year_from_filename(path.name),
        filename_matches_content=None, n_missing_days=None,
    )

    try:
        import pyarrow.parquet as pq
    except ImportError:
        base.error = "pyarrow no está instalado -- pip install pyarrow"
        return base

    try:
        pf = pq.ParquetFile(str(path))
        schema = pf.schema_arrow
        base.n_rows = pf.metadata.num_rows
        base.columns = list(schema.names)
        base.dtypes = {name: str(schema.field(name).type) for name in schema.names}
    except Exception as exc:  # archivo corrupto, truncado, o no-parquet
        base.error = f"{type(exc).__name__}: {exc}"
        return base

    date_col = next((c for c in DATE_COLUMN_CANDIDATES if c in base.columns), None)
    base.date_column = date_col
    if date_col is None:
        base.error = (
            f"ninguna columna de fecha reconocida (buscadas: "
            f"{', '.join(DATE_COLUMN_CANDIDATES)}); columnas reales: {base.columns}"
        )
        return base

    base.date_dtype = base.dtypes[date_col]
    #: HALLAZGO #1 -- una fecha guardada como string es exactamente el
    #: agujero por donde entró el bug de formatos mixtos ('/' vs '-').
    base.date_is_string = "string" in base.date_dtype.lower()

    try:
        table = pf.read(columns=[date_col])
        values = table.column(date_col).to_pylist()
    except Exception as exc:
        base.error = f"no se pudo leer la columna '{date_col}': {type(exc).__name__}: {exc}"
        return base

    isos = sorted({d for d in (_to_iso_date(v) for v in values) if d})
    if not isos:
        base.error = f"columna '{date_col}' sin ninguna fecha parseable"
        return base

    base.date_min, base.date_max = isos[0], isos[-1]

    if base.year_in_filename is not None:
        years_in_content = {int(d[:4]) for d in isos}
        base.filename_matches_content = years_in_content == {base.year_in_filename}

    d_min, d_max = _parse_iso(base.date_min), _parse_iso(base.date_max)
    if d_min and d_max:
        span_days = (d_max - d_min).days + 1
        base.n_missing_days = max(0, span_days - len(isos))

    return base


def find_group_findings(files: list[FileAudit]) -> list[GroupFinding]:
    """
    Hallazgos que solo se ven comparando archivos ENTRE SÍ -- un archivo
    aislado siempre parece correcto; el problema aparece cuando dos del
    mismo activo no coinciden.
    """
    findings: list[GroupFinding] = []
    groups: dict[tuple[str, str], list[FileAudit]] = {}
    for f in files:
        if f.error is None:
            groups.setdefault((f.asset, f.stream), []).append(f)

    for (asset, stream), group in sorted(groups.items()):
        # --- 1. Schema drift: mismo grupo, columnas o dtypes distintos ---
        schemas = {tuple(sorted(f.columns)) for f in group}
        if len(schemas) > 1:
            detail_parts = []
            for f in group:
                detail_parts.append(f"{Path(f.path).name}: {len(f.columns)} cols")
            findings.append(GroupFinding(
                kind="schema_drift", asset=asset, stream=stream,
                detail=(
                    f"{len(schemas)} juegos de columnas distintos en el mismo grupo. "
                    + "; ".join(sorted(detail_parts))
                ),
                files=[f.path for f in group],
            ))

        date_dtypes = {f.date_dtype for f in group if f.date_dtype}
        if len(date_dtypes) > 1:
            findings.append(GroupFinding(
                kind="schema_drift", asset=asset, stream=stream,
                detail=(
                    "la columna de fecha tiene tipos DISTINTOS entre archivos del "
                    f"mismo grupo: {sorted(date_dtypes)} -- esta es la clase exacta "
                    "de bug que detuvo el entrenamiento anterior"
                ),
                files=[f.path for f in group],
            ))

        # --- 2. Fecha guardada como string ---
        as_string = [f for f in group if f.date_is_string]
        if as_string:
            findings.append(GroupFinding(
                kind="date_as_string", asset=asset, stream=stream,
                detail=(
                    f"{len(as_string)} archivo(s) guardan la fecha como texto, no como "
                    "fecha real -- un formato distinto ('/' vs '-') pasaría sin ser detectado"
                ),
                files=[f.path for f in as_string],
            ))

        # --- 3. El nombre del archivo miente sobre su contenido ---
        liars = [f for f in group if f.filename_matches_content is False]
        for f in liars:
            findings.append(GroupFinding(
                kind="filename_mismatch", asset=asset, stream=stream,
                detail=(
                    f"{Path(f.path).name} dice '{f.year_in_filename}' pero contiene "
                    f"{f.date_min} .. {f.date_max}"
                ),
                files=[f.path],
            ))

        # --- 4. Solapamiento de rangos entre archivos del mismo grupo ---
        ranged = [f for f in group if f.date_min and f.date_max]
        ranged.sort(key=lambda f: f.date_min or "")
        for i in range(len(ranged) - 1):
            a, b = ranged[i], ranged[i + 1]
            if (a.date_max or "") >= (b.date_min or ""):
                findings.append(GroupFinding(
                    kind="overlap", asset=asset, stream=stream,
                    detail=(
                        f"{Path(a.path).name} ({a.date_min}..{a.date_max}) y "
                        f"{Path(b.path).name} ({b.date_min}..{b.date_max}) cubren días "
                        "en común -- ninguna regla define hoy cuál gana"
                    ),
                    files=[a.path, b.path],
                ))

    return findings


def build_coverage(files: list[FileAudit], today: Optional[date] = None) -> dict[str, dict[str, Any]]:
    """Rango total cubierto por (activo/stream) y cuántos días faltan hasta
    hoy -- la respuesta directa a '¿qué me falta descargar?'."""
    today = today or datetime.now(timezone.utc).date()
    coverage: dict[str, dict[str, Any]] = {}
    for f in files:
        if f.error is not None or not f.date_min or not f.date_max:
            continue
        key = f"{f.asset}/{f.stream}"
        entry = coverage.setdefault(key, {
            "asset": f.asset, "stream": f.stream, "n_files": 0,
            "date_min": f.date_min, "date_max": f.date_max, "n_rows": 0,
        })
        entry["n_files"] += 1
        entry["n_rows"] += f.n_rows
        entry["date_min"] = min(entry["date_min"], f.date_min)
        entry["date_max"] = max(entry["date_max"], f.date_max)

    for entry in coverage.values():
        d_max = _parse_iso(entry["date_max"])
        entry["days_behind_today"] = (today - d_max).days if d_max else None
    return coverage


def audit_lake(root: Path, today: Optional[date] = None) -> LakeAudit:
    """Audita el árbol completo. Recorre .parquet recursivamente -- el layout
    real tiene subcarpetas (ohlcv/aggregated/, gdelt/raw/), no una
    profundidad fija."""
    paths = sorted(p for p in root.rglob("*.parquet") if p.is_file())
    files = [audit_file(p, root) for p in paths]
    return LakeAudit(
        root=str(root),
        generated_utc=datetime.now(timezone.utc).isoformat(),
        n_files=len(files),
        files=files,
        findings=find_group_findings(files),
        coverage=build_coverage(files, today=today),
    )


def format_report(audit: LakeAudit) -> str:
    """Reporte legible en pantalla. El JSON es para máquinas; esto es para
    leerlo desde el teléfono, que es donde Altair trabaja."""
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("  AUDITORÍA DEL DATA LAKE")
    lines.append("=" * 68)
    lines.append(f"  raíz:     {audit.root}")
    lines.append(f"  archivos: {audit.n_files}")
    lines.append("")

    lines.append("--- COBERTURA POR ACTIVO / STREAM " + "-" * 34)
    if not audit.coverage:
        lines.append("  (ningún archivo con fechas legibles)")
    for key in sorted(audit.coverage):
        e = audit.coverage[key]
        behind = e.get("days_behind_today")
        behind_txt = f"{behind} días atrás" if behind is not None else "?"
        lines.append(
            f"  {key:28s} {e['date_min']} .. {e['date_max']}  "
            f"({e['n_rows']:>6,} filas, {e['n_files']} archivos) -- {behind_txt}"
        )
    lines.append("")

    errored = [f for f in audit.files if f.error]
    if errored:
        lines.append("--- ARCHIVOS QUE NO SE PUDIERON AUDITAR " + "-" * 27)
        for f in errored:
            lines.append(f"  {Path(f.path).name}: {f.error}")
        lines.append("")

    lines.append("--- HALLAZGOS " + "-" * 53)
    if not audit.findings:
        lines.append("  Ninguno. Todos los archivos de cada grupo son consistentes entre sí.")
    for finding in audit.findings:
        lines.append(f"  [{finding.kind}] {finding.asset}/{finding.stream}")
        lines.append(f"      {finding.detail}")
    lines.append("")
    lines.append("=" * 68)
    return "\n".join(lines)


def _audit_to_dict(audit: LakeAudit) -> dict:
    return {
        "root": audit.root,
        "generated_utc": audit.generated_utc,
        "n_files": audit.n_files,
        "files": [asdict(f) for f in audit.files],
        "findings": [asdict(x) for x in audit.findings],
        "coverage": audit.coverage,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audita el data lake Parquet: schema, rangos reales, drift y huecos."
    )
    parser.add_argument("--root", default=None, help="Raíz del data lake (processed/)")
    parser.add_argument("--json", default=None, help="Ruta donde escribir el manifiesto JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        root = data_lake_root(args.root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not root.exists():
        print(f"ERROR: la ruta no existe: {root}", file=sys.stderr)
        return 2

    audit = audit_lake(root)
    print(format_report(audit))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_audit_to_dict(audit), indent=2, ensure_ascii=False))
        print(f"Manifiesto escrito en: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
