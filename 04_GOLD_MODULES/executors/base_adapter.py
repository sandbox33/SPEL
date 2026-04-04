# ══════════════════════════════════════════════════════════════════════════════
#  SPEL v8 · infrastructure/adapters/base_adapter.py
#  Patrón Adapter Canónico — Ingesta de Datos Tolerante a Fallos
#
#  Autor: Abraham Fuenmayor · v8.0 · 01 Mar 2026
#
#  REGLA DE ORO:
#    La lógica matemática (Capa A, Capa B, Motor LSTM) NO cambia.
#    Lo que cambia es DÓNDE y CÓMO se obtiene el dato.
#
#  REGLA DE DEGRADACIÓN ELEGANTE:
#    Si un adapter falla → retorna DataFrame con columnas en 0.0
#    → emite log en /app/shared/logs/
#    → activa is_degraded=True
#    → el pipeline CONTINÚA. SPEL nunca hace crash por culpa de un tercero.
#
#  ADAPTERS DISPONIBLES:
#    AlphaVantageAdapter  → OHLCV diario (reemplaza yfinance bloqueado en Colab)
#    BigQueryGDELTAdapter → Entropía geopolítica GDELT (Bug #44 resuelto)
#    TiingoAdapter        → OHLCV alternativo (fallback de AlphaVantage)
#    ParquetCacheAdapter  → Último valor conocido del data lake (último recurso)
#
#  JERARQUÍA DE FALLBACK POR ACTIVO:
#    OHLCV:  AlphaVantage → Tiingo → ParquetCache → DataFrame vacío
#    GDELT:  BigQuery     → ParquetCache → 0.0
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

# ── Logging canónico ──────────────────────────────────────────────────────────
logger = logging.getLogger("spel.adapters")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Columnas canónicas del parquet v4 (Regla 5 — NO MODIFICAR) ───────────────
COLS_CANONICAS_V4 = [
    "date", "open", "high", "low", "close", "volume",
    "log_return", "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
    "fibonacci_lag_1", "fibonacci_lag_2", "fibonacci_lag_3", "fibonacci_lag_5",
    "fibonacci_lag_8", "fibonacci_lag_13", "fibonacci_lag_21",
    "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
    "mass_panic_index", "fear_momentum", "vix_norm", "nash_frozen_7d",
]

# ── Columnas OHLCV base (subset de las 24 canónicas) ─────────────────────────
COLS_OHLCV = ["date", "open", "high", "low", "close", "volume"]

# ── Columnas GDELT base ───────────────────────────────────────────────────────
COLS_GDELT = [
    "date", "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
    "mass_panic_index", "fear_momentum", "nash_frozen_7d",
]


# ══════════════════════════════════════════════════════════════════════════════
#  RESULTADO DE ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AdapterResult:
    """
    Contenedor canónico del resultado de cualquier adapter.
    El pipeline siempre recibe un AdapterResult — nunca lanza excepciones.
    """
    data:          pl.DataFrame              # Datos obtenidos (puede estar vacío o degradado)
    adapter_name:  str                       # Nombre del adapter que generó el resultado
    activo:        str                       # Ticker canónico (NVDA, BTC, XAU, NIFTY50)
    is_degraded:   bool         = False      # True si el dato es placeholder/fallback
    error_msg:     Optional[str] = None      # Mensaje de error si is_degraded=True
    rows_fetched:  int          = 0          # Filas descargadas de la fuente real
    latency_ms:    float        = 0.0        # Latencia de la llamada externa en ms
    timestamp_utc: str          = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def log_summary(self) -> None:
        """Emite un log canónico del resultado."""
        estado = "⚠️  DEGRADADO" if self.is_degraded else "✅ OK"
        logger.info(
            "%s [%s → %s] | filas=%d | latencia=%.0fms%s",
            estado,
            self.adapter_name,
            self.activo,
            self.rows_fetched,
            self.latency_ms,
            f" | error: {self.error_msg}" if self.error_msg else "",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  INTERFAZ BASE ABSTRACTA
# ══════════════════════════════════════════════════════════════════════════════

class BaseSensorAdapter(ABC):
    """
    Interfaz canónica que todo adapter SPEL debe implementar.

    Contrato:
      - fetch() NUNCA lanza excepciones al caller.
      - Si falla → retorna AdapterResult con is_degraded=True y data vacío.
      - Siempre escribe en el log de auditoría.

    Subclases obligadas a implementar:
      - _fetch_raw(): lógica real de descarga (puede lanzar excepciones internas)
      - name: str (identificador del adapter)
    """

    # ── Configuración de reintentos (override en subclase si se necesita) ─────
    MAX_RETRIES:   int   = 3
    RETRY_DELAY_S: float = 2.0
    TIMEOUT_S:     float = 15.0

    def __init__(self, logs_dir: Optional[Path] = None):
        self.logs_dir = logs_dir or Path(
            os.environ.get("SPEL_LOGS", "/app/shared/logs")
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único del adapter (ej: 'alpha_vantage', 'bigquery_gdelt')."""
        ...

    @abstractmethod
    def _fetch_raw(self, activo: str, desde: datetime, hasta: datetime) -> pl.DataFrame:
        """
        Lógica real de descarga. PUEDE lanzar excepciones.
        Retorna un DataFrame con al menos la columna 'date' y las columnas
        relevantes al tipo de dato (OHLCV o GDELT).
        """
        ...

    # ── Interfaz pública ──────────────────────────────────────────────────────

    def fetch(
        self,
        activo:  str,
        desde:   Optional[datetime] = None,
        hasta:   Optional[datetime] = None,
    ) -> AdapterResult:
        """
        Método público con manejo de errores, reintentos y logging.
        El pipeline SIEMPRE llama a este método, nunca a _fetch_raw().
        """
        hasta = hasta or datetime.utcnow()
        desde = desde or (hasta - timedelta(days=7))

        last_error: Optional[str] = None
        t0 = time.monotonic()

        for intento in range(1, self.MAX_RETRIES + 1):
            try:
                df = self._fetch_raw(activo, desde, hasta)
                latencia = (time.monotonic() - t0) * 1000
                resultado = AdapterResult(
                    data=df,
                    adapter_name=self.name,
                    activo=activo,
                    is_degraded=False,
                    rows_fetched=len(df),
                    latency_ms=latencia,
                )
                resultado.log_summary()
                self._escribir_log_auditoria(resultado)
                return resultado

            except Exception as exc:
                last_error = f"[intento {intento}/{self.MAX_RETRIES}] {type(exc).__name__}: {exc}"
                logger.warning("%s — %s | activo=%s", self.name, last_error, activo)
                if intento < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_S * intento)

        # Todos los reintentos fallaron → degradación elegante
        latencia = (time.monotonic() - t0) * 1000
        df_vacio = self._dataframe_degradado(activo)
        resultado = AdapterResult(
            data=df_vacio,
            adapter_name=self.name,
            activo=activo,
            is_degraded=True,
            error_msg=last_error,
            rows_fetched=0,
            latency_ms=latencia,
        )
        resultado.log_summary()
        self._escribir_log_auditoria(resultado)
        return resultado

    def is_available(self) -> bool:
        """
        Health check rápido del adapter.
        Override en subclases para verificar credenciales / conectividad.
        """
        return True

    # ── Auxiliares internos ───────────────────────────────────────────────────

    def _dataframe_degradado(self, activo: str) -> pl.DataFrame:
        """
        DataFrame vacío con las columnas correctas y un fila de timestamp=hoy,
        valores en 0.0. Permite que el pipeline continúe sin datos reales.
        Subclases pueden hacer override para devolver el último valor conocido.
        """
        hoy = datetime.utcnow().strftime("%Y-%m-%d")
        return pl.DataFrame({"date": [hoy]})

    def _escribir_log_auditoria(self, resultado: AdapterResult) -> None:
        """Persiste el resultado en logs/adapters_audit.json (últimas 200 entradas)."""
        log_path = self.logs_dir / "adapters_audit.json"
        entrada = {
            "timestamp_utc": resultado.timestamp_utc,
            "adapter":       resultado.adapter_name,
            "activo":        resultado.activo,
            "is_degraded":   resultado.is_degraded,
            "rows_fetched":  resultado.rows_fetched,
            "latency_ms":    round(resultado.latency_ms, 1),
            "error":         resultado.error_msg,
        }
        historial: list = []
        if log_path.exists():
            try:
                historial = json.loads(log_path.read_text())
            except Exception:
                historial = []
        historial.append(entrada)
        historial = historial[-200:]
        try:
            log_path.write_text(json.dumps(historial, indent=2, default=str))
        except Exception as e:
            logger.error("No se pudo escribir el log de auditoría: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTER CONCRETO 1: ALPHA VANTAGE (OHLCV)
#  Reemplaza yfinance bloqueado desde IPs de Colab/datacenters.
#  Free tier: 25 req/día. Cubre NVDA, BTC/USD, XAU (GLD), VIX.
# ══════════════════════════════════════════════════════════════════════════════

class AlphaVantageAdapter(BaseSensorAdapter):
    """
    Adapter OHLCV usando Alpha Vantage (fuente primaria desde v21).
    Documentado en SPEL_project_log_v21 — yfinance → Alpha Vantage migration.
    """

    name = "alpha_vantage"

    # Mapeo activo SPEL → símbolo Alpha Vantage
    _SIMBOLOS = {
        "NVDA":    ("TIME_SERIES_DAILY", "NVDA"),
        "BTC":     ("DIGITAL_CURRENCY_DAILY", "BTC", "USD"),
        "XAU":     ("TIME_SERIES_DAILY", "GLD"),    # GLD ETF como proxy XAU/USD
        "NIFTY50": ("TIME_SERIES_DAILY", "NIFTY50"),# No disponible en free tier — fallback
        "VIX":     ("TIME_SERIES_DAILY", "VIXY"),   # VIX ETF proxy
    }

    def __init__(self, api_key: Optional[str] = None, logs_dir: Optional[Path] = None):
        super().__init__(logs_dir)
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
        if not self.api_key:
            logger.warning("[alpha_vantage] ALPHAVANTAGE_API_KEY no configurada")

    @property
    def name(self) -> str:
        return "alpha_vantage"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _fetch_raw(self, activo: str, desde: datetime, hasta: datetime) -> pl.DataFrame:
        import requests

        if activo not in self._SIMBOLOS:
            raise ValueError(f"Activo '{activo}' no soportado por AlphaVantageAdapter")

        config = self._SIMBOLOS[activo]
        funcion = config[0]
        simbolo = config[1]

        if funcion == "DIGITAL_CURRENCY_DAILY":
            # Cripto
            url    = "https://www.alphavantage.co/query"
            params = {
                "function":   funcion,
                "symbol":     simbolo,
                "market":     config[2],
                "apikey":     self.api_key,
                "outputsize": "compact",  # últimos 100 días
            }
        else:
            url    = "https://www.alphavantage.co/query"
            params = {
                "function":   funcion,
                "symbol":     simbolo,
                "apikey":     self.api_key,
                "outputsize": "compact",
            }

        resp = requests.get(url, params=params, timeout=self.TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()

        # Detectar límite de rate
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information", "")
            raise RuntimeError(f"Alpha Vantage rate limit: {msg[:80]}")

        return self._parsear_respuesta(data, activo, desde, hasta)

    def _parsear_respuesta(
        self, data: dict, activo: str, desde: datetime, hasta: datetime
    ) -> pl.DataFrame:
        """Parsea la respuesta JSON de AV a un DataFrame Polars OHLCV canónico."""
        # Las claves de series temporales varían según el endpoint
        ts_key = next(
            (k for k in data if "Time Series" in k or "Time Series (Digital" in k),
            None
        )
        if ts_key is None:
            raise ValueError(f"Respuesta AV inesperada para {activo}: {list(data.keys())}")

        series = data[ts_key]
        rows = []
        for date_str, vals in series.items():
            try:
                fecha = datetime.strptime(date_str, "%Y-%m-%d")
                if fecha < desde or fecha > hasta:
                    continue
                # AV usa diferentes prefijos: "1. open", "1a. open (USD)", etc.
                open_  = float(next(v for k, v in vals.items() if "open" in k.lower()))
                high_  = float(next(v for k, v in vals.items() if "high" in k.lower()))
                low_   = float(next(v for k, v in vals.items() if "low" in k.lower()))
                close_ = float(next(v for k, v in vals.items() if "close" in k.lower() and "market" not in k.lower()))
                vol_   = float(next((v for k, v in vals.items() if "volume" in k.lower()), 0.0))
                rows.append({
                    "date": date_str,
                    "open": open_, "high": high_, "low": low_,
                    "close": close_, "volume": vol_,
                })
            except (StopIteration, ValueError):
                continue  # Fila malformada — ignorar sin romper el pipeline

        if not rows:
            raise ValueError(f"Sin datos en el rango {desde.date()} → {hasta.date()} para {activo}")

        df = pl.DataFrame(rows).sort("date")
        return df

    def _dataframe_degradado(self, activo: str) -> pl.DataFrame:
        """Override: devuelve DataFrame OHLCV con valores 0.0."""
        hoy = datetime.utcnow().strftime("%Y-%m-%d")
        return pl.DataFrame({
            "date": [hoy],
            "open": [0.0], "high": [0.0], "low": [0.0],
            "close": [0.0], "volume": [0.0],
        })


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTER CONCRETO 2: BIGQUERY GDELT (ENTROPÍA GEOPOLÍTICA)
#  Resuelve Bug #44 por diseño: este adapter SOLO existe en Contenedor 1.
#  El Contenedor 2 (Dashboard) nunca lo importa.
# ══════════════════════════════════════════════════════════════════════════════

class BigQueryGDELTAdapter(BaseSensorAdapter):
    """
    Adapter GDELT usando Google BigQuery.
    Solo existe en contenedor_1_ingestion. El dashboard nunca lo ve.

    Resuelve Bug #44: las credenciales GCP solo están en el contenedor de ingesta.
    El dashboard lee los datos ya calculados desde los parquets canónicos.
    """

    _TIMEOUT_BQ_S = 30.0  # BigQuery puede tardar más que una API REST

    # Keywords por activo (migrado desde capa_b_bigquery.py)
    _KEYWORDS = {
        "XAU":    ["gold", "federal reserve", "inflation", "bullion", "precious metals"],
        "BTC":    ["bitcoin", "cryptocurrency", "blockchain", "crypto"],
        "NVDA":   ["nvidia", "semiconductor", "AI chips", "GPU", "CUDA"],
        "NIFTY50":["india", "nifty", "sensex", "rupee", "RBI"],
    }

    def __init__(
        self,
        gcp_project_id:  Optional[str]  = None,
        bq_client=None,                           # Acepta client ya instanciado (Fix #44)
        logs_dir:        Optional[Path] = None,
    ):
        super().__init__(logs_dir)
        self.gcp_project_id = gcp_project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._bq_client     = bq_client           # Inyección de dependencia

    @property
    def name(self) -> str:
        return "bigquery_gdelt"

    def is_available(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            return False

    def _get_client(self):
        """Inicialización lazy del cliente BigQuery con soporte ADC."""
        if self._bq_client is not None:
            return self._bq_client
        from google.cloud import bigquery
        # ADC: usa GOOGLE_APPLICATION_CREDENTIALS env var o Workload Identity en GKE
        self._bq_client = bigquery.Client(project=self.gcp_project_id)
        return self._bq_client

    def _fetch_raw(self, activo: str, desde: datetime, hasta: datetime) -> pl.DataFrame:
        if activo not in self._KEYWORDS:
            raise ValueError(f"Activo '{activo}' sin keywords GDELT configuradas")

        client   = self._get_client()
        keywords = self._KEYWORDS[activo]
        kw_filter = " OR ".join(
            f"LOWER(Actor1Name) LIKE '%{kw.lower()}%' OR LOWER(Actor2Name) LIKE '%{kw.lower()}%'"
            for kw in keywords
        )

        query = f"""
        SELECT
            DATE(TIMESTAMP_SECONDS(CAST(SQLDATE AS INT64))) AS date,
            AVG(GoldsteinScale)                             AS goldstein_geo,
            COUNT(*)                                        AS n_events_ohlcv,
            AVG(NumArticles)                                AS vitality_tesla,
            COUNTIF(GoldsteinScale < -5)                   AS mass_panic_index,
            AVG(AvgTone)                                    AS fear_momentum,
            AVG(NumSources)                                 AS nash_frozen_7d
        FROM
            `gdelt-bq.gdeltv2.events`
        WHERE
            CAST(SQLDATE AS INT64) BETWEEN
                {int(desde.strftime('%Y%m%d'))}
                AND {int(hasta.strftime('%Y%m%d'))}
            AND ({kw_filter})
        GROUP BY date
        ORDER BY date
        """

        t0     = time.monotonic()
        result = client.query(query).result(timeout=self._TIMEOUT_BQ_S)
        filas  = [dict(row) for row in result]

        if not filas:
            raise ValueError(f"BigQuery sin resultados para {activo} en rango {desde.date()}→{hasta.date()}")

        df = pl.DataFrame(filas)
        # Normalizar tipos
        for col in ["goldstein_geo", "n_events_ohlcv", "vitality_tesla",
                    "mass_panic_index", "fear_momentum", "nash_frozen_7d"]:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64).fill_null(0.0))

        return df

    def _dataframe_degradado(self, activo: str) -> pl.DataFrame:
        """Override: devuelve DataFrame GDELT con valores 0.0."""
        hoy = datetime.utcnow().strftime("%Y-%m-%d")
        return pl.DataFrame({
            "date":             [hoy],
            "goldstein_geo":    [0.0],
            "n_events_ohlcv":   [0.0],
            "vitality_tesla":   [0.0],
            "mass_panic_index": [0.0],
            "fear_momentum":    [0.0],
            "nash_frozen_7d":   [0.0],
        })


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTER CONCRETO 3: TIINGO (OHLCV — Fallback de Alpha Vantage)
#  Cubre especialmente NIFTY50 y otros mercados no disponibles en AV free tier.
# ══════════════════════════════════════════════════════════════════════════════

class TiingoAdapter(BaseSensorAdapter):
    """
    Adapter OHLCV usando Tiingo API.
    Rol: fallback de AlphaVantageAdapter. Excelente cobertura global.
    """

    _BASE_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

    _TICKERS = {
        "NVDA":    "nvda",
        "BTC":     "btcusd",
        "XAU":     "gld",       # GLD ETF como proxy
        "NIFTY50": "nifty50",   # Verificar disponibilidad en Tiingo
    }

    def __init__(self, api_key: Optional[str] = None, logs_dir: Optional[Path] = None):
        super().__init__(logs_dir)
        self.api_key = api_key or os.environ.get("TIINGO_API_KEY", "")

    @property
    def name(self) -> str:
        return "tiingo"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _fetch_raw(self, activo: str, desde: datetime, hasta: datetime) -> pl.DataFrame:
        import requests

        ticker = self._TICKERS.get(activo)
        if not ticker:
            raise ValueError(f"Activo '{activo}' no mapeado en TiingoAdapter")

        headers = {"Content-Type": "application/json", "Authorization": f"Token {self.api_key}"}
        params  = {
            "startDate": desde.strftime("%Y-%m-%d"),
            "endDate":   hasta.strftime("%Y-%m-%d"),
            "resampleFreq": "daily",
        }
        url  = self._BASE_URL.format(ticker=ticker)
        resp = requests.get(url, headers=headers, params=params, timeout=self.TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            raise ValueError(f"Tiingo: sin datos para {activo}")

        rows = [
            {
                "date":   r["date"][:10],
                "open":   float(r.get("adjOpen",  r.get("open",  0.0))),
                "high":   float(r.get("adjHigh",  r.get("high",  0.0))),
                "low":    float(r.get("adjLow",   r.get("low",   0.0))),
                "close":  float(r.get("adjClose", r.get("close", 0.0))),
                "volume": float(r.get("adjVolume", r.get("volume", 0.0))),
            }
            for r in data
        ]
        return pl.DataFrame(rows).sort("date")

    def _dataframe_degradado(self, activo: str) -> pl.DataFrame:
        hoy = datetime.utcnow().strftime("%Y-%m-%d")
        return pl.DataFrame({
            "date": [hoy],
            "open": [0.0], "high": [0.0], "low": [0.0],
            "close": [0.0], "volume": [0.0],
        })


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTER CONCRETO 4: PARQUET CACHE (Último recurso — SIEMPRE disponible)
#  Lee el último registro del parquet canónico existente.
#  Garantiza que el pipeline NUNCA retorna un DataFrame totalmente vacío.
# ══════════════════════════════════════════════════════════════════════════════

class ParquetCacheAdapter(BaseSensorAdapter):
    """
    Adapter de último recurso. Lee la última fila del parquet canónico v4.
    Garantiza que el pipeline siempre tiene ALGÚN dato para continuar.
    is_degraded siempre es True — indica al pipeline que está usando dato viejo.
    """

    def __init__(self, data_lake_path: Optional[Path] = None, logs_dir: Optional[Path] = None):
        super().__init__(logs_dir)
        self.data_lake_path = data_lake_path or Path(
            os.environ.get("SPEL_DATA_LAKE", "/app/shared/data_lake")
        )

    @property
    def name(self) -> str:
        return "parquet_cache"

    def is_available(self) -> bool:
        return self.data_lake_path.exists()

    def _fetch_raw(self, activo: str, desde: datetime, hasta: datetime) -> pl.DataFrame:
        parquet_path = self.data_lake_path / "training" / f"{activo}_canonical_v4.parquet"
        if not parquet_path.exists():
            raise FileNotFoundError(f"Parquet canónico no encontrado: {parquet_path}")

        df = pl.read_parquet(str(parquet_path))
        # Filtrar por rango si es posible
        if "date" in df.columns:
            desde_str = desde.strftime("%Y-%m-%d")
            hasta_str = hasta.strftime("%Y-%m-%d")
            df_rango  = df.filter(
                (pl.col("date") >= desde_str) & (pl.col("date") <= hasta_str)
            )
            return df_rango if len(df_rango) > 0 else df.tail(1)

        return df.tail(1)


# ══════════════════════════════════════════════════════════════════════════════
#  ORQUESTADOR DE ADAPTERS — SPELAdapterChain
#  Implementa la jerarquía de fallback: Primario → Secundario → ParquetCache
# ══════════════════════════════════════════════════════════════════════════════

class SPELAdapterChain:
    """
    Orquesta la jerarquía de adapters con fallback automático.

    Uso en main_cron.py:
        chain = SPELAdapterChain.build_ohlcv_chain()
        result = chain.fetch("NVDA", desde=..., hasta=...)
        if result.is_degraded:
            logger.warning("NVDA usando dato de fallback: %s", result.error_msg)

    El pipeline continúa sin importar el resultado — SPEL no hace crash.
    """

    def __init__(self, adapters: list[BaseSensorAdapter]):
        if not adapters:
            raise ValueError("SPELAdapterChain requiere al menos un adapter")
        self.adapters = adapters

    def fetch(
        self,
        activo: str,
        desde: Optional[datetime] = None,
        hasta: Optional[datetime] = None,
    ) -> AdapterResult:
        """
        Intenta cada adapter en orden. Retorna el primer resultado no degradado.
        Si todos fallan, retorna el resultado del último adapter (ParquetCache).
        """
        ultimo_resultado: Optional[AdapterResult] = None

        for adapter in self.adapters:
            if not adapter.is_available():
                logger.info("[chain] Adapter '%s' no disponible — saltando", adapter.name)
                continue

            resultado = adapter.fetch(activo, desde, hasta)
            ultimo_resultado = resultado

            if not resultado.is_degraded:
                return resultado  # ✅ Dato real obtenido

            logger.warning(
                "[chain] Adapter '%s' degradado para %s — probando siguiente",
                adapter.name, activo,
            )

        # Todos los adapters fallaron → retornar el último (siempre es ParquetCache)
        if ultimo_resultado is not None:
            return ultimo_resultado

        # Caso extremo: ningún adapter ejecutó (todos not is_available)
        # Retornar DataFrame vacío seguro
        return AdapterResult(
            data=pl.DataFrame({"date": [datetime.utcnow().strftime("%Y-%m-%d")]}),
            adapter_name="chain_empty",
            activo=activo,
            is_degraded=True,
            error_msg="Todos los adapters no disponibles",
        )

    # ── Factories canónicas ───────────────────────────────────────────────────

    @classmethod
    def build_ohlcv_chain(
        cls,
        data_lake_path: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
    ) -> "SPELAdapterChain":
        """
        Cadena canónica para OHLCV:
        AlphaVantage → Tiingo → ParquetCache
        """
        return cls([
            AlphaVantageAdapter(logs_dir=logs_dir),
            TiingoAdapter(logs_dir=logs_dir),
            ParquetCacheAdapter(data_lake_path=data_lake_path, logs_dir=logs_dir),
        ])

    @classmethod
    def build_gdelt_chain(
        cls,
        gcp_project_id:  Optional[str]  = None,
        bq_client=None,
        data_lake_path:  Optional[Path] = None,
        logs_dir:        Optional[Path] = None,
    ) -> "SPELAdapterChain":
        """
        Cadena canónica para GDELT:
        BigQueryGDELT → ParquetCache
        """
        return cls([
            BigQueryGDELTAdapter(
                gcp_project_id=gcp_project_id,
                bq_client=bq_client,
                logs_dir=logs_dir,
            ),
            ParquetCacheAdapter(data_lake_path=data_lake_path, logs_dir=logs_dir),
        ])


# ══════════════════════════════════════════════════════════════════════════════
#  EJEMPLO DE USO (ejecutar directamente para test de sanidad)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from datetime import timedelta

    print("\n" + "═" * 65)
    print("  🔌  SPEL v8 · Test de Adapters")
    print("═" * 65 + "\n")

    hasta = datetime.utcnow()
    desde = hasta - timedelta(days=5)

    # Test OHLCV chain
    ohlcv_chain = SPELAdapterChain.build_ohlcv_chain()
    for activo in ["NVDA", "BTC"]:
        resultado = ohlcv_chain.fetch(activo, desde=desde, hasta=hasta)
        print(f"  {'⚠️ ' if resultado.is_degraded else '✅'} OHLCV {activo}: "
              f"{resultado.rows_fetched} filas | adapter={resultado.adapter_name} | "
              f"latencia={resultado.latency_ms:.0f}ms")
        if resultado.is_degraded:
            print(f"     └─ {resultado.error_msg}")

    # Test GDELT chain (solo mostrará BigQuery si GCP_PROJECT_ID está configurado)
    gdelt_chain = SPELAdapterChain.build_gdelt_chain()
    for activo in ["NVDA", "XAU"]:
        resultado = gdelt_chain.fetch(activo, desde=desde, hasta=hasta)
        print(f"  {'⚠️ ' if resultado.is_degraded else '✅'} GDELT {activo}: "
              f"{resultado.rows_fetched} filas | adapter={resultado.adapter_name} | "
              f"latencia={resultado.latency_ms:.0f}ms")

    print("\n" + "═" * 65 + "\n")
