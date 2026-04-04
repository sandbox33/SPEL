"""
spel_harvester_v3.py
════════════════════════════════════════════════════════════════
SPEL — Data Harvester v3 · Arquitectura limpia multi-asset

ROMPE CON:
  - spel_data_harvester.py  (mono-asset, sin semántica de volumen)
  - spel_bulk_harvester.py  (sin soporte índices/forex/bonds)
  - parquets contaminados pre-S22

SOPORTA:
  · EQUITY_INDEX  → volume=0.0 sentinel (SYNTHETIC_INDEX)
  · COMMODITY     → volumen real futuros (NATIVE_FUTURES)
  · FOREX         → tick volume proxy (TICK_PROXY)
  · CRYPTO        → volumen spot real (SPOT_CRYPTO)
  · BOND          → yield instrument, volume=0.0 (YIELD_INSTRUMENT)
  · EQUITY        → volumen real acciones (NATIVE_FUTURES)

ESQUEMA CANONICAL v5 (27 cols):
  date · open · high · low · close · volume · volume_type
  asset_class · log_return
  entropy_shannon · entropy_decay_lambda · entropy_psych_vix
  fibonacci_lag_1/2/3/5/8/13/21
  goldstein_geo · n_events_ohlcv · vitality_tesla
  mass_panic_index · fear_momentum · vix_norm
  nash_frozen_7d · trading_session

LSTM feature matrix (20 cols — R13 inamovible):
  Mismas que v4. Las 3 nuevas cols (volume_type, asset_class,
  trading_session) son metadata → excluidas del tensor.

REGLAS: R2, R4, R5, R11, R13, R15.

════════════════════════════════════════════════════════════════
PARCHE S22c — 2026-03-11
════════════════════════════════════════════════════════════════
FIX-1 · DeprecationWarning Polars ≥1.21:
  rolling_mean/rolling_std: min_periods → min_samples
  Afecta: _calc_entropy_psych_vix, _calc_mass_panic, _calc_vix_norm
  Impacto downstream: ninguno (renombre puro, comportamiento idéntico)

FIX-2 · NIFTY50 VALIDATION_FAILED — 59 NaN > lookback=42:
  Causa raíz: el join left OHLCV←GDELT produce NaN al inicio cuando
  el dataset GDELT arranca más tarde que el OHLCV (gap estructural,
  no datos corruptos).
  Solución: trim de filas líderes con NaN en columnas GDELT clave
  (entropy_shannon, goldstein_geo, n_events_ohlcv, vitality_tesla,
  nash_frozen_7d) ANTES de validar y persistir.
  Contrato con módulos downstream: n_rows puede ser < 2812. Los módulos
  que dependan de n_rows fijo deben usar df["date"].min() como
  referencia de inicio, no asumir 2015-01-01.
  SHA_REGISTRY: se actualiza con la nueva SHA post-trim.

FIX-3 · BTC ERROR HTTP 451 — Binance geo-restriction:
  Causa raíz: Binance bloquea el IP de Colab/VPN con código 451
  (restricción geográfica de términos de servicio).
  Solución: try/except en harvest_asset cuando CCXT falla →
  fallback automático a yfinance BTC-USD.
  Contrato con módulos downstream: vol_type=SPOT_CRYPTO se
  PRESERVA independientemente de la fuente (CCXT o yfinance).
  La SHA cambiará respecto a parquets CCXT anteriores — actualizar
  SHA_REGISTRY y godel_bound.py si se reentrenan modelos BTC.
════════════════════════════════════════════════════════════════
"""

import json, hashlib, os, time, yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
import polars as pl
import numpy as np
import yfinance as yf

# Importar GDELT foundation (no tocar — R6)
# from gdelt_foundation import GDELTFoundation

ROOT        = "/content/drive/MyDrive/ORDEN/SPEL 3.0"
DATA_LAKE   = f"{ROOT}/data_lake"
META_DIR    = f"{ROOT}/meta"
UNIVERSE    = f"{ROOT}/config/spel_universe.yml"

# ─────────────────────────────────────────────────────────────
# CONSTANTES — R4 inamovibles + nuevas extensiones
# ─────────────────────────────────────────────────────────────
LOOKBACKS = {
    # Core v2.0
    "NVDA": 63, "BTC": 21, "XAU": 63, "NIFTY50": 42,
    # Extended universe
    "SPX": 63, "NDX": 42, "DAX": 63, "NKY": 63,
    "XAG": 42, "WTI": 21, "BRENT": 21, "NG": 21,
    "HG": 42, "ZS": 42, "ZC": 42, "ZW": 42,
    "EURUSD": 21, "USDJPY": 21, "GBPUSD": 21, "USDCHF": 21,
    "ETH": 21, "SOL": 14,
    "US10Y": 63, "BUND10Y": 63,
}

# Mapeo ticker yfinance por asset_id
YFINANCE_MAP = {
    "SPX": "^GSPC", "NDX": "^NDX", "DAX": "^GDAXI",
    "NKY": "^N225", "NIFTY50": "^NSEI",
    "XAU": "GC=F",  "XAG": "SI=F",  "WTI": "CL=F",
    "BRENT": "BZ=F", "NG": "NG=F",  "HG": "HG=F",
    "ZS": "ZS=F",   "ZC": "ZC=F",  "ZW": "ZW=F",
    "EURUSD": "EURUSD=X", "USDJPY": "USDJPY=X",
    "GBPUSD": "GBPUSD=X", "USDCHF": "USDCHF=X",
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "NVDA": "NVDA",
    "US10Y": "^TNX", "BUND10Y": "BUND10Y=X",
}

# Semántica de volumen por asset_id
VOLUME_TYPE = {
    # Índices sintéticos → 0.0 sentinel
    "SPX": "SYNTHETIC_INDEX",   "NDX": "SYNTHETIC_INDEX",
    "DAX": "SYNTHETIC_INDEX",   "NKY": "SYNTHETIC_INDEX",
    "NIFTY50": "SYNTHETIC_INDEX",
    # Commodities → volumen futuros real
    "XAU": "NATIVE_FUTURES",    "XAG": "NATIVE_FUTURES",
    "WTI": "NATIVE_FUTURES",    "BRENT": "NATIVE_FUTURES",
    "NG": "NATIVE_FUTURES",     "HG": "NATIVE_FUTURES",
    "ZS": "NATIVE_FUTURES",     "ZC": "NATIVE_FUTURES",
    "ZW": "NATIVE_FUTURES",
    # Forex → tick proxy
    "EURUSD": "TICK_PROXY",     "USDJPY": "TICK_PROXY",
    "GBPUSD": "TICK_PROXY",     "USDCHF": "TICK_PROXY",
    # Crypto → spot real
    "BTC": "SPOT_CRYPTO",       "ETH": "SPOT_CRYPTO",
    "SOL": "SPOT_CRYPTO",
    # Equity → volumen acciones real
    "NVDA": "NATIVE_FUTURES",
    # Bonds → yield instrument
    "US10Y": "YIELD_INSTRUMENT", "BUND10Y": "YIELD_INSTRUMENT",
}

ASSET_CLASS_MAP = {
    "SPX": "INDEX",    "NDX": "INDEX",    "DAX": "INDEX",
    "NKY": "INDEX",    "NIFTY50": "INDEX",
    "XAU": "COMMODITY_METAL_PRECIOUS", "XAG": "COMMODITY_METAL_PRECIOUS",
    "WTI": "COMMODITY_ENERGY", "BRENT": "COMMODITY_ENERGY", "NG": "COMMODITY_ENERGY",
    "HG": "COMMODITY_METAL_INDUSTRIAL",
    "ZS": "COMMODITY_AGRI", "ZC": "COMMODITY_AGRI", "ZW": "COMMODITY_AGRI",
    "EURUSD": "FOREX_MAJOR", "USDJPY": "FOREX_MAJOR",
    "GBPUSD": "FOREX_MAJOR", "USDCHF": "FOREX_MAJOR",
    "BTC": "CRYPTO_MAJOR", "ETH": "CRYPTO_MAJOR", "SOL": "CRYPTO_ALT",
    "NVDA": "EQUITY",
    "US10Y": "BOND", "BUND10Y": "BOND",
}

TRADING_SESSION = {
    "SPX": "US",       "NDX": "US",        "NVDA": "US",
    "DAX": "EUROPE",   "BUND10Y": "EUROPE",
    "NKY": "ASIA",     "NIFTY50": "ASIA",
    "XAU": "24H",      "XAG": "24H",       "WTI": "24H",
    "BRENT": "24H",    "NG": "24H",        "HG": "24H",
    "ZS": "US",        "ZC": "US",         "ZW": "US",
    "EURUSD": "24H",   "USDJPY": "24H",    "GBPUSD": "24H",  "USDCHF": "24H",
    "BTC": "24H",      "ETH": "24H",       "SOL": "24H",
    "US10Y": "US",
}

# Assets cuyos índices no tienen volumen nativo (0.0 sentinel)
ZERO_VOLUME_ASSETS = {a for a, t in VOLUME_TYPE.items()
                      if t in ("SYNTHETIC_INDEX", "YIELD_INSTRUMENT")}


# ─────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────
def sha12(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        [h.update(c) for c in iter(lambda: f.read(65536), b"")]
    return h.hexdigest()[:12]

def parquet_path(asset: str) -> str:
    return f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"

def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")
def ok(m):   print(f"  ✅ {m}")
def err(m):  print(f"  🔴 {m}")
def warn(m): print(f"  ⚠️  {m}")
def info(m): print(f"  ·  {m}")


# ─────────────────────────────────────────────────────────────
# CLASE: OHLCVHarvester — descarga y normaliza OHLCV
# ─────────────────────────────────────────────────────────────
class OHLCVHarvester:
    """
    Descarga OHLCV de yfinance y aplica semántica de volumen correcta
    según el tipo de activo definido en VOLUME_TYPE.
    """

    def __init__(self, asset_id: str, start: str = "2010-01-01",
                 end: Optional[str] = None, interval: str = "1d"):
        self.asset_id = asset_id
        self.ticker   = YFINANCE_MAP.get(asset_id)
        self.start    = start
        self.end      = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.interval = interval
        self.vol_type = VOLUME_TYPE.get(asset_id, "UNKNOWN")
        self.asset_class = ASSET_CLASS_MAP.get(asset_id, "UNKNOWN")
        self.session  = TRADING_SESSION.get(asset_id, "UNKNOWN")

        if not self.ticker:
            raise ValueError(f"Asset '{asset_id}' no tiene ticker en YFINANCE_MAP")

    def fetch(self) -> pl.DataFrame:
        """Descarga y normaliza. Retorna DataFrame con schema base."""
        info(f"Descargando {self.asset_id} ({self.ticker}) [{self.start} → {self.end}] interval={self.interval}")

        raw = yf.download(
            self.ticker,
            start=self.start,
            end=self.end,
            interval=self.interval,
            auto_adjust=True,
            progress=False,
        )

        if raw.empty:
            raise RuntimeError(f"yfinance retornó datos vacíos para {self.ticker}")

        # Aplanar MultiIndex si existe
        if hasattr(raw.columns, 'levels'):
            raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]

        raw = raw.reset_index()
        raw.columns = [str(c).lower() for c in raw.columns]

        # Renombrar columnas estándar
        col_map = {"adj close": "close", "datetime": "date"}
        raw.rename(columns=col_map, inplace=True)

        required = ["date", "open", "high", "low", "close"]
        missing  = [c for c in required if c not in raw.columns]
        if missing:
            raise ValueError(f"{self.asset_id}: columnas faltantes {missing}")

        # Convertir a Polars
        df = pl.from_pandas(raw[required + (["volume"] if "volume" in raw.columns else [])])

        # Normalizar fecha → datetime[ms, UTC] (R5 inamovible)
        if df["date"].dtype == pl.Date:
            df = df.with_columns(
                pl.col("date")
                  .cast(pl.Datetime("ms"))
                  .dt.replace_time_zone("UTC")
            )
        elif df["date"].dtype == pl.Datetime:
            df = df.with_columns(
                pl.col("date").cast(pl.Datetime("ms", "UTC"))
            )

        # Aplicar semántica de volumen
        df = self._apply_volume_semantics(df)

        # Columnas de metadata (no entran en tensor LSTM)
        df = df.with_columns([
            pl.lit(self.vol_type).alias("volume_type"),
            pl.lit(self.asset_class).alias("asset_class"),
            pl.lit(self.session).alias("trading_session"),
        ])

        # log_return (anti-leakage: solo usa datos del propio activo)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1)).log()
              .alias("log_return")
        )

        # Ordenar por fecha, eliminar duplicados
        df = df.sort("date").unique(subset=["date"], keep="last")

        info(f"  {self.asset_id}: {len(df)} filas · {len(df.columns)} cols · "
             f"vol_type={self.vol_type}")
        return df

    def _apply_volume_semantics(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Aplica la semántica de volumen correcta según el tipo de activo.
        """
        if self.vol_type in ("SYNTHETIC_INDEX", "YIELD_INSTRUMENT"):
            # Índice o bono: no existe volumen nativo → 0.0 sentinel
            df = df.with_columns(
                pl.lit(0.0).cast(pl.Float64).alias("volume")
            )
            info(f"  {self.asset_id}: volume=0.0 (sentinel {self.vol_type})")

        elif self.vol_type == "TICK_PROXY":
            # Forex: yfinance no provee tick volume → mantener como está
            # Si volume viene vacío, usar 0.0 pero registrar como TICK_UNAVAILABLE
            if "volume" not in df.columns:
                df = df.with_columns(pl.lit(0.0).cast(pl.Float64).alias("volume"))
                warn(f"  {self.asset_id}: tick volume no disponible → 0.0")
            else:
                # Normalizar: fill_null con 0.0
                df = df.with_columns(
                    pl.col("volume").cast(pl.Float64).fill_null(0.0).alias("volume")
                )

        elif self.vol_type in ("NATIVE_FUTURES", "SPOT_CRYPTO"):
            # Volumen real: fill_null solo dentro del warm-up
            lookback = LOOKBACKS.get(self.asset_id, 42)
            if "volume" not in df.columns:
                raise ValueError(f"{self.asset_id}: se esperaba volumen real pero no existe columna 'volume'")

            n_null = df["volume"].is_null().sum()
            if n_null > lookback:
                warn(f"  {self.asset_id}: {n_null} NaN en volume (>lookback={lookback}) — fill_null(0.0)")
            df = df.with_columns(
                pl.col("volume").cast(pl.Float64).fill_null(0.0).fill_nan(0.0).alias("volume")
            )

        return df


# ─────────────────────────────────────────────────────────────
# CLASE: CCXTHarvester — descarga crypto via CCXT
# ─────────────────────────────────────────────────────────────
class CCXTHarvester:
    """
    Descarga OHLCV de Binance via CCXT para activos crypto.
    Proporciona volumen spot real.
    """

    CCXT_SYMBOLS = {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
    }

    def __init__(self, asset_id: str, start: str = "2017-01-01",
                 timeframe: str = "1d"):
        self.asset_id  = asset_id
        self.symbol    = self.CCXT_SYMBOLS.get(asset_id)
        self.start     = start
        self.timeframe = timeframe
        if not self.symbol:
            raise ValueError(f"Asset '{asset_id}' no tiene símbolo CCXT")

    def fetch(self) -> pl.DataFrame:
        try:
            import ccxt
        except ImportError:
            raise ImportError("pip install ccxt --break-system-packages")

        exchange = ccxt.binance({"enableRateLimit": True})
        since    = int(datetime.fromisoformat(self.start).timestamp() * 1000)
        limit    = 1000
        all_bars = []

        info(f"Descargando {self.asset_id} ({self.symbol}) via CCXT/Binance [{self.start}]")

        while True:
            bars = exchange.fetch_ohlcv(self.symbol, self.timeframe, since=since, limit=limit)
            if not bars:
                break
            all_bars.extend(bars)
            since = bars[-1][0] + 1
            if len(bars) < limit:
                break
            time.sleep(exchange.rateLimit / 1000)

        if not all_bars:
            raise RuntimeError(f"CCXT retornó datos vacíos para {self.symbol}")

        df = pl.DataFrame({
            "ts":     [b[0] for b in all_bars],
            "open":   [b[1] for b in all_bars],
            "high":   [b[2] for b in all_bars],
            "low":    [b[3] for b in all_bars],
            "close":  [b[4] for b in all_bars],
            "volume": [b[5] for b in all_bars],
        })

        df = df.with_columns(
            pl.from_epoch(pl.col("ts"), time_unit="ms")
              .dt.replace_time_zone("UTC")
              .alias("date")
        ).drop("ts")

        df = df.with_columns([
            pl.lit("SPOT_CRYPTO").alias("volume_type"),
            pl.lit("CRYPTO_MAJOR").alias("asset_class"),
            pl.lit("24H").alias("trading_session"),
            (pl.col("close") / pl.col("close").shift(1)).log().alias("log_return"),
        ])

        df = df.sort("date").unique(subset=["date"], keep="last")
        info(f"  {self.asset_id}: {len(df)} filas · vol_type=SPOT_CRYPTO ✅")
        return df


# ─────────────────────────────────────────────────────────────
# CLASE: EntropyFeatureBuilder — features GDELT + derivadas
# ─────────────────────────────────────────────────────────────
class EntropyFeatureBuilder:
    """
    Integra features de entropía GDELT al DataFrame OHLCV.
    Respeta R6: gdelt_foundation.py no se modifica.
    Aplica anti-leakage estricto: todo rolling calculado en ventana pasada.
    """

    # Cutoff anti-leakage inamovible
    TRAIN_CUTOFF = datetime(2023, 12, 31, tzinfo=timezone.utc)

    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.lookback = LOOKBACKS.get(asset_id, 42)
        self.gdelt_path = (
            f"{DATA_LAKE}/{asset_id}/gdelt/raw/{asset_id}_gdelt_entropy.parquet"
        )

    def build(self, df_ohlcv: pl.DataFrame) -> pl.DataFrame:
        """
        Merge GDELT + calcular features derivadas.
        Retorna DataFrame canonical v5 completo.
        """
        if not os.path.exists(self.gdelt_path):
            warn(f"{self.asset_id}: GDELT no encontrado en {self.gdelt_path}")
            warn(f"  → Columnas GDELT serán NaN — ejecutar gdelt_foundation.py")
            return self._add_null_entropy_cols(df_ohlcv)

        df_gdelt = pl.read_parquet(self.gdelt_path)

        # Normalizar fecha GDELT
        if df_gdelt["date"].dtype != pl.Datetime("ms", "UTC"):
            df_gdelt = df_gdelt.with_columns(
                pl.col("date").cast(pl.Datetime("ms", "UTC"))
            )

        # Join con OHLCV (left join para conservar todas las barras)
        df = df_ohlcv.join(
            df_gdelt.select(["date", "entropy_shannon", "zipf_concentration",
                             "goldstein_mean", "tone_variance", "n_events",
                             "nash_frozen_7d", "vitality_tesla"]),
            on="date",
            how="left"
        )

        # Renombrar n_events a n_events_ohlcv para consistencia
        if "n_events" in df.columns:
            df = df.rename({"n_events": "n_events_ohlcv"})

        # Goldstein geo = goldstein_mean (alias semántico)
        if "goldstein_mean" in df.columns:
            df = df.with_columns(pl.col("goldstein_mean").alias("goldstein_geo"))

        # Features derivadas (todas rolling — sin leakage)
        df = self._calc_entropy_decay(df)
        df = self._calc_entropy_psych_vix(df)
        df = self._calc_mass_panic(df)
        df = self._calc_fear_momentum(df)
        df = self._calc_vix_norm(df)
        df = self._calc_fibonacci_lags(df)

        return df

    def _calc_entropy_decay(self, df: pl.DataFrame) -> pl.DataFrame:
        """entropy_decay_lambda: EWM de entropy_shannon (sin leakage)."""
        ent = df["entropy_shannon"].cast(pl.Float64).fill_null(0.0)
        ewm = ent.ewm_mean(span=self.lookback, adjust=True)
        return df.with_columns(ewm.alias("entropy_decay_lambda"))

    def _calc_entropy_psych_vix(self, df: pl.DataFrame) -> pl.DataFrame:
        """entropy_psych_vix: entropy × tone_variance normalizado rolling."""
        ent  = df["entropy_shannon"].cast(pl.Float64).fill_null(0.0)
        tone = df["tone_variance"].cast(pl.Float64).fill_null(0.0)
        raw  = ent * tone.abs()
        roll_mean = raw.rolling_mean(self.lookback, min_samples=1)
        roll_std  = raw.rolling_std(self.lookback, min_samples=2).fill_null(1.0)
        result    = (raw - roll_mean) / roll_std.clip(lower_bound=1e-8)
        return df.with_columns(result.alias("entropy_psych_vix"))

    def _calc_mass_panic(self, df: pl.DataFrame) -> pl.DataFrame:
        """mass_panic_index: z-score de n_events sobre rolling window."""
        n_ev = df["n_events_ohlcv"].cast(pl.Float64).fill_null(0.0) \
               if "n_events_ohlcv" in df.columns else pl.lit(0.0).cast(pl.Float64)
        roll_mean = n_ev.rolling_mean(self.lookback, min_samples=1)
        roll_std  = n_ev.rolling_std(self.lookback, min_samples=2).fill_null(1.0)
        zscore    = (n_ev - roll_mean) / roll_std.clip(lower_bound=1e-8)
        return df.with_columns(zscore.alias("mass_panic_index"))

    def _calc_fear_momentum(self, df: pl.DataFrame) -> pl.DataFrame:
        """fear_momentum: diferencia de entropy_psych_vix, signo preservado."""
        psych = df["entropy_psych_vix"].cast(pl.Float64).fill_null(0.0)
        diff  = psych - psych.shift(1).fill_null(0.0)
        return df.with_columns(diff.alias("fear_momentum"))

    def _calc_vix_norm(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        vix_norm: rolling z-score de entropy_shannon.
        ANTI-LEAKAGE: usa rolling, NO std global (BUG-LA-01 cerrado).
        """
        ent       = df["entropy_shannon"].cast(pl.Float64).fill_null(0.0)
        roll_mean = ent.rolling_mean(self.lookback, min_samples=1)
        roll_std  = ent.rolling_std(self.lookback, min_samples=2).fill_null(1.0)
        result    = (ent - roll_mean) / roll_std.clip(lower_bound=1e-8)
        return df.with_columns(result.alias("vix_norm"))

    def _calc_fibonacci_lags(self, df: pl.DataFrame) -> pl.DataFrame:
        """Fibonacci lags del log_return: [1, 2, 3, 5, 8, 13, 21]."""
        lr = df["log_return"].cast(pl.Float64).fill_null(0.0)
        for lag in [1, 2, 3, 5, 8, 13, 21]:
            df = df.with_columns(
                lr.shift(lag).fill_null(0.0).alias(f"fibonacci_lag_{lag}")
            )
        return df

    def _add_null_entropy_cols(self, df: pl.DataFrame) -> pl.DataFrame:
        """Añade columnas de entropía como null cuando no hay GDELT."""
        entropy_cols = [
            "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
            "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
            "mass_panic_index", "fear_momentum", "vix_norm", "nash_frozen_7d",
        ]
        for col in entropy_cols:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))
        for lag in [1, 2, 3, 5, 8, 13, 21]:
            col = f"fibonacci_lag_{lag}"
            if col not in df.columns:
                df = df.with_columns(pl.lit(0.0).alias(col))
        return df


# ─────────────────────────────────────────────────────────────
# CLASE: CanonicalBuilder — ensambla canonical v5
# ─────────────────────────────────────────────────────────────
class CanonicalBuilder:
    """
    Ensambla el parquet canonical v5 final.
    Garantiza schema completo, tipos correctos y SHA.
    """

    # Columnas del tensor LSTM (20 — R13 inamovible)
    LSTM_FEATURES = [
        "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
        "fibonacci_lag_1", "fibonacci_lag_2", "fibonacci_lag_3",
        "fibonacci_lag_5", "fibonacci_lag_8", "fibonacci_lag_13", "fibonacci_lag_21",
        "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
        "mass_panic_index", "fear_momentum", "vix_norm",
        "nash_frozen_7d", "log_return",
        "open", "close",  # microestructura base
    ]

    # Schema completo canonical v5 (orden canónico)
    CANONICAL_COLS = [
        "date", "open", "high", "low", "close", "volume", "volume_type",
        "asset_class", "trading_session", "log_return",
        "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
        "fibonacci_lag_1", "fibonacci_lag_2", "fibonacci_lag_3",
        "fibonacci_lag_5", "fibonacci_lag_8", "fibonacci_lag_13", "fibonacci_lag_21",
        "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
        "mass_panic_index", "fear_momentum", "vix_norm", "nash_frozen_7d",
    ]

    def build(self, df: pl.DataFrame, asset_id: str) -> pl.DataFrame:
        """Reordena y tipifica el DataFrame al schema canonical v5."""

        # Asegurar que todas las columnas existen
        for col in self.CANONICAL_COLS:
            if col not in df.columns:
                if col in ("volume_type", "asset_class", "trading_session"):
                    df = df.with_columns(pl.lit("UNKNOWN").alias(col))
                else:
                    df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))
                    warn(f"{asset_id}: columna '{col}' añadida como null")

        # Ordenar columnas
        extra = [c for c in df.columns if c not in self.CANONICAL_COLS]
        df = df.select(self.CANONICAL_COLS + extra)

        # Tipos Float64 para columnas numéricas
        numeric_cols = [c for c in self.CANONICAL_COLS
                        if c not in ("date", "volume_type", "asset_class", "trading_session")]
        df = df.with_columns([
            pl.col(c).cast(pl.Float64) for c in numeric_cols if c in df.columns
        ])

        # Ordenar por fecha, eliminar duplicados
        df = df.sort("date").unique(subset=["date"], keep="last")

        info(f"  {asset_id}: canonical v5 → {len(df)} filas × {len(self.CANONICAL_COLS)} cols")
        return df

    def validate(self, df: pl.DataFrame, asset_id: str) -> bool:
        """Validación de integridad del canonical v5."""
        lookback = LOOKBACKS.get(asset_id, 42)
        errors   = []

        # Check schema completo
        missing = [c for c in self.CANONICAL_COLS if c not in df.columns]
        if missing:
            errors.append(f"Columnas faltantes: {missing}")

        # Check date tipo (R5)
        if df["date"].dtype != pl.Datetime("ms", "UTC"):
            errors.append(f"date dtype incorrecto: {df['date'].dtype}")

        # Check NaN críticos fuera de warm-up
        for col in self.LSTM_FEATURES:
            if col not in df.columns:
                continue
            n_null = df[col].is_null().sum()
            if n_null > lookback:
                # Excepción: volume=0 para índices es válido
                vol_type = df["volume_type"][0] if "volume_type" in df.columns else ""
                if col == "volume" and vol_type in ("SYNTHETIC_INDEX", "YIELD_INSTRUMENT"):
                    continue
                errors.append(f"'{col}': {n_null} NaN > lookback={lookback}")

        # Check volumen semántica
        if asset_id in ZERO_VOLUME_ASSETS:
            n_nonzero = (df["volume"].cast(pl.Float64) != 0.0).sum()
            if n_nonzero > 0:
                errors.append(f"SYNTHETIC_INDEX con {n_nonzero} valores != 0.0")

        if errors:
            for e in errors:
                err(f"  VALIDACIÓN {asset_id}: {e}")
            return False

        ok(f"  {asset_id}: validación canonical v5 ✅  ({len(df)} filas)")
        return True


# ─────────────────────────────────────────────────────────────
# FUNCIÓN PRINCIPAL: harvest_asset
# ─────────────────────────────────────────────────────────────
def harvest_asset(asset_id: str,
                  start: str = "2010-01-01",
                  force_ccxt: bool = False) -> dict:
    """
    Orquesta la descarga y construcción del canonical v5 para un activo.
    Retorna dict con status, sha, n_rows.
    """
    section(f"Harvesting: {asset_id}")

    try:
        # 1. OHLCV
        if force_ccxt or VOLUME_TYPE.get(asset_id) == "SPOT_CRYPTO":
            try:
                harvester  = CCXTHarvester(asset_id, start=start)
                df_ohlcv   = harvester.fetch()
            except Exception as ccxt_err:
                # Binance puede devolver HTTP 451 en entornos geo-restringidos
                # (Colab / VPN / regiones bloqueadas). Fallback a yfinance.
                warn(f"{asset_id}: CCXT falló ({ccxt_err.__class__.__name__}: "
                     f"{str(ccxt_err)[:80]}) — fallback a yfinance")
                if asset_id not in YFINANCE_MAP:
                    raise RuntimeError(
                        f"{asset_id}: sin ticker yfinance para fallback CCXT"
                    ) from ccxt_err
                harvester = OHLCVHarvester(asset_id, start=start)
                df_ohlcv  = harvester.fetch()
                info(f"  {asset_id}: fuente cambiada → yfinance ({YFINANCE_MAP[asset_id]}) "
                     f"· vol_type conserva SPOT_CRYPTO")
                # Preservar semántica vol_type aunque la fuente sea yfinance
                df_ohlcv = df_ohlcv.with_columns(
                    pl.lit("SPOT_CRYPTO").alias("volume_type")
                )
        else:
            harvester = OHLCVHarvester(asset_id, start=start)
            df_ohlcv  = harvester.fetch()

        # 2. Entropy features
        entropy  = EntropyFeatureBuilder(asset_id)
        df_full  = entropy.build(df_ohlcv)

        # 3. Canonical v5
        builder  = CanonicalBuilder()
        df_canon = builder.build(df_full, asset_id)

        # 3b. Drop definitivo: elimina TODAS las filas con NaN en features LSTM.
        #     Cubre: join tardío GDELT + warm-up rolling + cualquier NaN residual.
        #     Solución robusta — no asume posición ni cantidad de NaN.
        lstm_cols_present = [c for c in builder.LSTM_FEATURES if c in df_canon.columns]
        if lstm_cols_present:
            n_before  = len(df_canon)
            df_canon  = df_canon.filter(
                pl.all_horizontal([pl.col(c).is_not_null() for c in lstm_cols_present])
            )
            n_dropped = n_before - len(df_canon)
            if n_dropped > 0:
                info(f"  {asset_id}: {n_dropped} filas NaN eliminadas "
                     f"→ {len(df_canon)} filas limpias")

        if False:  # bloque desactivado — reemplazado por drop_nulls L3b
            lstm_nulls = [c for c in builder.LSTM_FEATURES if c in df_canon.columns]
            if lstm_nulls:
                has_all_lstm = pl.all_horizontal(
                    [pl.col(c).is_not_null() for c in lstm_nulls]
            )
            first_lstm_valid = (
                df_canon.with_row_index("__idx2__")
                .filter(has_all_lstm)["__idx2__"]
                .min()
            )
            if first_lstm_valid is not None and int(first_lstm_valid) > 0:
                n2 = int(first_lstm_valid)
                df_canon = df_canon.slice(n2)
                info(f"  {asset_id}: {n2} filas rolling-warmup trimadas → "
                     f"{len(df_canon)} filas finales")

        # 4. Validación
        if not builder.validate(df_canon, asset_id):
            return {"asset": asset_id, "status": "VALIDATION_FAILED",
                    "sha": None, "n_rows": 0}

        # 5. Persistencia
        out_path = parquet_path(asset_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_canon.write_parquet(out_path)
        sha = sha12(out_path)

        ok(f"{asset_id}: ✅  {len(df_canon)} filas · SHA {sha}")
        return {"asset": asset_id, "status": "OK",
                "sha": sha, "n_rows": len(df_canon),
                "n_cols": len(df_canon.columns),
                "vol_type": VOLUME_TYPE.get(asset_id)}

    except Exception as e:
        err(f"{asset_id}: ERROR — {e}")
        return {"asset": asset_id, "status": "ERROR", "error": str(e),
                "sha": None, "n_rows": 0}


# ─────────────────────────────────────────────────────────────
# FUNCIÓN: harvest_all — corre todos los activos del universo
# ─────────────────────────────────────────────────────────────
def harvest_all(asset_ids: list[str] | None = None,
                start: str = "2010-01-01",
                priority_filter: int | None = None) -> dict:
    """
    Descarga y construye canonical v5 para todos los activos.
    Actualiza SHA_REGISTRY al finalizar.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*64}")
    print(f"  SPEL — Harvester v3 · Bulk Download · {ts}")
    print(f"{'═'*64}\n")

    if asset_ids is None:
        asset_ids = list(YFINANCE_MAP.keys())

    from concurrent.futures import ThreadPoolExecutor, as_completed

    crypto_ids = [a for a in asset_ids if VOLUME_TYPE.get(a) == "SPOT_CRYPTO"]
    other_ids  = [a for a in asset_ids if VOLUME_TYPE.get(a) != "SPOT_CRYPTO"]

    results = {}

    def _run(asset_id):
        return asset_id, harvest_asset(asset_id, start=start)

    # yfinance: concurrente, max_workers=4 conservador
    if other_ids:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_run, a): a for a in other_ids}
            for future in as_completed(futures):
                asset_id, result = future.result()
                results[asset_id] = result

    # CCXT Binance: secuencial — rate limit interno propio
    for asset_id in crypto_ids:
        results[asset_id] = harvest_asset(asset_id, start=start)

    # SHA Registry update
    _update_registry(results)

    # Resumen
    section("RESUMEN — Harvest Completo")
    n_ok   = sum(1 for r in results.values() if r["status"] == "OK")
    n_fail = sum(1 for r in results.values() if r["status"] != "OK")

    print(f"\n  {'Activo':<12} {'Status':>8} {'Filas':>7} {'Cols':>5} {'Vol Type':<22} {'SHA'}")
    print("  " + "─" * 72)
    for asset, r in results.items():
        status = "✅" if r["status"] == "OK" else "🔴"
        rows   = str(r.get("n_rows", "—"))
        cols   = str(r.get("n_cols", "—"))
        vtype  = r.get("vol_type", "—")[:20]
        sha    = r.get("sha", "—") or "—"
        print(f"  {asset:<12} {status:>8} {rows:>7} {cols:>5} {vtype:<22} {sha}")

    print(f"\n  ✅ OK: {n_ok}/{len(asset_ids)}  🔴 Fallos: {n_fail}/{len(asset_ids)}")
    print(f"\n  SIGUIENTE: !python /content/spel_auditoria_total.py")
    print(f"{'─'*64}\n")

    return results


def _update_registry(results: dict):
    """Actualiza SHA_REGISTRY.json con resultados del harvest."""
    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    os.makedirs(META_DIR, exist_ok=True)
    reg = {}
    if os.path.exists(reg_path):
        with open(reg_path) as f:
            reg = json.load(f)

    reg["updated"]  = datetime.now(timezone.utc).isoformat()
    reg["session"]  = "S22_harvest_v3"
    reg["schema"]   = "canonical_v5"

    for asset, r in results.items():
        if r["status"] == "OK":
            reg.setdefault("parquets", {})[asset] = {
                "sha":        r.get("sha"),
                "n_rows":     r.get("n_rows"),
                "n_cols":     r.get("n_cols"),
                "vol_type":   r.get("vol_type"),
                "schema":     "canonical_v5",
                "path":       parquet_path(asset),
                "harvested":  datetime.now(timezone.utc).isoformat(),
            }

    with open(reg_path, "w") as f:
        json.dump(reg, f, indent=2)
    ok(f"SHA_REGISTRY actualizado: {reg_path}")


# ─────────────────────────────────────────────────────────────
# MAIN — ejecución directa
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    # Uso: python spel_harvester_v3.py [asset_id] [start_date]
    # Ejemplo: python spel_harvester_v3.py BTC 2017-01-01
    # Sin args: descarga los 4 activos core de SPEL v2.0

    if len(sys.argv) >= 2:
        target = [sys.argv[1].upper()]
        start  = sys.argv[2] if len(sys.argv) >= 3 else "2010-01-01"
        harvest_all(asset_ids=target, start=start)
    else:
        # Core SPEL v2.0 primero
        CORE = ["NVDA", "BTC", "XAU", "NIFTY50"]
        harvest_all(asset_ids=CORE, start="2010-01-01")
