"""
ingestion/gdelt_series.py
============================
Acumula DailyAggregationResult (ingestion/gdelt_aggregation.py) día a
día en una serie persistente por activo. Sin esto, cada llamada a
aggregate_day() es una fila aislada -- nash_frozen_7d y vitality_tesla
(core/scoring.py) necesitan una VENTANA de días pasados, no un punto
suelto. Esta es la pieza que produce esa ventana.

DECISIÓN DE FORMATO -- no un port, una elección deliberada marcada como
tal: el legacy (gdelt_foundation.py) usa Parquet + Polars, procesando
años completos de una vez. spel_ingest_incremental.py sí es
incremental (lee último día, calcula gap, actualiza) pero SIGUE usando
Parquet -- el legacy nunca tuvo la opción liviana que se elige acá.

Se usa JSONL (una línea JSON por día) en vez de Parquet porque:
  - Esta serie crece UN DÍA a la vez (aggregate_day() produce una fila),
    no un año de golpe -- el caso de uso real es append incremental,
    no reescritura batch. JSONL es append-only nativo (abrir en modo
    "a", escribir una línea, cerrar) -- Parquet no lo es (columnar,
    reescribir el archivo entero en cada append o mantener múltiples
    archivos y compactar después).
  - No suma una dependencia pesada (polars o pyarrow) solo para esto --
    misma decisión que ya se tomó dos veces en este patch set
    (ingestion/gdelt.py sin polars, ingestion/gdelt_aggregation.py sin
    numpy) por la razón inversa: no pagar el peso de una librería
    completa por una necesidad puntual.
  - Legible a mano en Drive sin herramientas -- relevante en un flujo
    de trabajo que corre desde Android.
  - Con las decenas o pocos cientos de días que este proyecto necesita
    en el horizonte de F1-F2 (no los 10+ años que gdelt_foundation.py
    procesaba para entrenar), el costo de "recorrer todo el archivo"
    en vez de índice columnar es irrelevante -- si esto deja de ser
    cierto en una fase futura con mucho más historial, ES EL MOMENTO de
    migrar a Parquet, no antes.

Usa PersistenceStream.METRICS (governance/persistence.py, patch 0010)
-- primer consumidor real de ese stream. Hasta este patch, persistence.py
solo declaraba la ruta; acá se lee y escribe de verdad por primera vez.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Optional

from governance.persistence import PersistenceStream, stream_path
from ingestion.gdelt_aggregation import DailyAggregationResult

logger = logging.getLogger("spel.ingestion.gdelt_series")


def _series_file_path(asset: str) -> Path:
    """Un archivo JSONL por activo, dentro del stream METRICS ya
    declarado -- gdelt_series/{asset}.jsonl bajo drive_root()/metrics/."""
    base = Path(stream_path(PersistenceStream.METRICS))
    return base / "gdelt_series" / f"{asset}.jsonl"


def _result_to_line(result: DailyAggregationResult) -> str:
    """Serializa un DailyAggregationResult a una línea JSON. date no es
    JSON-nativo -- se serializa como ISO 8601 (YYYY-MM-DD), se
    reconstruye al leer."""
    payload = asdict(result)
    payload["day"] = result.day.isoformat()
    return json.dumps(payload, ensure_ascii=False)


def _line_to_result(line: str) -> DailyAggregationResult:
    payload = json.loads(line)
    payload["day"] = date.fromisoformat(payload["day"])
    return DailyAggregationResult(**payload)


def append_day(result: DailyAggregationResult) -> None:
    """
    Agrega un día a la serie del activo. Append puro -- nunca lee ni
    reescribe el archivo entero, nunca deduplica ni ordena (eso es
    responsabilidad de read_series() al leer, no de cada escritura
    individual -- escribir rápido y simple, leer con cuidado).

    Crea el directorio padre si no existe (primera escritura de ese
    activo). No valida que `result.day` sea posterior al último día ya
    guardado -- un caller que reprocesa un día viejo (ej. backfill) es
    un caso de uso legítimo, no un error; ver read_series() para cómo
    se resuelven duplicados al leer.
    """
    path = _series_file_path(result.asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_result_to_line(result))
        f.write("\n")
    logger.debug("gdelt_series: día %s agregado para %s", result.day, result.asset)


def read_series(
    asset: str,
    *,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> list[DailyAggregationResult]:
    """
    Lee la serie completa de un activo, ordenada cronológicamente
    (orden ascendente, el ÚLTIMO elemento es el día más reciente --
    misma convención que entropy_window en core/scoring.py, que espera
    exactamente ese orden).

    DEDUPLICACIÓN: si append_day() escribió el mismo `day` más de una
    vez (backfill, reintento), read_series() se queda con la ÚLTIMA
    ocurrencia en el archivo (la más reciente escrita, no
    necesariamente la fecha más reciente) -- asume que un reprocesamiento
    corrige, no que la primera versión era mejor. Líneas corruptas
    (JSON inválido) se saltean con un warning, no abortan la lectura de
    las demás -- mismo principio de degradación parcial que
    ingestion/gdelt.py aplica a filas GDELT truncadas.

    Si el archivo no existe todavía (activo sin ningún día guardado),
    devuelve lista vacía -- no lanza, un activo nuevo es un caso válido.

    Args:
        asset: activo a leer.
        since, until: filtro opcional por rango de fechas, inclusive en
            ambos extremos. None = sin límite en ese extremo.
    """
    path = _series_file_path(asset)
    if not path.exists():
        return []

    por_dia: dict[date, DailyAggregationResult] = {}
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                result = _line_to_result(line)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(
                    "gdelt_series: línea %d corrupta en %s, se saltea: %s",
                    lineno, path, e,
                )
                continue
            por_dia[result.day] = result  # última ocurrencia gana

    dias_ordenados = sorted(por_dia.keys())
    if since is not None:
        dias_ordenados = [d for d in dias_ordenados if d >= since]
    if until is not None:
        dias_ordenados = [d for d in dias_ordenados if d <= until]

    return [por_dia[d] for d in dias_ordenados]


def last_day(asset: str) -> Optional[date]:
    """Fecha del día más reciente ya guardado para `asset`, o None si
    la serie está vacía. Pensado para el patrón gap-fill de
    spel_ingest_incremental.py (leer último día, calcular cuántos faltan
    hasta hoy) -- ver docstring del módulo para por qué NO se porta esa
    lógica de gap completa acá todavía (es responsabilidad de un
    orquestador que decide CUÁNDO correr esto, no de la capa de
    almacenamiento)."""
    serie = read_series(asset)
    return serie[-1].day if serie else None
