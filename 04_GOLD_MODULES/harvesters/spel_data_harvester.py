# ══════════════════════════════════════════════════════════════════════════════
# spel_data_harvester.py
# SPEL v22 — Data Lake Harvester (Polars · Hive Partition · zstd)
#
# Autor : Abraham Fuenmayor
# Versión: v22.0.0 · 03 Mar 2026
#
# REGLAS ACTIVAS:
#   Regla 4  : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla 5  : Parquets v4 = 24 columnas canónicas exactas · Source of Truth
#   Regla 13 : LSTM arquitectura inamovible (input=20, hidden=64, layers=1)
#   Regla 24 : Todo acceso externo exclusivamente via SPELAdapterChain
#   Regla 22 : detectar_col_fecha() SIEMPRE antes de operar parquets raw
#
# DISEÑO:
#   ├── data_lake/{activo}/ohlcv/raw/year=YYYY/month=MM/{ts_utc}.parquet
#   ├── data_lake/{activo}/gdelt/raw/year=YYYY/month=MM/{ts_utc}.parquet
#   ├── data_lake/{activo}/ohlcv/aggregated/{activo}_ohlcv_v4.parquet
#   └── data_lake/{activo}/gdelt/aggregated/{activo}_gdelt_v1.parquet
#
# STORAGE:
#   Compresión : zstd level=6 (mejor ratio / velocidad para datos analíticos)
#   Timestamp  : pl.Datetime("ms", "UTC") en todas las columnas temporales
#   Partición  : Hive-style year=YYYY/month=MM (predicate pushdown compatible)
#
# PROHIBIDO:
#   pandas · yfinance · datetime.utcnow() · numpy directo · cualquier
#   conversión implícita de timezone que no sea UTC
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import polars as pl

# ── Logging ───────────────────────────────────────────────────────────────────
_log = logging.getLogger("spel.harvester")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES CANÓNICAS (Regla 4 + Regla 5 — INAMOVIBLES)
# ══════════════════════════════════════════════════════════════════════════════

ACTIVOS_VALIDOS: frozenset[str] = frozenset({"NVDA", "BTC", "XAU", "NIFTY50"})

# λ canónicas por activo (Regla 4)
LAMBDA_PARAMS: dict[str, int] = {"BTC": 21, "NVDA": 63, "XAU": 63, "NIFTY50": 42}

# Columnas canónicas OHLCV raw (lo que llega del adapter antes de enriquecer)
OHLCV_RAW_SCHEMA: dict[str, pl.DataType] = {
    "date":   pl.Datetime("ms", "UTC"),
    "open":   pl.Float64,
    "high":   pl.Float64,
    "low":    pl.Float64,
    "close":  pl.Float64,
    "volume": pl.Float64,
}

# Columnas canónicas GDELT raw (lo que devuelven los adapters BigQuery / parquet_cache)
GDELT_RAW_SCHEMA: dict[str, pl.DataType] = {
    "date":            pl.Datetime("ms", "UTC"),
    "goldstein_geo":   pl.Float64,
    "n_events_ohlcv":  pl.Float64,
    "vitality_tesla":  pl.Float64,
    "mass_panic_index":pl.Float64,
    "fear_momentum":   pl.Float64,
}

# Schema completo canónico v4 (24 columnas — Regla 5)
CANONICAL_V4_SCHEMA: dict[str, pl.DataType] = {
    # ── OHLCV base ────────────────────────────────────────────────────────────
    "date":                    pl.Datetime("ms", "UTC"),
    "open":                    pl.Float64,
    "high":                    pl.Float64,
    "low":                     pl.Float64,
    "close":                   pl.Float64,
    "volume":                  pl.Float64,
    # ── Features de entropía ─────────────────────────────────────────────────
    "log_return":              pl.Float64,
    "entropy_shannon":         pl.Float64,
    "entropy_decay_lambda":    pl.Float64,
    "entropy_psych_vix":       pl.Float64,
    # ── Fibonacci lags ────────────────────────────────────────────────────────
    "fibonacci_lag_1":         pl.Float64,
    "fibonacci_lag_2":         pl.Float64,
    "fibonacci_lag_3":         pl.Float64,
    "fibonacci_lag_5":         pl.Float64,
    "fibonacci_lag_8":         pl.Float64,
    "fibonacci_lag_13":        pl.Float64,
    "fibonacci_lag_21":        pl.Float64,
    # ── GDELT features ────────────────────────────────────────────────────────
    "goldstein_geo":           pl.Float64,
    "n_events_ohlcv":          pl.Float64,
    "vitality_tesla":          pl.Float64,
    "mass_panic_index":        pl.Float64,
    "fear_momentum":           pl.Float64,
    # ── Indicadores sistémicos ────────────────────────────────────────────────
    "vix_norm":                pl.Float64,
    "nash_frozen_7d":          pl.Float64,
}

# Compresión y nivel zstd
_ZSTD_LEVEL: int = 6          # Nivel 6: ratio excelente · velocidad ~80% de nivel 1
_FEED_OHLCV: str = "ohlcv"
_FEED_GDELT: str = "gdelt"
_RAW_DIR:    str = "raw"
_AGG_DIR:    str = "aggregated"

# Días de retención para datos crudos (antes del pruning)
DEFAULT_RAW_RETENTION_DAYS: int = 90


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES DE RESULTADO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HarvestResult:
    """Resultado inmutable de una operación de harvest."""
    activo:      str
    feed:        Literal["ohlcv", "gdelt"]
    rows:        int
    partitions:  list[Path]
    bytes_total: int
    sha256:      str
    ts_utc:      datetime
    ok:          bool
    error:       str = ""

    def __str__(self) -> str:
        status = "✅" if self.ok else "❌"
        kb = self.bytes_total // 1024
        parts = len(self.partitions)
        return (
            f"{status} HarvestResult({self.activo}/{self.feed}) "
            f"{self.rows} rows · {parts} partitions · {kb} KB · {self.sha256[:12]}"
        )


@dataclass(frozen=True)
class PruneResult:
    """Resultado inmutable de una operación de pruning."""
    activo:          str
    dirs_deleted:    list[Path]
    bytes_freed:     int
    cutoff_date:     datetime
    ts_utc:          datetime
    ok:              bool
    errors:          list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "✅" if self.ok else "⚠️"
        mb = self.bytes_freed // (1024 ** 2)
        return (
            f"{status} PruneResult({self.activo}) "
            f"{len(self.dirs_deleted)} dirs eliminados · {mb} MB liberados"
        )


@dataclass
class LakeAudit:
    """Snapshot del estado del data lake para un activo."""
    activo:          str
    ts_utc:          datetime
    ohlcv_raw_rows:  int
    ohlcv_agg_rows:  int
    gdelt_raw_rows:  int
    gdelt_agg_rows:  int
    raw_partitions:  dict[str, list[str]]   # feed → [year=YYYY/month=MM, ...]
    total_bytes:     int
    schema_ok:       bool
    schema_errors:   list[str]

    def summary(self) -> str:
        mb = self.total_bytes // (1024 ** 2)
        icon = "✅" if self.schema_ok else "⚠️"
        return (
            f"{icon} LakeAudit({self.activo}) "
            f"OHLCV raw={self.ohlcv_raw_rows} agg={self.ohlcv_agg_rows} · "
            f"GDELT raw={self.gdelt_raw_rows} agg={self.gdelt_agg_rows} · "
            f"{mb} MB total"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLASE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class SPELDataHarvester:
    """
    Data Lake Harvester para SPEL v22.

    Responsabilidades:
      1. Ingestar DataFrames Polars de OHLCV y GDELT y escribirlos como
         Parquet particionado por año/mes con compresión zstd.
      2. Mantener el data lake limpio mediante pruning automático de datos
         crudos con más de `raw_retention_days` días de antigüedad,
         preservando siempre los datos agregados.
      3. Exponer una API de lectura lazy (LazyFrame) compatible con
         predicate pushdown de Polars sobre particiones Hive.
      4. Consolidar particiones raw en el parquet canónico v4 para consumo
         por el motor LSTM (Regla 5 · Regla 13).

    Estructura en disco:
        {root}/
        └── data_lake/
            └── {activo}/
                ├── ohlcv/
                │   ├── raw/
                │   │   └── year=YYYY/month=MM/{ts_utc_ms}.parquet
                │   └── aggregated/
                │       └── {activo}_ohlcv_v4.parquet
                └── gdelt/
                    ├── raw/
                    │   └── year=YYYY/month=MM/{ts_utc_ms}.parquet
                    └── aggregated/
                        └── {activo}_gdelt_v1.parquet

    Parámetros:
        root              : Path raíz del data lake (ej: SPEL_v8_PROD/).
        activo            : Uno de {"NVDA", "BTC", "XAU", "NIFTY50"}.
        raw_retention_days: Días de retención para datos crudos (default 90).
        zstd_level        : Nivel de compresión zstd 1–22 (default 6).
        strict_schema     : Si True, rechaza DFs que no cumplan schema exacto.

    Notas:
        - Todas las operaciones de tiempo usan datetime.now(timezone.utc).
          utcnow() está explícitamente prohibido.
        - El dtype temporal canónico es pl.Datetime("ms", "UTC") en todo
          el pipeline, sin excepción.
        - Esta clase no implementa adapters externos: recibe DataFrames ya
          construidos por SPELAdapterChain (Regla 24).
    """

    def __init__(
        self,
        root: Path | str,
        activo: str,
        raw_retention_days: int = DEFAULT_RAW_RETENTION_DAYS,
        zstd_level: int = _ZSTD_LEVEL,
        strict_schema: bool = True,
    ) -> None:
        self._root = Path(root)
        self._activo = activo.upper()
        self._retention = raw_retention_days
        self._zstd_level = zstd_level
        self._strict = strict_schema

        # Validaciones de construcción
        if self._activo not in ACTIVOS_VALIDOS:
            raise ValueError(
                f"Activo '{self._activo}' no válido. "
                f"Opciones: {sorted(ACTIVOS_VALIDOS)}"
            )
        if not (1 <= zstd_level <= 22):
            raise ValueError(f"zstd_level debe estar entre 1 y 22, recibido: {zstd_level}")

        # Crear estructura de directorios idempotente
        for feed in (_FEED_OHLCV, _FEED_GDELT):
            self._raw_root(feed).mkdir(parents=True, exist_ok=True)
            self._agg_root(feed).mkdir(parents=True, exist_ok=True)

        _log.info("SPELDataHarvester inicializado — activo=%s root=%s", self._activo, self._root)

    # ── Propiedades de acceso a paths ─────────────────────────────────────────

    @property
    def activo(self) -> str:
        return self._activo

    @property
    def lambda_days(self) -> int:
        """λ canónica para este activo (Regla 4)."""
        return LAMBDA_PARAMS[self._activo]

    def _lake_root(self) -> Path:
        return self._root / "data_lake" / self._activo

    def _raw_root(self, feed: str) -> Path:
        return self._lake_root() / feed / _RAW_DIR

    def _agg_root(self, feed: str) -> Path:
        return self._lake_root() / feed / _AGG_DIR

    def _partition_dir(self, feed: str, year: int, month: int) -> Path:
        """Path de una partición Hive específica."""
        return self._raw_root(feed) / f"year={year}" / f"month={month:02d}"

    def _agg_path(self, feed: str) -> Path:
        """Path del parquet agregado para este feed."""
        suffix = "v4" if feed == _FEED_OHLCV else "v1"
        return self._agg_root(feed) / f"{self._activo}_{feed}_{suffix}.parquet"

    # ── API pública: harvest ──────────────────────────────────────────────────

    def harvest_ohlcv(self, df: pl.DataFrame) -> HarvestResult:
        """
        Ingesta un DataFrame de OHLCV crudo, valida su schema, normaliza
        timestamps a UTC·ms y escribe particiones Hive comprimidas con zstd.

        El DataFrame de entrada debe contener al menos las 6 columnas del
        OHLCV_RAW_SCHEMA. Columnas adicionales son permitidas y preservadas.

        Returns:
            HarvestResult con metadata de la operación.
        """
        return self._harvest(df, _FEED_OHLCV, OHLCV_RAW_SCHEMA)

    def harvest_gdelt(self, df: pl.DataFrame) -> HarvestResult:
        """
        Ingesta un DataFrame de GDELT crudo, valida su schema, normaliza
        timestamps a UTC·ms y escribe particiones Hive comprimidas con zstd.

        Returns:
            HarvestResult con metadata de la operación.
        """
        return self._harvest(df, _FEED_GDELT, GDELT_RAW_SCHEMA)

    def _harvest(
        self,
        df: pl.DataFrame,
        feed: str,
        required_schema: dict[str, pl.DataType],
    ) -> HarvestResult:
        """Núcleo de escritura particionada. Usado por harvest_ohlcv y harvest_gdelt."""
        ts_now = datetime.now(timezone.utc)

        try:
            # 1. Validar y normalizar schema
            df = self._validate_and_cast(df, required_schema, feed)

            # 2. Añadir columnas de partición year / month
            df = self._add_partition_cols(df)

            # 3. Agrupar por (year, month) y escribir cada partición
            written_paths: list[Path] = []
            total_bytes = 0

            for (year, month), part_df in df.group_by(["_year", "_month"], maintain_order=True):
                # Limpiar columnas de partición antes de escribir
                part_df = part_df.drop(["_year", "_month"])

                # Construir path de partición Hive
                part_dir = self._partition_dir(feed, int(year), int(month))
                part_dir.mkdir(parents=True, exist_ok=True)

                # Nombre de archivo: timestamp UTC en milisegundos para unicidad
                ts_ms = int(ts_now.timestamp() * 1000)
                out_path = part_dir / f"{ts_ms}.parquet"

                part_df.write_parquet(
                    out_path,
                    compression="zstd",
                    compression_level=self._zstd_level,
                    statistics=True,       # para predicate pushdown
                    row_group_size=50_000,
                )

                size = out_path.stat().st_size
                written_paths.append(out_path)
                total_bytes += size
                _log.debug(
                    "  partition=%s/%s rows=%d bytes=%d",
                    f"year={year}", f"month={month:02d}", len(part_df), size,
                )

            # 4. SHA256 del conjunto de paths escritos (fingerprint de la ingesta)
            sha = self._sha256_paths(written_paths)

            _log.info(
                "harvest_%s(%s) OK — %d rows · %d partitions · %d KB",
                feed, self._activo, len(df), len(written_paths), total_bytes // 1024,
            )

            return HarvestResult(
                activo=self._activo,
                feed=feed,                  # type: ignore[arg-type]
                rows=len(df),
                partitions=written_paths,
                bytes_total=total_bytes,
                sha256=sha,
                ts_utc=ts_now,
                ok=True,
            )

        except Exception as exc:
            _log.error("harvest_%s(%s) FAILED: %s", feed, self._activo, exc)
            return HarvestResult(
                activo=self._activo,
                feed=feed,                  # type: ignore[arg-type]
                rows=0,
                partitions=[],
                bytes_total=0,
                sha256="",
                ts_utc=ts_now,
                ok=False,
                error=str(exc),
            )

    # ── API pública: consolidar en parquet canónico ───────────────────────────

    def consolidate_ohlcv(self) -> pl.DataFrame:
        """
        Lee todas las particiones raw de OHLCV, elimina duplicados por `date`,
        ordena cronológicamente, aplica casts al schema canónico v4 y escribe
        el parquet agregado canónico en `aggregated/`.

        Este es el parquet que consume el motor LSTM (Regla 5).

        Returns:
            pl.DataFrame con el schema canónico v4 completo (24 columnas).
            Las columnas no presentes en el raw se rellenan con null·Float64
            (deben ser completadas por el feature engine antes de entrenar).
        """
        raw_root = self._raw_root(_FEED_OHLCV)
        parquet_files = sorted(raw_root.rglob("*.parquet"))

        if not parquet_files:
            _log.warning("consolidate_ohlcv(%s): no hay particiones raw", self._activo)
            return pl.DataFrame(schema=CANONICAL_V4_SCHEMA)

        # Leer todas las particiones lazy y concatenar
        lf = pl.scan_parquet(
            [str(p) for p in parquet_files],
            hive_partitioning=False,  # las columnas year/month no están en los archivos
        )
        df = lf.collect()

        # Deduplicar por date (mantener última ingesta — mayor ts de archivo)
        df = (
            df
            .sort("date", descending=False)
            .unique(subset=["date"], keep="last", maintain_order=True)
        )

        # Completar columnas canónicas faltantes con null
        df = self._fill_missing_canonical_cols(df)

        # Cast a schema canónico v4
        df = self._cast_to_schema(df, CANONICAL_V4_SCHEMA)

        # Escribir parquet agregado
        agg_path = self._agg_path(_FEED_OHLCV)
        df.write_parquet(
            agg_path,
            compression="zstd",
            compression_level=self._zstd_level,
            statistics=True,
        )

        _log.info(
            "consolidate_ohlcv(%s) → %s | %d rows · %d cols",
            self._activo, agg_path.name, len(df), len(df.columns),
        )
        return df

    def consolidate_gdelt(self) -> pl.DataFrame:
        """
        Lee todas las particiones raw de GDELT, elimina duplicados y escribe
        el parquet agregado canónico de GDELT.

        Returns:
            pl.DataFrame con schema GDELT_RAW_SCHEMA consolidado.
        """
        raw_root = self._raw_root(_FEED_GDELT)
        parquet_files = sorted(raw_root.rglob("*.parquet"))

        if not parquet_files:
            _log.warning("consolidate_gdelt(%s): no hay particiones raw", self._activo)
            return pl.DataFrame(schema=GDELT_RAW_SCHEMA)

        lf = pl.scan_parquet([str(p) for p in parquet_files], hive_partitioning=False)
        df = (
            lf.collect()
            .sort("date", descending=False)
            .unique(subset=["date"], keep="last", maintain_order=True)
        )
        df = self._cast_to_schema(df, GDELT_RAW_SCHEMA)

        agg_path = self._agg_path(_FEED_GDELT)
        df.write_parquet(
            agg_path,
            compression="zstd",
            compression_level=self._zstd_level,
            statistics=True,
        )

        _log.info(
            "consolidate_gdelt(%s) → %s | %d rows",
            self._activo, agg_path.name, len(df),
        )
        return df

    # ── API pública: lectura lazy ─────────────────────────────────────────────

    def scan_ohlcv_raw(
        self,
        start: datetime | None = None,
        end:   datetime | None = None,
    ) -> pl.LazyFrame:
        """
        Retorna un LazyFrame sobre las particiones raw de OHLCV.
        Aplica filtro temporal si se proveen start/end.
        Usa scan_parquet con hive_partitioning=False (las columnas year/month
        no están incluidas en los archivos internos).

        Args:
            start: datetime UTC inclusive. Si None, sin límite inferior.
            end:   datetime UTC inclusive. Si None, sin límite superior.

        Returns:
            pl.LazyFrame — no materializado hasta `.collect()`.
        """
        return self._scan_raw(_FEED_OHLCV, start, end)

    def scan_gdelt_raw(
        self,
        start: datetime | None = None,
        end:   datetime | None = None,
    ) -> pl.LazyFrame:
        """
        Retorna un LazyFrame sobre las particiones raw de GDELT.
        Ver scan_ohlcv_raw para documentación completa.
        """
        return self._scan_raw(_FEED_GDELT, start, end)

    def read_canonical(self) -> pl.LazyFrame:
        """
        Lee el parquet canónico v4 de OHLCV agregado como LazyFrame.
        Este es el punto de entrada para el motor LSTM (Regla 5).

        Returns:
            pl.LazyFrame — el parquet canónico consolidado.
        Raises:
            FileNotFoundError si el parquet agregado no existe aún.
        """
        agg_path = self._agg_path(_FEED_OHLCV)
        if not agg_path.exists():
            raise FileNotFoundError(
                f"Parquet canónico no encontrado: {agg_path}. "
                "Ejecutar consolidate_ohlcv() primero."
            )
        return pl.scan_parquet(str(agg_path))

    def _scan_raw(
        self,
        feed: str,
        start: datetime | None,
        end:   datetime | None,
    ) -> pl.LazyFrame:
        """Núcleo de lectura lazy para un feed dado."""
        raw_root = self._raw_root(feed)
        parquet_files = sorted(raw_root.rglob("*.parquet"))

        if not parquet_files:
            _log.warning("scan_raw(%s/%s): no hay particiones", self._activo, feed)
            schema = OHLCV_RAW_SCHEMA if feed == _FEED_OHLCV else GDELT_RAW_SCHEMA
            return pl.LazyFrame(schema=schema)

        # Filtrar archivos a nivel de filesystem para particiones fuera de rango
        # (optimización de I/O antes del predicate pushdown de Polars)
        if start is not None or end is not None:
            parquet_files = self._filter_partitions_by_date(parquet_files, start, end)

        if not parquet_files:
            schema = OHLCV_RAW_SCHEMA if feed == _FEED_OHLCV else GDELT_RAW_SCHEMA
            return pl.LazyFrame(schema=schema)

        lf = pl.scan_parquet([str(p) for p in parquet_files], hive_partitioning=False)

        # Predicate pushdown sobre columna date
        if start is not None:
            start_ms = _to_utc_ms_datetime(start)
            lf = lf.filter(pl.col("date") >= start_ms)
        if end is not None:
            end_ms = _to_utc_ms_datetime(end)
            lf = lf.filter(pl.col("date") <= end_ms)

        return lf

    # ── API pública: pruning ──────────────────────────────────────────────────

    def prune(
        self,
        cutoff_days: int | None = None,
        dry_run: bool = False,
    ) -> PruneResult:
        """
        Elimina particiones raw cuya fecha de partición sea anterior al
        cutoff (por defecto: raw_retention_days días atrás desde ahora UTC).

        Los datos AGREGADOS en `aggregated/` nunca se tocan: esta función
        opera exclusivamente sobre el árbol `raw/`.

        Args:
            cutoff_days: Días de retención. Si None, usa self._retention (90).
            dry_run:     Si True, reporta qué eliminaría sin borrar nada.

        Returns:
            PruneResult con metadata de la operación.

        Ejemplo:
            >>> result = harvester.prune()
            >>> print(result)
            ✅ PruneResult(NVDA) 3 dirs eliminados · 12 MB liberados
        """
        ts_now    = datetime.now(timezone.utc)
        days      = cutoff_days if cutoff_days is not None else self._retention
        cutoff_dt = ts_now - timedelta(days=days)

        _log.info(
            "prune(%s) cutoff=%s days=%d dry_run=%s",
            self._activo, cutoff_dt.strftime("%Y-%m-%d"), days, dry_run,
        )

        dirs_deleted: list[Path] = []
        bytes_freed  = 0
        errors:  list[str] = []

        for feed in (_FEED_OHLCV, _FEED_GDELT):
            raw_root = self._raw_root(feed)
            if not raw_root.exists():
                continue

            # Iterar sobre year= dirs
            for year_dir in sorted(raw_root.iterdir()):
                if not year_dir.is_dir() or not year_dir.name.startswith("year="):
                    continue
                year = self._parse_hive_int(year_dir.name, "year=")
                if year is None:
                    continue

                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir() or not month_dir.name.startswith("month="):
                        continue
                    month = self._parse_hive_int(month_dir.name, "month=")
                    if month is None:
                        continue

                    # La partición representa el último día del mes como fecha de referencia
                    # para evitar borrar un mes que todavía está parcialmente en retention
                    partition_end = _last_day_of_month(year, month)

                    if partition_end < cutoff_dt:
                        dir_size = _dir_size_bytes(month_dir)
                        if dry_run:
                            _log.info(
                                "  [DRY-RUN] eliminaría %s (%d KB)",
                                month_dir.relative_to(self._root),
                                dir_size // 1024,
                            )
                        else:
                            try:
                                shutil.rmtree(month_dir)
                                _log.info(
                                    "  pruned %s (%d KB)",
                                    month_dir.relative_to(self._root),
                                    dir_size // 1024,
                                )
                            except Exception as exc:
                                errors.append(f"{month_dir}: {exc}")
                                _log.error("  ERROR al borrar %s: %s", month_dir, exc)
                                continue

                        dirs_deleted.append(month_dir)
                        bytes_freed += dir_size

                # Limpiar year_dir si quedó vacío tras el pruning
                if not dry_run and year_dir.exists() and not any(year_dir.iterdir()):
                    try:
                        year_dir.rmdir()
                        _log.debug("  year_dir vacío eliminado: %s", year_dir.name)
                    except Exception:
                        pass

        ok = len(errors) == 0
        return PruneResult(
            activo=self._activo,
            dirs_deleted=dirs_deleted,
            bytes_freed=bytes_freed,
            cutoff_date=cutoff_dt,
            ts_utc=ts_now,
            ok=ok,
            errors=errors,
        )

    # ── API pública: auditoría ────────────────────────────────────────────────

    def audit(self) -> LakeAudit:
        """
        Genera un snapshot del estado actual del data lake para este activo.
        Verifica conteos de filas, particiones existentes e integridad del
        schema canónico v4.

        Returns:
            LakeAudit con toda la información de estado.
        """
        ts_now = datetime.now(timezone.utc)

        # Conteo OHLCV raw (lazy scan para no materializar todo)
        ohlcv_raw_rows  = self._count_raw_rows(_FEED_OHLCV)
        gdelt_raw_rows  = self._count_raw_rows(_FEED_GDELT)

        # Conteo aggregated
        ohlcv_agg_rows  = self._count_agg_rows(_FEED_OHLCV)
        gdelt_agg_rows  = self._count_agg_rows(_FEED_GDELT)

        # Listar particiones existentes
        raw_partitions: dict[str, list[str]] = {}
        for feed in (_FEED_OHLCV, _FEED_GDELT):
            raw_root = self._raw_root(feed)
            partitions = []
            if raw_root.exists():
                for year_dir in sorted(raw_root.iterdir()):
                    if year_dir.is_dir() and year_dir.name.startswith("year="):
                        for month_dir in sorted(year_dir.iterdir()):
                            if month_dir.is_dir() and month_dir.name.startswith("month="):
                                partitions.append(f"{year_dir.name}/{month_dir.name}")
            raw_partitions[feed] = partitions

        # Total de bytes en disco
        total_bytes = _dir_size_bytes(self._lake_root()) if self._lake_root().exists() else 0

        # Verificación de schema del parquet agregado
        schema_ok, schema_errors = self._verify_canonical_schema()

        return LakeAudit(
            activo=self._activo,
            ts_utc=ts_now,
            ohlcv_raw_rows=ohlcv_raw_rows,
            ohlcv_agg_rows=ohlcv_agg_rows,
            gdelt_raw_rows=gdelt_raw_rows,
            gdelt_agg_rows=gdelt_agg_rows,
            raw_partitions=raw_partitions,
            total_bytes=total_bytes,
            schema_ok=schema_ok,
            schema_errors=schema_errors,
        )

    # ── Métodos privados: validación y cast ───────────────────────────────────

    def _validate_and_cast(
        self,
        df: pl.DataFrame,
        required_schema: dict[str, pl.DataType],
        feed: str,
    ) -> pl.DataFrame:
        """
        Valida que el DataFrame contenga las columnas requeridas,
        normaliza la columna `date` a pl.Datetime("ms", "UTC"),
        y castea cada columna al tipo canónico.
        """
        # Verificar columnas presentes
        missing = [c for c in required_schema if c not in df.columns]
        if missing:
            msg = f"Columnas faltantes en {feed} para {self._activo}: {missing}"
            if self._strict:
                raise ValueError(msg)
            _log.warning(msg)
            # Añadir columnas faltantes como null
            for col in missing:
                df = df.with_columns(pl.lit(None).cast(required_schema[col]).alias(col))

        # Normalizar columna date (Regla 22 — detectar_col_fecha canónico)
        df = self._normalize_date_column(df)

        # Cast columnas numéricas al dtype canónico
        casts = [
            pl.col(c).cast(t)
            for c, t in required_schema.items()
            if c in df.columns and c != "date"
        ]
        if casts:
            df = df.with_columns(casts)

        # Eliminar filas con date nulo
        before = len(df)
        df = df.filter(pl.col("date").is_not_null())
        dropped = before - len(df)
        if dropped > 0:
            _log.warning("  %d filas con date=null eliminadas en %s/%s", dropped, self._activo, feed)

        return df.sort("date")

    def _normalize_date_column(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Implementa la lógica de detectar_col_fecha (Regla 22) para Polars.
        Detecta la columna temporal (date, timestamp, Date, Timestamp),
        la renombra a 'date' y castea a pl.Datetime("ms", "UTC").
        """
        # Candidatos en orden de prioridad
        candidates = ["date", "timestamp", "Date", "Timestamp", "datetime", "time"]
        col_fecha = next((c for c in candidates if c in df.columns), None)

        if col_fecha is None:
            # Buscar cualquier columna con dtype temporal
            temporal_cols = [
                c for c in df.columns
                if df[c].dtype in (
                    pl.Date, pl.Datetime,
                    pl.Datetime("ms", "UTC"), pl.Datetime("us", "UTC"),
                    pl.Datetime("ns", "UTC"),
                )
            ]
            if temporal_cols:
                col_fecha = temporal_cols[0]
                _log.warning(
                    "  col_fecha detectada heurísticamente: '%s' en %s",
                    col_fecha, self._activo,
                )
            else:
                raise ValueError(
                    f"No se encontró columna temporal en el DataFrame para {self._activo}. "
                    f"Columnas disponibles: {df.columns}"
                )

        # Renombrar si no se llama 'date'
        if col_fecha != "date":
            df = df.rename({col_fecha: "date"})

        # Cast al dtype canónico UTC·ms
        current_dtype = df["date"].dtype

        if current_dtype == pl.Datetime("ms", "UTC"):
            pass  # ya es el dtype correcto — sin operación
        elif isinstance(current_dtype, pl.Datetime):
            # Tiene timezone pero diferente unit, o no tiene tz
            if current_dtype.time_zone is not None:
                df = df.with_columns(
                    pl.col("date").dt.convert_time_zone("UTC").dt.cast_time_unit("ms")
                )
            else:
                # Asumir UTC si no tiene timezone (con advertencia)
                _log.warning(
                    "  columna 'date' sin timezone en %s — asumiendo UTC",
                    self._activo,
                )
                df = df.with_columns(
                    pl.col("date").dt.replace_time_zone("UTC").dt.cast_time_unit("ms")
                )
        elif current_dtype == pl.Date:
            # pl.Date → pl.Datetime UTC·ms (sin hora = medianoche UTC)
            df = df.with_columns(
                pl.col("date")
                .cast(pl.Datetime("ms"))
                .dt.replace_time_zone("UTC")
            )
        elif current_dtype in (pl.Utf8, pl.String):
            # Intentar parsear string ISO 8601
            df = df.with_columns(
                pl.col("date")
                .str.to_datetime(format=None, use_earliest=True)
                .dt.replace_time_zone("UTC")
                .dt.cast_time_unit("ms")
            )
        else:
            raise ValueError(
                f"dtype de columna 'date' no soportado: {current_dtype} en {self._activo}"
            )

        return df

    def _add_partition_cols(self, df: pl.DataFrame) -> pl.DataFrame:
        """Añade columnas _year y _month para el groupby de particionamiento."""
        return df.with_columns([
            pl.col("date").dt.year().alias("_year"),
            pl.col("date").dt.month().alias("_month"),
        ])

    def _fill_missing_canonical_cols(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Añade columnas del schema canónico v4 que no están en el DataFrame,
        rellenándolas con null. Estas deben ser completadas por el feature engine.
        """
        for col, dtype in CANONICAL_V4_SCHEMA.items():
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).cast(dtype).alias(col))
        # Reordenar según schema canónico
        return df.select(list(CANONICAL_V4_SCHEMA.keys()))

    def _cast_to_schema(
        self,
        df: pl.DataFrame,
        schema: dict[str, pl.DataType],
    ) -> pl.DataFrame:
        """Cast defensivo de todas las columnas al schema objetivo."""
        casts = []
        for col, dtype in schema.items():
            if col in df.columns:
                if df[col].dtype != dtype:
                    casts.append(pl.col(col).cast(dtype))
        if casts:
            df = df.with_columns(casts)
        return df

    # ── Métodos privados: auditoría ───────────────────────────────────────────

    def _count_raw_rows(self, feed: str) -> int:
        """Cuenta filas en todas las particiones raw de un feed, de forma lazy."""
        raw_root = self._raw_root(feed)
        files = list(raw_root.rglob("*.parquet"))
        if not files:
            return 0
        try:
            return pl.scan_parquet([str(f) for f in files]).select(
                pl.len().alias("n")
            ).collect().item()
        except Exception:
            return -1

    def _count_agg_rows(self, feed: str) -> int:
        """Cuenta filas en el parquet agregado."""
        agg_path = self._agg_path(feed)
        if not agg_path.exists():
            return 0
        try:
            return pl.scan_parquet(str(agg_path)).select(
                pl.len().alias("n")
            ).collect().item()
        except Exception:
            return -1

    def _verify_canonical_schema(self) -> tuple[bool, list[str]]:
        """
        Verifica que el parquet canónico v4 de OHLCV tenga las 24 columnas
        canónicas con los dtypes correctos.

        Returns:
            (ok: bool, errors: list[str])
        """
        agg_path = self._agg_path(_FEED_OHLCV)
        if not agg_path.exists():
            return False, ["Parquet canónico v4 no existe"]

        errors: list[str] = []
        try:
            actual_schema = pl.scan_parquet(str(agg_path)).schema

            for col, expected_dtype in CANONICAL_V4_SCHEMA.items():
                if col not in actual_schema:
                    errors.append(f"Columna faltante: '{col}'")
                elif actual_schema[col] != expected_dtype:
                    errors.append(
                        f"Dtype incorrecto en '{col}': "
                        f"esperado {expected_dtype}, encontrado {actual_schema[col]}"
                    )
        except Exception as exc:
            errors.append(f"Error al leer parquet: {exc}")

        return (len(errors) == 0), errors

    # ── Métodos privados: utilidades ──────────────────────────────────────────

    @staticmethod
    def _parse_hive_int(dirname: str, prefix: str) -> int | None:
        """Extrae el valor entero de un directorio Hive como year=2025 → 2025."""
        try:
            return int(dirname.removeprefix(prefix))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _sha256_paths(paths: list[Path]) -> str:
        """SHA256 determinista sobre los paths escritos (no el contenido — eficiencia)."""
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(str(p).encode())
        return h.hexdigest()

    @staticmethod
    def _filter_partitions_by_date(
        files: list[Path],
        start: datetime | None,
        end:   datetime | None,
    ) -> list[Path]:
        """
        Filtra archivos de partición a nivel de filesystem parseando
        year= y month= del path. Evita leer particiones fuera del rango temporal.
        """
        filtered = []
        _year_re  = re.compile(r"year=(\d{4})")
        _month_re = re.compile(r"month=(\d{2})")

        for f in files:
            path_str = str(f)
            y_match = _year_re.search(path_str)
            m_match = _month_re.search(path_str)
            if not (y_match and m_match):
                filtered.append(f)  # incluir si no se puede parsear
                continue

            year  = int(y_match.group(1))
            month = int(m_match.group(1))

            # Inicio de la partición = primer día del mes
            part_start = datetime(year, month, 1, tzinfo=timezone.utc)
            # Fin de la partición = último día del mes
            part_end = _last_day_of_month(year, month)

            # Incluir si hay solapamiento con el rango solicitado
            if start is not None and part_end < start:
                continue
            if end is not None and part_start > end:
                continue
            filtered.append(f)

        return filtered


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES DE MÓDULO
# ══════════════════════════════════════════════════════════════════════════════

def _to_utc_ms_datetime(dt: datetime) -> datetime:
    """Garantiza que un datetime sea UTC, devolviendo datetime tz-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _last_day_of_month(year: int, month: int) -> datetime:
    """Retorna el último momento del mes dado como datetime UTC."""
    # Primer día del mes siguiente - 1 microsegundo
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return next_month - timedelta(microseconds=1)


def _dir_size_bytes(path: Path) -> int:
    """Tamaño total en bytes de todos los archivos bajo `path`."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY — construcción rápida desde variables de entorno SPEL
# ══════════════════════════════════════════════════════════════════════════════

def harvester_from_env(activo: str, **kwargs) -> SPELDataHarvester:
    """
    Construye un SPELDataHarvester leyendo SPEL_PROD desde variables de entorno
    (las mismas que configura la Celda 4 del Launcher).

    Args:
        activo: Activo canónico ("NVDA", "BTC", "XAU", "NIFTY50").
        **kwargs: Argumentos adicionales pasados a SPELDataHarvester.__init__.

    Returns:
        SPELDataHarvester apuntando a SPEL_v8_PROD/.

    Example:
        >>> harvester = harvester_from_env("NVDA")
        >>> result = harvester.harvest_ohlcv(df_from_adapter)
    """
    prod_root = Path(
        os.environ.get("SPEL_PROD", "/content/drive/MyDrive/SPEL_v8_PROD")
    )
    return SPELDataHarvester(root=prod_root, activo=activo, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST (ejecutar directamente para verificar que la clase funciona)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.DEBUG)
    print("=" * 65)
    print("  🧪  SPELDataHarvester — Self-Test v22")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        for activo in ("NVDA", "BTC"):
            print(f"\n  ▶  {activo}")
            h = SPELDataHarvester(root=root, activo=activo, strict_schema=False)
            print(f"     λ={h.lambda_days}d")

            # ── Crear DataFrames de prueba ──────────────────────────────────
            n = 500
            dates_ms = [
                datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
                for i in range(n)
            ]

            df_ohlcv = pl.DataFrame({
                "date":   pl.Series(dates_ms).cast(pl.Datetime("ms", "UTC")),
                "open":   pl.Series([100.0 + i * 0.1 for i in range(n)]),
                "high":   pl.Series([101.0 + i * 0.1 for i in range(n)]),
                "low":    pl.Series([99.0  + i * 0.1 for i in range(n)]),
                "close":  pl.Series([100.5 + i * 0.1 for i in range(n)]),
                "volume": pl.Series([1_000_000.0 + i * 100 for i in range(n)]),
            })

            df_gdelt = pl.DataFrame({
                "date":             pl.Series(dates_ms).cast(pl.Datetime("ms", "UTC")),
                "goldstein_geo":    pl.Series([-2.5] * n),
                "n_events_ohlcv":   pl.Series([42.0] * n),
                "vitality_tesla":   pl.Series([3.1] * n),
                "mass_panic_index": pl.Series([0.12] * n),
                "fear_momentum":    pl.Series([0.08] * n),
            })

            # ── Harvest ────────────────────────────────────────────────────
            r_ohlcv = h.harvest_ohlcv(df_ohlcv)
            print(f"     {r_ohlcv}")
            assert r_ohlcv.ok, f"harvest_ohlcv falló: {r_ohlcv.error}"

            r_gdelt = h.harvest_gdelt(df_gdelt)
            print(f"     {r_gdelt}")
            assert r_gdelt.ok, f"harvest_gdelt falló: {r_gdelt.error}"

            # ── Consolidar ─────────────────────────────────────────────────
            df_canon = h.consolidate_ohlcv()
            assert len(df_canon) == n, f"Filas esperadas {n}, obtenidas {len(df_canon)}"
            assert df_canon["date"].dtype == pl.Datetime("ms", "UTC"), "dtype incorrecto"
            assert set(CANONICAL_V4_SCHEMA.keys()).issubset(set(df_canon.columns)), \
                "Columnas canónicas faltantes"
            print(f"     consolidate_ohlcv: {len(df_canon)} rows · {len(df_canon.columns)} cols ✅")

            # ── Scan lazy ──────────────────────────────────────────────────
            start = datetime(2025, 3, 1, tzinfo=timezone.utc)
            end   = datetime(2025, 5, 31, tzinfo=timezone.utc)
            lf = h.scan_ohlcv_raw(start=start, end=end)
            df_rango = lf.collect()
            assert len(df_rango) > 0, "Scan de rango temporal devolvió 0 filas"
            assert df_rango["date"].min() >= start, "Filtro start no aplicado"
            print(f"     scan_ohlcv_raw({start.date()} → {end.date()}): {len(df_rango)} rows ✅")

            # ── Pruning ────────────────────────────────────────────────────
            # Con cutoff 30 días deberían eliminarse particiones de principio de 2025
            prune_r = h.prune(cutoff_days=30, dry_run=False)
            print(f"     {prune_r}")
            assert prune_r.ok, f"prune falló: {prune_r.errors}"

            # Verificar que el agregado sobrevivió al pruning
            canon_path = h._agg_path(_FEED_OHLCV)
            assert canon_path.exists(), "El parquet canónico fue eliminado por el pruning ❌"
            print("     Parquet canónico sobrevivió al pruning ✅")

            # ── Auditoría ──────────────────────────────────────────────────
            audit = h.audit()
            print(f"     {audit.summary()}")

        print("\n" + "=" * 65)
        print("  ✅  Todos los tests pasaron — SPELDataHarvester operacional")
        print("=" * 65)
