# ══════════════════════════════════════════════════════════════════════════════
# spel_bulk_harvester.py
# SPEL v23 — Bulk Historical Bootstrapper (Quota-Aware · Polars Output)
#
# Autor  : Abraham Fuenmayor
# Versión: v23.0.0 · 04 Mar 2026
#
# PROPÓSITO:
#   Descarga masiva de historia sin agotar cuotas de API, produciendo
#   DataFrames 100% compatibles con SPELDataHarvester.harvest_ohlcv()
#   y SPELDataHarvester.harvest_gdelt() (OHLCV_RAW_SCHEMA · GDELT_RAW_SCHEMA).
#
# FUENTES SOPORTADAS:
#   OHLCV  ─ stooq  : pandas_datareader → NVDA · XAU (GC.F)
#   OHLCV  ─ binance: REST público      → BTC (BTCUSDT · klines diarios)
#   GDELT  ─ bulk   : HTTP masterlist   → CSV 15-min agregados a daily
#
# REGLAS ACTIVAS HEREDADAS DEL HARVESTER:
#   Regla  4 : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla  5 : Parquets v4 = schema canónico exacto · Source of Truth
#   Regla 22 : detectar_col_fecha() SIEMPRE antes de operar parquets raw
#   Regla 24 : SPELAdapterChain es la única vía de acceso en PROD
#              (este módulo es solo para bootstrap inicial — NO usar en prod)
#
# COMPATIBILIDAD DE SCHEMA:
#   bootstrap_ohlcv() → OHLCV_RAW_SCHEMA (6 cols)
#     date · open · high · low · close · volume
#   bootstrap_gdelt() → GDELT_RAW_SCHEMA (6 cols)
#     date · goldstein_geo · n_events_ohlcv · vitality_tesla
#     · mass_panic_index · fear_momentum
#
# ESTRATEGIA ANTI-QUOTA:
#   ─ GDELT : caché local de ZIPs + sleep configurable entre requests
#             + descarga en batches diarios (96 archivos/día → 1 aggregado)
#   ─ Binance: paginación de 1000 velas por request con retry exponencial
#   ─ Stooq : sin límite conocido; una sola request por activo
#
# PROHIBIDO (heredado del harvester):
#   yfinance · datetime.utcnow() · conversión implícita de timezone
#   pandas como output final (solo como buffer interno de parseo)
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import polars as pl

# ── Logging ───────────────────────────────────────────────────────────────────
_log = logging.getLogger("spel.bulk_harvester")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")
    )
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

# Activos válidos y sus tickers por fuente
_STOOQ_TICKERS: dict[str, str] = {
    "NVDA":    "NVDA.US",
    "XAU":     "GC.F",      # Gold Continuous Futures en Stooq
    "NIFTY50": "^NIF",      # Stooq usa ^NIF para Nifty50
}

_BINANCE_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
}

# Palabras clave para filtrar eventos GDELT por activo
# Buscadas en Actor1Name y Actor2Name (case-insensitive)
_GDELT_ACTOR_KEYWORDS: dict[str, list[str]] = {
    "NVDA":    ["NVIDIA", "NVDA", "GRAPHICS", "GPU", "JENSEN HUANG"],
    "BTC":     ["BITCOIN", "CRYPTOCURRENCY", "CRYPTO", "BLOCKCHAIN", "SATOSHI"],
    "XAU":     ["GOLD", "BULLION", "PRECIOUS METAL", "COMEX", "XAU"],
    "NIFTY50": ["INDIA", "NSE", "NIFTY", "BSE", "SENSEX", "MUMBAI"],
}

# Umbral de GoldsteinScale para considerar un evento "extremo negativo" (mass panic)
_GOLDSTEIN_PANIC_THRESHOLD: float = -5.0

# GDELT v2 bulk endpoint
_GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
_GDELT_BASE_URL   = "http://data.gdeltproject.org/gdeltv2/"

# Columnas que necesitamos del export CSV de GDELT 2.0 (índices 0-based)
# Ref: https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
_GDELT_COLS = {
    "SQLDATE":       1,    # YYYYMMDD
    "Actor1Name":    6,    # nombre actor 1
    "Actor2Name":    16,   # nombre actor 2
    "GoldsteinScale":30,   # escala Goldstein [-10, +10]
    "NumMentions":   31,   # menciones totales
    "NumSources":    33,   # fuentes únicas
    "NumArticles":   34,   # artículos únicos
    "AvgTone":       35,   # tono promedio [-100, +100]
    "QuadClass":     29,   # 1=VerbCoop 2=MatCoop 3=VerbConf 4=MatConf
}

# Binance klines endpoint (API pública — sin auth)
_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_BINANCE_MAX_LIMIT  = 1000   # máximo de velas por request

# Rate limiting por fuente (segundos entre requests)
_SLEEP_GDELT_BETWEEN_FILES: float = 0.25   # 4 req/s — dentro de política GDELT
_SLEEP_BINANCE_RETRY_BASE:  float = 2.0    # base para exponential backoff
_MAX_RETRIES: int = 3


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES DE RESULTADO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BootstrapResult:
    """Resultado inmutable de una operación de bootstrap bulk."""
    activo:      str
    feed:        Literal["ohlcv", "gdelt"]
    source:      str                         # "stooq" · "binance" · "gdelt_bulk"
    rows:        int
    date_start:  datetime
    date_end:    datetime
    ok:          bool
    warnings:    list[str] = field(default_factory=list)
    error:       str = ""

    def __str__(self) -> str:
        status = "✅" if self.ok else "❌"
        span = f"{self.date_start.date()} → {self.date_end.date()}"
        warn = f" ⚠️ {len(self.warnings)}w" if self.warnings else ""
        return (
            f"{status} BootstrapResult({self.activo}/{self.source}) "
            f"{self.rows} rows · {span}{warn}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLASE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class SPELBulkHarvester:
    """
    Descargador masivo de historia para bootstrap del Data Lake SPEL.

    Produce DataFrames Polars estrictamente compatibles con:
      - SPELDataHarvester.harvest_ohlcv()  → OHLCV_RAW_SCHEMA
      - SPELDataHarvester.harvest_gdelt()  → GDELT_RAW_SCHEMA

    ADVERTENCIA: Este módulo es solo para bootstrap inicial.
    En producción, el acceso externo va EXCLUSIVAMENTE por SPELAdapterChain
    (Regla 24). Nunca instanciar SPELBulkHarvester en el pipeline de prod.

    Parámetros:
        cache_dir  : Directorio para cachear ZIPs de GDELT descargados.
                     Evita re-descargas en runs incrementales.
        sleep_gdelt: Segundos de pausa entre archivos GDELT (anti-quota).
        verbose    : Activa logs DEBUG de descarga.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        sleep_gdelt: float = _SLEEP_GDELT_BETWEEN_FILES,
        verbose: bool = False,
    ) -> None:
        self._cache = Path(cache_dir) if cache_dir else Path("/tmp/spel_gdelt_cache")
        self._cache.mkdir(parents=True, exist_ok=True)
        self._sleep = sleep_gdelt
        if verbose:
            _log.setLevel(logging.DEBUG)
        _log.info(
            "SPELBulkHarvester init — cache=%s sleep_gdelt=%.2fs",
            self._cache, self._sleep,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # API PÚBLICA
    # ══════════════════════════════════════════════════════════════════════════

    def bootstrap_ohlcv(
        self,
        activo: str,
        source: Literal["stooq", "binance"],
        years: int = 5,
    ) -> pl.DataFrame:
        """
        Descarga historia OHLCV diaria y devuelve un DataFrame compatible
        con OHLCV_RAW_SCHEMA para inyección directa en harvest_ohlcv().

        Schema de salida (6 cols · OHLCV_RAW_SCHEMA):
            date   : pl.Datetime("ms", "UTC")
            open   : pl.Float64
            high   : pl.Float64
            low    : pl.Float64
            close  : pl.Float64
            volume : pl.Float64

        Args:
            activo : Activo canónico. Stooq admite NVDA/XAU/NIFTY50;
                     Binance admite BTC.
            source : "stooq" o "binance".
            years  : Años de historia hacia atrás desde hoy.

        Returns:
            pl.DataFrame ordenado por date ascendente, sin nulls en OHLCV.

        Raises:
            ValueError  : activo/source incompatibles o no soportados.
            RuntimeError: fallo de descarga tras MAX_RETRIES intentos.
        """
        activo = activo.upper()
        end_dt   = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_dt = end_dt - timedelta(days=years * 365)

        if source == "stooq":
            return self._fetch_stooq(activo, start_dt, end_dt)
        elif source == "binance":
            return self._fetch_binance(activo, start_dt, end_dt)
        else:
            raise ValueError(
                f"source '{source}' no soportado. Opciones: 'stooq', 'binance'."
            )

    def bootstrap_gdelt(
        self,
        activo: str,
        months: int = 6,
    ) -> pl.DataFrame:
        """
        Descarga masterfiles GDELT v2 Bulk HTTP, filtra por actor/activo,
        agrega a granularidad diaria y devuelve un DataFrame compatible
        con GDELT_RAW_SCHEMA para inyección directa en harvest_gdelt().

        Schema de salida (6 cols · GDELT_RAW_SCHEMA):
            date             : pl.Datetime("ms", "UTC")
            goldstein_geo    : pl.Float64  ← avg(GoldsteinScale) daily
            n_events_ohlcv   : pl.Float64  ← sum(NumArticles) daily
            vitality_tesla   : pl.Float64  ← avg(NumSources) daily
            mass_panic_index : pl.Float64  ← frac eventos GS < -5 daily
            fear_momentum    : pl.Float64  ← normalized negative AvgTone daily

        Estrategia anti-quota:
            1. Descarga masterfilelist.txt para identificar archivos del rango.
            2. Agrupa en batches diarios (hasta 96 archivos/día).
            3. Cachea ZIPs localmente → re-runs incrementales sin re-descarga.
            4. sleep(_SLEEP_GDELT_BETWEEN_FILES) entre cada archivo.

        Args:
            activo : Activo canónico. Filtra por _GDELT_ACTOR_KEYWORDS[activo].
            months : Meses de historia hacia atrás desde hoy.

        Returns:
            pl.DataFrame ordenado por date ascendente, una fila por día.
            Días sin eventos del activo aparecen como null → fill_null(0.0).

        Raises:
            ValueError  : activo no soportado.
            RuntimeError: fallo en descarga del masterfile.
        """
        activo = activo.upper()
        if activo not in _GDELT_ACTOR_KEYWORDS:
            raise ValueError(
                f"activo '{activo}' no tiene keywords GDELT definidos. "
                f"Disponibles: {list(_GDELT_ACTOR_KEYWORDS)}"
            )

        end_dt   = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_dt = end_dt - timedelta(days=months * 30)

        return self._fetch_gdelt_bulk(activo, start_dt, end_dt)

    # ══════════════════════════════════════════════════════════════════════════
    # BACKENDS PRIVADOS: OHLCV
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_stooq(
        self,
        activo: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pl.DataFrame:
        """Descarga vía pandas_datareader (Stooq) y convierte a OHLCV_RAW_SCHEMA."""
        if activo not in _STOOQ_TICKERS:
            raise ValueError(
                f"Activo '{activo}' no tiene ticker Stooq. "
                f"Activos Stooq disponibles: {list(_STOOQ_TICKERS)}"
            )

        try:
            import pandas_datareader.data as web   # import diferido — no en prod
        except ImportError:
            raise RuntimeError(
                "pandas_datareader no instalado. "
                "Ejecuta: pip install pandas-datareader --break-system-packages"
            )

        ticker = _STOOQ_TICKERS[activo]
        _log.info("Stooq fetch — %s (%s) · %s → %s",
                  activo, ticker, start_dt.date(), end_dt.date())

        try:
            df_pd = web.DataReader(
                ticker,
                "stooq",
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
            )
        except Exception as exc:
            raise RuntimeError(f"Stooq descarga fallida para {ticker}: {exc}") from exc

        if df_pd is None or df_pd.empty:
            raise RuntimeError(
                f"Stooq devolvió DataFrame vacío para {ticker}. "
                "Verifica el ticker o el rango de fechas."
            )

        # Stooq retorna DatetimeIndex + columnas Open/High/Low/Close/Volume
        # (mayúsculas) ordenado descendente — normalizar a OHLCV_RAW_SCHEMA
        df_pd = df_pd.sort_index(ascending=True)
        df_pd.index.name = "date"
        df_pd = df_pd.reset_index()
        df_pd.columns = [c.lower() for c in df_pd.columns]

        # Conversión a Polars: pandas → polars → normalizar datetime a UTC ms
        df_pl = pl.from_pandas(df_pd)
        df_pl = self._normalize_ohlcv(df_pl, activo, "stooq")

        _log.info("Stooq OK — %s · %d filas · %s → %s",
                  activo, len(df_pl),
                  df_pl["date"].min(), df_pl["date"].max())
        return df_pl

    def _fetch_binance(
        self,
        activo: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pl.DataFrame:
        """Descarga vía Binance REST público (klines diarios) con paginación."""
        if activo not in _BINANCE_SYMBOLS:
            raise ValueError(
                f"Activo '{activo}' no tiene símbolo Binance. "
                f"Activos Binance disponibles: {list(_BINANCE_SYMBOLS)}"
            )

        symbol = _BINANCE_SYMBOLS[activo]
        _log.info("Binance fetch — %s (%s) · %s → %s",
                  activo, symbol, start_dt.date(), end_dt.date())

        # Binance klines: timestamps en milisegundos
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms   = int(end_dt.timestamp() * 1000)

        all_klines: list[list] = []
        current_start = start_ms

        while current_start < end_ms:
            import json
            url = (
                f"{_BINANCE_KLINES_URL}"
                f"?symbol={symbol}&interval=1d"
                f"&startTime={current_start}&endTime={end_ms}"
                f"&limit={_BINANCE_MAX_LIMIT}"
            )
            data = self._http_get_json(url, source="binance")

            if not data:
                break

            all_klines.extend(data)

            # La última vela retornada marca el nuevo start para paginación
            last_open_time = int(data[-1][0])
            next_start     = last_open_time + 86_400_000  # +1 día en ms

            if next_start <= current_start:
                break  # guard contra loop infinito
            current_start = next_start

            if len(data) < _BINANCE_MAX_LIMIT:
                break  # llegamos al final del rango

            _log.debug("Binance pagination — %d klines acumuladas", len(all_klines))

        if not all_klines:
            raise RuntimeError(
                f"Binance devolvió 0 klines para {symbol} "
                f"en el rango {start_dt.date()} → {end_dt.date()}"
            )

        # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
        dates   = [int(k[0]) for k in all_klines]   # ms UTC
        opens   = [float(k[1]) for k in all_klines]
        highs   = [float(k[2]) for k in all_klines]
        lows    = [float(k[3]) for k in all_klines]
        closes  = [float(k[4]) for k in all_klines]
        volumes = [float(k[5]) for k in all_klines]

        df_pl = pl.DataFrame({
            "date":   pl.Series(dates, dtype=pl.Int64),
            "open":   pl.Series(opens,   dtype=pl.Float64),
            "high":   pl.Series(highs,   dtype=pl.Float64),
            "low":    pl.Series(lows,    dtype=pl.Float64),
            "close":  pl.Series(closes,  dtype=pl.Float64),
            "volume": pl.Series(volumes, dtype=pl.Float64),
        })

        # Int64 ms → Datetime("ms", "UTC")
        df_pl = df_pl.with_columns(
            pl.col("date")
              .cast(pl.Datetime("ms", "UTC"))
        )

        df_pl = (
            df_pl
            .sort("date")
            .unique(subset=["date"], keep="last", maintain_order=True)
            .drop_nulls(subset=["open", "close"])
        )

        _log.info("Binance OK — %s · %d filas · %s → %s",
                  activo, len(df_pl),
                  df_pl["date"].min(), df_pl["date"].max())
        return df_pl

    def _normalize_ohlcv(
        self,
        df: pl.DataFrame,
        activo: str,
        source: str,
    ) -> pl.DataFrame:
        """
        Normaliza un DataFrame OHLCV crudo (de cualquier fuente) a
        OHLCV_RAW_SCHEMA exacto. Maneja conversión de fecha string/date/datetime.
        """
        warnings_: list[str] = []

        # ── 1. Detectar y normalizar columna de fecha ─────────────────────────
        date_candidates = ["date", "Date", "DATE", "timestamp", "time", "Time"]
        date_col = next((c for c in date_candidates if c in df.columns), None)
        if date_col is None:
            raise ValueError(
                f"No se encontró columna de fecha en DataFrame de {source}/{activo}. "
                f"Columnas disponibles: {df.columns}"
            )
        if date_col != "date":
            df = df.rename({date_col: "date"})

        # Normalizar a Datetime("ms", "UTC")
        current_dtype = df["date"].dtype
        if current_dtype == pl.Date:
            df = df.with_columns(
                pl.col("date")
                  .cast(pl.Datetime("ms"))
                  .dt.replace_time_zone("UTC")
            )
        elif current_dtype in (pl.Utf8, pl.String):
            df = df.with_columns(
                pl.col("date")
                  .str.to_datetime(format="%Y-%m-%d", time_unit="ms")
                  .dt.replace_time_zone("UTC")
            )
        elif current_dtype == pl.Datetime("us", None):
            # pandas default: microseconds naive → convertir a ms UTC
            df = df.with_columns(
                pl.col("date")
                  .dt.replace_time_zone("UTC")
                  .cast(pl.Datetime("ms", "UTC"))
            )
        elif current_dtype == pl.Datetime("us", "UTC"):
            df = df.with_columns(
                pl.col("date").cast(pl.Datetime("ms", "UTC"))
            )
        elif current_dtype != pl.Datetime("ms", "UTC"):
            # Intento genérico
            try:
                df = df.with_columns(
                    pl.col("date").cast(pl.Datetime("ms", "UTC"))
                )
            except Exception:
                raise ValueError(
                    f"No se puede convertir 'date' dtype={current_dtype} "
                    f"a Datetime('ms','UTC') para {source}/{activo}"
                )

        # ── 2. Normalizar nombres de columnas OHLCV ───────────────────────────
        col_map = {
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
            "Adj Close": "close",   # Stooq a veces retorna adjusted
        }
        rename_needed = {k: v for k, v in col_map.items() if k in df.columns}
        if rename_needed:
            df = df.rename(rename_needed)

        # ── 3. Verificar columnas obligatorias ────────────────────────────────
        required = ["date", "open", "high", "low", "close", "volume"]
        missing  = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(
                f"Columnas OHLCV faltantes en {source}/{activo}: {missing}. "
                f"Columnas disponibles: {df.columns}"
            )

        # ── 4. Cast a Float64 + limpiar filas nulas en precio ─────────────────
        float_cols = ["open", "high", "low", "close", "volume"]
        df = df.with_columns([
            pl.col(c).cast(pl.Float64) for c in float_cols
        ])
        df = df.drop_nulls(subset=["open", "high", "low", "close"])

        # ── 5. Seleccionar solo columnas canónicas, ordenar ───────────────────
        df = df.select(required).sort("date")

        return df

    # ══════════════════════════════════════════════════════════════════════════
    # BACKEND PRIVADO: GDELT BULK
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_gdelt_bulk(
        self,
        activo: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pl.DataFrame:
        """
        Descarga masterfilelist, filtra por rango temporal, descarga archivos
        export.CSV.zip en batches diarios, filtra por actor, agrega a daily.

        Mapping GDELT → GDELT_RAW_SCHEMA:
            goldstein_geo    = avg(GoldsteinScale)              por día
            n_events_ohlcv   = sum(NumArticles)                 por día
            vitality_tesla   = avg(NumSources)                  por día
            mass_panic_index = frac(GS < -5.0)                  por día
            fear_momentum    = clip(-avg(AvgTone)/100, 0, 1)    por día
        """
        keywords = _GDELT_ACTOR_KEYWORDS[activo]
        _log.info(
            "GDELT bulk — %s · %s → %s · keywords=%s",
            activo, start_dt.date(), end_dt.date(), keywords,
        )

        # ── 1. Obtener y parsear masterfilelist ───────────────────────────────
        _log.info("Descargando masterfilelist.txt ...")
        try:
            master_text = self._http_get_text(_GDELT_MASTER_URL, source="gdelt_master")
        except Exception as exc:
            raise RuntimeError(f"No se pudo obtener masterfilelist GDELT: {exc}") from exc

        # Cada línea: "SIZE HASH URL" donde URL contiene YYYYMMDDHHMMSS.export.CSV.zip
        export_urls = self._parse_master_export_urls(
            master_text, start_dt, end_dt
        )

        if not export_urls:
            _log.warning(
                "GDELT masterlist sin archivos export en rango %s → %s",
                start_dt.date(), end_dt.date(),
            )
            return self._empty_gdelt_frame()

        _log.info("Archivos export identificados: %d", len(export_urls))

        # ── 2. Descargar + parsear por batches diarios ────────────────────────
        # Agrupar URLs por fecha (YYYYMMDD del nombre de archivo)
        daily_batches: dict[str, list[str]] = {}
        for url in export_urls:
            fname = url.split("/")[-1]          # YYYYMMDDHHMMSS.export.CSV.zip
            date_str = fname[:8]                 # YYYYMMDD
            daily_batches.setdefault(date_str, []).append(url)

        all_day_records: list[dict] = []

        for date_str in sorted(daily_batches.keys()):
            day_urls = daily_batches[date_str]
            day_rows = self._process_gdelt_day(
                date_str, day_urls, keywords, activo
            )
            if day_rows:
                all_day_records.extend(day_rows)
            _log.debug("GDELT day=%s → %d events filtrados", date_str, len(day_rows))

        if not all_day_records:
            _log.warning("GDELT bulk: 0 eventos encontrados para %s", activo)
            return self._empty_gdelt_frame()

        # ── 3. Construir DataFrame y agregar a daily ──────────────────────────
        df = pl.DataFrame(all_day_records)
        df = self._aggregate_gdelt_daily(df, activo)

        _log.info(
            "GDELT bulk OK — %s · %d días · %s → %s",
            activo, len(df), df["date"].min(), df["date"].max(),
        )
        return df

    def _parse_master_export_urls(
        self,
        master_text: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[str]:
        """Extrae URLs de archivos .export.CSV.zip dentro del rango temporal."""
        urls = []
        start_str = start_dt.strftime("%Y%m%d")
        end_str   = end_dt.strftime("%Y%m%d")

        for line in master_text.strip().splitlines():
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            url = parts[-1]
            if ".export.CSV.zip" not in url:
                continue
            # Extraer fecha del nombre de archivo
            fname = url.split("/")[-1]
            if len(fname) < 8:
                continue
            file_date = fname[:8]
            if start_str <= file_date <= end_str:
                urls.append(url)
        return urls

    def _process_gdelt_day(
        self,
        date_str: str,
        urls: list[str],
        keywords: list[str],
        activo: str,
    ) -> list[dict]:
        """
        Descarga y parsea todos los archivos de un día GDELT,
        filtra por keywords y retorna lista de dicts de eventos.
        Usa caché local para evitar re-descargas.
        """
        day_rows: list[dict] = []
        kw_upper = [k.upper() for k in keywords]

        for url in urls:
            fname    = url.split("/")[-1]
            cached   = self._cache / fname

            # Intentar caché antes de red
            raw_bytes = self._load_or_download(url, cached)
            if raw_bytes is None:
                continue

            time.sleep(self._sleep)   # anti-quota throttle

            # Descomprimir y parsear CSV en memoria
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    csv_name = next(
                        (n for n in zf.namelist() if n.endswith(".CSV")), None
                    )
                    if csv_name is None:
                        continue
                    csv_bytes = zf.read(csv_name)
            except zipfile.BadZipFile:
                _log.warning("ZIP corrupto o incompleto: %s — saltando", fname)
                continue

            # Parsear líneas del CSV (tab-separated, sin header)
            try:
                rows = self._parse_gdelt_csv_bytes(csv_bytes, kw_upper)
                day_rows.extend(rows)
            except Exception as exc:
                _log.warning("Error parseando %s: %s — saltando", fname, exc)
                continue

        return day_rows

    def _parse_gdelt_csv_bytes(
        self,
        csv_bytes: bytes,
        keywords_upper: list[str],
    ) -> list[dict]:
        """
        Parsea bytes de un CSV de export GDELT v2 (tab-separated, sin header).
        Extrae columnas relevantes y filtra por keywords en Actor1Name/Actor2Name.
        """
        rows: list[dict] = []
        text = csv_bytes.decode("utf-8", errors="replace")

        for line in text.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < 36:
                continue

            try:
                actor1 = fields[_GDELT_COLS["Actor1Name"]].upper()
                actor2 = fields[_GDELT_COLS["Actor2Name"]].upper()

                # Filtro por keywords (Actor1 o Actor2)
                match = any(
                    kw in actor1 or kw in actor2
                    for kw in keywords_upper
                )
                if not match:
                    continue

                sql_date     = fields[_GDELT_COLS["SQLDATE"]].strip()
                goldstein    = fields[_GDELT_COLS["GoldsteinScale"]].strip()
                num_articles = fields[_GDELT_COLS["NumArticles"]].strip()
                num_sources  = fields[_GDELT_COLS["NumSources"]].strip()
                avg_tone     = fields[_GDELT_COLS["AvgTone"]].strip()

                rows.append({
                    "date_str":    sql_date,
                    "goldstein":   float(goldstein)    if goldstein    else 0.0,
                    "num_articles":float(num_articles) if num_articles else 0.0,
                    "num_sources": float(num_sources)  if num_sources  else 0.0,
                    "avg_tone":    float(avg_tone)     if avg_tone     else 0.0,
                })
            except (ValueError, IndexError):
                continue  # fila malformada — skip silencioso

        return rows

    def _aggregate_gdelt_daily(
        self,
        df_raw: pl.DataFrame,
        activo: str,
    ) -> pl.DataFrame:
        """
        Agrega registros de eventos a granularidad diaria y mapea al
        GDELT_RAW_SCHEMA canónico del harvester.

        Mappings:
            goldstein_geo    = avg(goldstein)      — estabilidad geopolítica
            n_events_ohlcv   = sum(num_articles)   — volumen informacional
            vitality_tesla   = avg(num_sources)    — amplificación de señal
            mass_panic_index = frac(goldstein < -5)— concentración de eventos
                                                     extremos negativos
            fear_momentum    = clip(-avg_tone/100, 0, 1)
                                                   — presión de sentimiento
                                                     negativo normalizada
        """
        # Convertir date_str YYYYMMDD → Datetime("ms","UTC")
        df_raw = df_raw.with_columns(
            pl.col("date_str")
              .str.strptime(pl.Date, format="%Y%m%d")
              .cast(pl.Datetime("ms"))
              .dt.replace_time_zone("UTC")
              .alias("date")
        )

        # Columna auxiliar: bandera de panic (goldstein < umbral)
        df_raw = df_raw.with_columns(
            (pl.col("goldstein") < _GOLDSTEIN_PANIC_THRESHOLD)
              .cast(pl.Float64)
              .alias("is_panic")
        )

        # Agregación diaria
        df_agg = (
            df_raw
            .group_by("date")
            .agg([
                pl.col("goldstein").mean().alias("goldstein_geo"),
                pl.col("num_articles").sum().alias("n_events_ohlcv"),
                pl.col("num_sources").mean().alias("vitality_tesla"),
                pl.col("is_panic").mean().alias("mass_panic_index"),
                pl.col("avg_tone").mean().alias("_avg_tone_raw"),
            ])
            .sort("date")
        )

        # fear_momentum = clip(-avg_tone / 100, 0, 1)
        df_agg = df_agg.with_columns(
            pl.col("_avg_tone_raw")
              .map_elements(lambda t: max(0.0, min(1.0, -t / 100.0)),
                            return_dtype=pl.Float64)
              .alias("fear_momentum")
        ).drop("_avg_tone_raw")

        # Cast final al schema canónico y fill_null defensivo
        df_agg = df_agg.with_columns([
            pl.col("goldstein_geo").cast(pl.Float64).fill_null(0.0),
            pl.col("n_events_ohlcv").cast(pl.Float64).fill_null(0.0),
            pl.col("vitality_tesla").cast(pl.Float64).fill_null(0.0),
            pl.col("mass_panic_index").cast(pl.Float64).fill_null(0.0),
            pl.col("fear_momentum").cast(pl.Float64).fill_null(0.0),
        ])

        # Seleccionar en orden exacto del GDELT_RAW_SCHEMA
        return df_agg.select([
            "date",
            "goldstein_geo",
            "n_events_ohlcv",
            "vitality_tesla",
            "mass_panic_index",
            "fear_momentum",
        ])

    # ══════════════════════════════════════════════════════════════════════════
    # UTILIDADES HTTP
    # ══════════════════════════════════════════════════════════════════════════

    def _http_get_json(self, url: str, source: str) -> list | dict:
        """HTTP GET con retry exponencial. Retorna JSON parsed."""
        import json
        for attempt in range(_MAX_RETRIES):
            try:
                req  = Request(url, headers={"User-Agent": "SPEL-BulkHarvester/23.0"})
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (HTTPError, URLError) as exc:
                wait = _SLEEP_BINANCE_RETRY_BASE * (2 ** attempt)
                _log.warning(
                    "%s HTTP error (attempt %d/%d): %s — retry en %.1fs",
                    source, attempt + 1, _MAX_RETRIES, exc, wait,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"{source}: {_MAX_RETRIES} intentos fallidos — {exc}"
                    ) from exc
        return []

    def _http_get_text(self, url: str, source: str) -> str:
        """HTTP GET → string. Sin retry (usado para masterlist)."""
        req = Request(url, headers={"User-Agent": "SPEL-BulkHarvester/23.0"})
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _load_or_download(self, url: str, cached_path: Path) -> bytes | None:
        """Carga desde caché local si existe; si no, descarga y cachea."""
        if cached_path.exists():
            _log.debug("Cache HIT — %s", cached_path.name)
            return cached_path.read_bytes()

        try:
            req = Request(url, headers={"User-Agent": "SPEL-BulkHarvester/23.0"})
            with urlopen(req, timeout=60) as resp:
                data = resp.read()
            cached_path.write_bytes(data)
            _log.debug("Cache MISS → descargado %s (%d KB)",
                       cached_path.name, len(data) // 1024)
            return data
        except Exception as exc:
            _log.warning("Descarga fallida %s: %s — saltando", url, exc)
            return None

    @staticmethod
    def _empty_gdelt_frame() -> pl.DataFrame:
        """DataFrame vacío con GDELT_RAW_SCHEMA para retornos sin datos."""
        return pl.DataFrame(schema={
            "date":             pl.Datetime("ms", "UTC"),
            "goldstein_geo":    pl.Float64,
            "n_events_ohlcv":   pl.Float64,
            "vitality_tesla":   pl.Float64,
            "mass_panic_index": pl.Float64,
            "fear_momentum":    pl.Float64,
        })


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY RÁPIDA
# ══════════════════════════════════════════════════════════════════════════════

def bulk_harvester_from_env(**kwargs) -> SPELBulkHarvester:
    """
    Construye un SPELBulkHarvester leyendo SPEL_GDELT_CACHE del entorno.
    Si no está definida, usa /tmp/spel_gdelt_cache.

    Example::

        harvester = bulk_harvester_from_env(sleep_gdelt=0.5)
        df_ohlcv  = harvester.bootstrap_ohlcv("NVDA", source="stooq", years=3)
        df_gdelt  = harvester.bootstrap_gdelt("NVDA", months=6)
    """
    cache = Path(os.environ.get("SPEL_GDELT_CACHE", "/tmp/spel_gdelt_cache"))
    return SPELBulkHarvester(cache_dir=cache, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# DRY RUN — Verificación visual de compatibilidad de schema
#
# Ejecutar: python spel_bulk_harvester.py
#
# Descarga:
#   - 5 días de NVDA desde Stooq
#   - 1 archivo CSV de GDELT (el más reciente disponible)
#
# Salida esperada:
#   ── OHLCV_RAW_SCHEMA (Stooq/NVDA) ─────────
#   {'date': Datetime(time_unit='ms', time_zone='UTC'), 'open': Float64,
#    'high': Float64, 'low': Float64, 'close': Float64, 'volume': Float64}
#
#   ── GDELT_RAW_SCHEMA (1 archivo bulk) ──────
#   {'date': Datetime(time_unit='ms', time_zone='UTC'),
#    'goldstein_geo': Float64, 'n_events_ohlcv': Float64,
#    'vitality_tesla': Float64, 'mass_panic_index': Float64,
#    'fear_momentum': Float64}
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    SEP   = "═" * 65
    SEP_S = "─" * 65

    print(f"\n{SEP}")
    print("  🧪  SPELBulkHarvester — Dry Run v23")
    print(f"  📋  Verifica compatibilidad de schema ANTES de inyectar al Data Lake")
    print(SEP)

    # ── Setup temporal ────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp_cache:
        harvester = SPELBulkHarvester(
            cache_dir=tmp_cache,
            sleep_gdelt=0.1,   # agresivo solo para dry run (1 archivo)
            verbose=True,
        )

        # ── PARTE 1: OHLCV — Stooq / NVDA · últimos 5 días ──────────────────
        print(f"\n{SEP_S}")
        print("  [1/2] OHLCV bootstrap — Stooq · NVDA · 5 días")
        print(SEP_S)

        # 5 días de historia
        end_dt   = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_dt = end_dt - timedelta(days=10)   # pedir 10 para asegurar 5 hábiles

        try:
            df_ohlcv = harvester.bootstrap_ohlcv("NVDA", source="stooq", years=1)
            # Tomar solo los últimos 5 días para el dry run
            df_ohlcv = df_ohlcv.tail(5)

            print(f"\n  ✅ Descarga exitosa — {len(df_ohlcv)} filas")
            print(f"\n  ── OHLCV_RAW_SCHEMA (Stooq/NVDA) {'─' * 28}")
            print(f"  {df_ohlcv.schema}")
            print(f"\n  ── Preview (5 filas) {'─' * 42}")
            print(df_ohlcv)

            # Verificación explícita de compatibilidad
            OHLCV_RAW_SCHEMA_EXPECTED = {
                "date":   pl.Datetime("ms", "UTC"),
                "open":   pl.Float64,
                "high":   pl.Float64,
                "low":    pl.Float64,
                "close":  pl.Float64,
                "volume": pl.Float64,
            }
            schema_match = dict(df_ohlcv.schema) == OHLCV_RAW_SCHEMA_EXPECTED
            icon = "✅" if schema_match else "❌"
            print(f"\n  {icon} Schema OHLCV == OHLCV_RAW_SCHEMA: {schema_match}")
            if not schema_match:
                print(f"  ⚠️  Esperado : {OHLCV_RAW_SCHEMA_EXPECTED}")
                print(f"  ⚠️  Obtenido : {dict(df_ohlcv.schema)}")

        except Exception as exc:
            print(f"\n  ❌ OHLCV dry run FALLÓ: {exc}")
            print("  💡 Verifica: pip install pandas-datareader --break-system-packages")
            df_ohlcv = None

        # ── PARTE 2: GDELT — 1 archivo bulk reciente ─────────────────────────
        print(f"\n{SEP_S}")
        print("  [2/2] GDELT bootstrap — 1 archivo bulk · NVDA")
        print(SEP_S)

        try:
            # Descargar solo masterfilelist y tomar el último archivo export
            print("\n  Descargando masterfilelist.txt ...")
            master_text = harvester._http_get_text(_GDELT_MASTER_URL, "gdelt_master")
            export_urls = []
            for line in master_text.strip().splitlines()[-200:]:  # últimas 200 líneas
                parts = line.strip().split()
                if len(parts) >= 3 and ".export.CSV.zip" in parts[-1]:
                    export_urls.append(parts[-1])

            if not export_urls:
                print("  ⚠️  No se encontraron archivos export en masterlist")
            else:
                # Solo descargar el ÚLTIMO archivo disponible (dry run mínimo)
                last_url = export_urls[-1]
                fname    = last_url.split("/")[-1]
                print(f"  Procesando archivo: {fname}")

                date_str = fname[:8]
                day_rows = harvester._process_gdelt_day(
                    date_str, [last_url],
                    _GDELT_ACTOR_KEYWORDS["NVDA"],
                    "NVDA",
                )

                if not day_rows:
                    print(
                        f"\n  ⚠️  0 eventos de NVDA en {fname}. "
                        "Normal para archivos de 15min — "
                        "producción agrega múltiples archivos/día."
                    )
                    # Construir DF de ejemplo para verificar schema
                    df_gdelt = harvester._empty_gdelt_frame()
                    print(
                        "\n  ── Schema de DataFrame vacío (estructura correcta):"
                    )
                else:
                    df_raw_gdelt = pl.DataFrame(day_rows)
                    df_gdelt     = harvester._aggregate_gdelt_daily(
                        df_raw_gdelt, "NVDA"
                    )
                    print(f"\n  ✅ {len(day_rows)} eventos · {len(df_gdelt)} días agregados")

                GDELT_RAW_SCHEMA_EXPECTED = {
                    "date":             pl.Datetime("ms", "UTC"),
                    "goldstein_geo":    pl.Float64,
                    "n_events_ohlcv":   pl.Float64,
                    "vitality_tesla":   pl.Float64,
                    "mass_panic_index": pl.Float64,
                    "fear_momentum":    pl.Float64,
                }

                print(f"\n  ── GDELT_RAW_SCHEMA (bulk/NVDA) {'─' * 30}")
                print(f"  {df_gdelt.schema}")

                if len(df_gdelt) > 0:
                    print(f"\n  ── Preview {'─' * 52}")
                    print(df_gdelt)

                schema_match_gdelt = (
                    dict(df_gdelt.schema) == GDELT_RAW_SCHEMA_EXPECTED
                )
                icon = "✅" if schema_match_gdelt else "❌"
                print(
                    f"\n  {icon} Schema GDELT == GDELT_RAW_SCHEMA: {schema_match_gdelt}"
                )
                if not schema_match_gdelt:
                    print(f"  ⚠️  Esperado : {GDELT_RAW_SCHEMA_EXPECTED}")
                    print(f"  ⚠️  Obtenido : {dict(df_gdelt.schema)}")

        except Exception as exc:
            print(f"\n  ❌ GDELT dry run FALLÓ: {exc}")

        # ── Resumen ───────────────────────────────────────────────────────────
        print(f"\n{SEP}")
        print("  📋 Próximos pasos si ambos schemas son ✅:")
        print("     harvester = SPELDataHarvester(root=SPEL_PROD, activo='NVDA')")
        print("     result_ohlcv  = harvester.harvest_ohlcv(df_ohlcv)")
        print("     result_gdelt  = harvester.harvest_gdelt(df_gdelt)")
        print("     print(result_ohlcv, result_gdelt)")
        print(SEP)
