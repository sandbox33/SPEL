# ── SPEL · PIPELINE DE INGESTA DIARIA · spel_ingestion.py ───────────────────
# Módulo: spel_ingestion.py
# Proyecto: Socio-Political Entropy Loss (SPEL) · v1.0 · 01 Mar 2026
# Autor: Abraham Fuenmayor
#
# PROPÓSITO:
#   Módulo central de ingesta y actualización de datos.
#   Define claramente DÓNDE vive cada dato, CÓMO llega al sistema,
#   y QUÉ desbloquea cada fuente cuando está disponible.
#
# ARQUITECTURA DEL PIPELINE:
#
#   PASO 1 — RAW INGESTION (yfinance + BigQuery GDELT)
#   └── Descarga datos del día y los añade a los parquets raw existentes
#
#   PASO 2 — BUILD CANONICAL (fusión de fuentes)
#   └── Fusiona raw OHLCV + entropía + features GDELT → canonical_v4.parquet
#
#   PASO 3 — DASHBOARD READS (solo lectura)
#   └── El dashboard lee únicamente canonical_v4.parquet → nunca toca raw
#
# FUENTES DE DATOS Y QUÉ DESBLOQUEAN:
#
#   yfinance (gratis, sin key)
#   ├── NVDA_1d · BTC_1d · XAU_1d · NIFTY50_1d → OHLCV + Volume (Capa A)
#   └── ^VIX → entropy_psych_vix (Score Capa B, Gödel calibration)
#
#   Google BigQuery GDELT (gratis hasta 10GB/mes)
#   ├── gdelt-bq.gdeltv2.events → goldstein_geo, n_events_ohlcv (Score Capa C)
#   ├── gdelt-bq.gdeltv2.events → vitality_tesla, mass_panic_index (Trauma detection)
#   ├── gdelt-bq.gdeltv2.events → fear_momentum, nash_frozen_7d (Psych features)
#   └── gdelt-bq.gdeltv2.gkg   → GEOINT (Nivel 10 — placeholder activo)
#
#   newsdata.io (gratis 200 req/día — Bug #39 pendiente)
#   └── n_articles_24h → Source Entropy Sensor (Nivel 4-B pausado)
#
# EJECUCIÓN:
#   Automática: 18:00 UTC vía scheduler en Colab / Railway
#   Manual: SPELIngestion(spel_path).ejecutar_pipeline_completo()
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

warnings.filterwarnings("ignore")

# ── Columnas canónicas del parquet v4 (30 cols — Regla 5, NO MODIFICAR) ──────
COLS_CANONICAS_V4 = [
    "date", "open", "high", "low", "close", "volume",
    "log_return", "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
    "fibonacci_lag_1", "fibonacci_lag_2", "fibonacci_lag_3", "fibonacci_lag_5",
    "fibonacci_lag_8", "fibonacci_lag_13", "fibonacci_lag_21",
    "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
    "mass_panic_index", "fear_momentum", "vix_norm", "nash_frozen_7d",
]

# ── Rutas raw por activo (estructura real de tu Drive) ────────────────────────
_RUTAS_RAW = {
    "NVDA":    ("data_lake/us_equity",  "NVDA_1d.parquet"),
    "BTC":     ("data_lake/crypto",     "BTC_1d.parquet"),
    "XAU":     ("data_lake/gold",       "XAU_1d.parquet"),
    "NIFTY50": ("data_lake/india",      "NIFTY50_1d.parquet"),
}

# ── Ticker de yfinance por activo ─────────────────────────────────────────────
_TICKERS_YFINANCE = {
    "NVDA":    "NVDA",
    "BTC":     "BTC-USD",
    "XAU":     "GC=F",       # Gold Futures (proxy XAU/USD)
    "NIFTY50": "^NSEI",
    "VIX":     "^VIX",
}

# ── Lookback λ canónico por activo (Regla 4) ──────────────────────────────────
_LOOKBACK_LAMBDA = {
    "NVDA": 63, "XAU": 63, "BTC": 21, "NIFTY50": 42,
}

# ── Keywords GDELT por activo (para filtro en BigQuery) ───────────────────────
_KEYWORDS_GDELT = {
    "NVDA":    ["nvidia", "semiconductor", "AI chip", "GPU", "CUDA", "export controls"],
    "BTC":     ["bitcoin", "cryptocurrency", "blockchain", "crypto", "FTX", "binance"],
    "XAU":     ["gold", "federal reserve", "inflation", "safe haven", "bullion"],
    "NIFTY50": ["india", "nifty", "rupee", "RBI", "sensex", "NSE"],
}


class SPELIngestion:
    """
    Pipeline central de ingesta y actualización de datos SPEL.

    Uso mínimo — update diario:
        pipeline = SPELIngestion(Path('/content/drive/MyDrive/SPEL'))
        pipeline.actualizar_activo('NVDA')

    Uso completo — reconstrucción total:
        pipeline.ejecutar_pipeline_completo()
    """

    def __init__(self, spel_path: Path, gcp_project_id: str = ""):
        self.spel_path       = Path(spel_path)
        self.gcp_project_id  = gcp_project_id or os.environ.get("GCP_PROJECT_ID", "")
        self.training_dir    = self.spel_path / "data_lake" / "training"
        self.entropy_dir     = self.spel_path / "data_lake" / "entropy"
        self.training_dir.mkdir(parents=True, exist_ok=True)
        self._bq_client      = None
        self._meta           = self._cargar_meta()

    # ══════════════════════════════════════════════════════════════════════════
    # PÚBLICO — Interfaz principal
    # ══════════════════════════════════════════════════════════════════════════

    def ejecutar_pipeline_completo(self, activos: list = None) -> dict:
        """
        Ejecuta el pipeline completo para todos los activos:
        1. Ingesta raw OHLCV (yfinance)
        2. Ingesta GDELT diario (BigQuery)
        3. Construcción del parquet canónico v4

        Retorna un dict con el resumen de resultados.
        """
        activos = activos or list(_RUTAS_RAW.keys())
        resultados = {}
        timestamp_inicio = datetime.utcnow()

        print("=" * 65)
        print(f"  🔄 SPEL PIPELINE · {timestamp_inicio.strftime('%Y-%m-%d %H:%M')} UTC")
        print("=" * 65)

        for activo in activos:
            print(f"\n  Procesando {activo}...")
            r = {"ohlcv": False, "gdelt": False, "canonical": False, "error": None}
            try:
                r["ohlcv"]     = self._ingestar_ohlcv(activo)
                r["gdelt"]     = self._ingestar_gdelt_diario(activo)
                r["canonical"] = self._construir_canonical(activo, forzar=False)
            except Exception as e:
                r["error"] = str(e)
                print(f"    ❌ Error en {activo}: {e}")
            resultados[activo] = r

        # Resumen
        print("\n" + "=" * 65)
        print("  RESUMEN:")
        for activo, r in resultados.items():
            estado = "✅" if r["canonical"] else "❌"
            print(f"  {estado} {activo}: OHLCV={r['ohlcv']} | GDELT={r['gdelt']} | Canonical={r['canonical']}")
            if r["error"]:
                print(f"       Error: {r['error']}")
        print("=" * 65)

        # Guardar log de ingesta
        self._guardar_log_ingesta(resultados, timestamp_inicio)
        return resultados

    def actualizar_activo(self, activo: str) -> bool:
        """
        Actualización rápida de un solo activo.
        Útil para llamar manualmente antes de operar.
        """
        ok_ohlcv    = self._ingestar_ohlcv(activo)
        ok_gdelt    = self._ingestar_gdelt_diario(activo)
        ok_canonical = self._construir_canonical(activo, forzar=True)
        return ok_canonical

    def construir_canonical_inicial(self, activo: str) -> bool:
        """
        Primera construcción del parquet canónico desde cero.
        Usar cuando el canonical_v4 no existe todavía.
        Las columnas GDELT se inicializan desde BigQuery si está disponible,
        o en 0.0 si BigQuery no está configurado.
        """
        return self._construir_canonical(activo, forzar=True)

    def diagnostico(self) -> dict:
        """Diagnóstico del estado de todos los datos del sistema."""
        resultado = {
            "timestamp_utc": datetime.utcnow().isoformat(),
            "activos": {},
        }
        for activo in _RUTAS_RAW:
            carpeta, nombre = _RUTAS_RAW[activo]
            ruta_raw      = self.spel_path / carpeta / nombre
            ruta_canonical = self.training_dir / f"{activo}_canonical_v4.parquet"

            info_activo = {
                "raw_existe":       ruta_raw.exists(),
                "raw_filas":        0,
                "raw_ultima_fecha": None,
                "canonical_existe": ruta_canonical.exists(),
                "canonical_filas":  0,
                "canonical_cols":   [],
                "gdelt_columnas_ok": False,
            }

            if ruta_raw.exists():
                try:
                    df_raw = pl.read_parquet(str(ruta_raw))
                    info_activo["raw_filas"] = len(df_raw)
                    col_fecha = self._detectar_columna_fecha(df_raw)
                    if col_fecha:
                        info_activo["raw_ultima_fecha"] = str(df_raw[col_fecha][-1])[:10]
                except Exception as e:
                    info_activo["raw_error"] = str(e)

            if ruta_canonical.exists():
                try:
                    df_can = pl.read_parquet(str(ruta_canonical))
                    info_activo["canonical_filas"] = len(df_can)
                    info_activo["canonical_cols"]  = df_can.columns
                    cols_gdelt = ["goldstein_geo", "n_events_ohlcv", "vitality_tesla"]
                    info_activo["gdelt_columnas_ok"] = all(
                        df_can[c].mean() != 0.0
                        for c in cols_gdelt if c in df_can.columns
                    )
                except Exception as e:
                    info_activo["canonical_error"] = str(e)

            resultado["activos"][activo] = info_activo

        return resultado

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 1 — INGESTA RAW OHLCV (yfinance)
    # ══════════════════════════════════════════════════════════════════════════

    def _ingestar_ohlcv(self, activo: str) -> bool:
        """
        Descarga los últimos N días de OHLCV desde yfinance y los añade
        al parquet raw existente (append, sin duplicados por fecha).

        Fuente: yfinance (gratuito, sin API key)
        Destino: data_lake/{subfolder}/{ACTIVO}_1d.parquet
        """
        try:
            import yfinance as yf
        except ImportError:
            print("    ⚠️  yfinance no instalado. Ejecutar: pip install yfinance")
            return False

        ticker_str  = _TICKERS_YFINANCE.get(activo)
        carpeta, nombre = _RUTAS_RAW[activo]
        ruta_raw    = self.spel_path / carpeta / nombre

        # Determinar desde qué fecha descargar
        if ruta_raw.exists():
            df_existente = pl.read_parquet(str(ruta_raw))
            col_fecha    = self._detectar_columna_fecha(df_existente)
            if col_fecha:
                ultima_fecha = str(df_existente[col_fecha][-1])[:10]
                fecha_inicio = (datetime.strptime(ultima_fecha, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                fecha_inicio = "2015-01-01"
        else:
            df_existente = None
            fecha_inicio = "2015-01-01"

        fecha_fin = datetime.utcnow().strftime("%Y-%m-%d")

        if fecha_inicio >= fecha_fin:
            print(f"    ✅ {activo} OHLCV ya está al día ({fecha_inicio})")
            return True

        print(f"    ⬇️  {activo} OHLCV: {fecha_inicio} → {fecha_fin}")
        ticker   = yf.Ticker(ticker_str)
        df_nuevo = ticker.history(start=fecha_inicio, end=fecha_fin, interval="1d")

        if df_nuevo.empty:
            print(f"    ⚠️  {activo}: yfinance no devolvió datos nuevos")
            return True  # No es error — puede no haber trading ese día

        # Normalizar a formato canónico
        df_nuevo = df_nuevo.reset_index()
        df_nuevo.columns = [c.lower() for c in df_nuevo.columns]

        # Detectar columna de fecha en el nuevo DataFrame
        for col in ["date", "datetime", "timestamp"]:
            if col in df_nuevo.columns:
                if col != "timestamp":
                    df_nuevo = df_nuevo.rename(columns={col: "timestamp"})
                break

        df_nuevo["timestamp"] = df_nuevo["timestamp"].astype(str).str[:10]

        # Seleccionar columnas estándar
        cols_a_guardar = ["timestamp", "open", "high", "low", "close", "volume"]
        cols_disponibles = [c for c in cols_a_guardar if c in df_nuevo.columns]
        df_nuevo = df_nuevo[cols_disponibles]

        # Añadir metadatos de identificación
        df_nuevo["symbol"]    = activo
        df_nuevo["timeframe"] = "1d"

        # Convertir a polars
        df_nuevo_pl = pl.from_pandas(df_nuevo)

        # Hacer append al existente
        if df_existente is not None:
            df_final = pl.concat([df_existente, df_nuevo_pl]).unique(subset=["timestamp"]).sort("timestamp")
        else:
            df_final = df_nuevo_pl.sort("timestamp")

        (self.spel_path / carpeta).mkdir(parents=True, exist_ok=True)
        df_final.write_parquet(str(ruta_raw))
        print(f"    ✅ {activo} OHLCV actualizado: {len(df_final):,} filas totales")
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 2 — INGESTA GDELT DIARIO (BigQuery)
    # ══════════════════════════════════════════════════════════════════════════

    def _ingestar_gdelt_diario(self, activo: str, dias_atras: int = 7) -> bool:
        """
        Descarga los últimos N días de eventos GDELT para el activo
        y los almacena en data_lake/gdelt_raw/{ACTIVO}_gdelt_daily.parquet.

        Este paso desbloquea:
        - goldstein_geo      (escala de cooperación/conflicto geopolítico)
        - n_events_ohlcv     (volumen de eventos relevantes al activo)
        - vitality_tesla     (índice de actividad mediática del sector)
        - mass_panic_index   (señal de pánico sistémico en prensa)
        - fear_momentum      (inercia del miedo en cobertura mediática)
        - nash_frozen_7d     (equilibrio de Nash — mercado en espera)

        Fuente: Google BigQuery · gdelt-bq.gdeltv2.events (dataset público)
        Destino: data_lake/gdelt_raw/{ACTIVO}_gdelt_daily.parquet
        """
        if not self.gcp_project_id:
            print(f"    ⚠️  {activo} GDELT: GCP_PROJECT_ID no configurado — columnas GDELT en 0.0")
            return False

        client = self._get_bq_client()
        if client is None:
            return False

        keywords  = _KEYWORDS_GDELT.get(activo, ["market"])
        ahora_utc = datetime.utcnow()
        inicio    = ahora_utc - timedelta(days=dias_atras)

        cond_kw = " OR ".join([
            f"LOWER(Actor1Name) LIKE '%{kw.lower()}%' "
            f"OR LOWER(Actor2Name) LIKE '%{kw.lower()}%'"
            for kw in keywords
        ])

        query = f"""
            SELECT
                FORMAT_DATE('%Y-%m-%d', DATE(TIMESTAMP(CAST(DATEADDED AS STRING),
                    'America/New_York')) ) AS fecha,
                AVG(GoldsteinScale)   AS goldstein_geo,
                COUNT(*)              AS n_events_ohlcv,
                AVG(AvgTone)          AS avg_tone,
                STDDEV(AvgTone)       AS tone_variance,
                SUM(NumMentions)      AS total_mentions
            FROM `gdelt-bq.gdeltv2.events`
            WHERE
                DATEADDED >= {inicio.strftime('%Y%m%d%H%M%S')}
                AND ({cond_kw})
            GROUP BY fecha
            ORDER BY fecha
        """

        try:
            df_gdelt = client.query(query).to_dataframe()
        except Exception as e:
            print(f"    ⚠️  {activo} GDELT BigQuery error: {e}")
            return False

        if df_gdelt.empty:
            print(f"    ⚠️  {activo} GDELT: sin eventos en los últimos {dias_atras} días")
            return False

        # Calcular features derivadas
        df_gdelt["vitality_tesla"]    = df_gdelt["total_mentions"] / (df_gdelt["total_mentions"].mean() + 1e-9)
        df_gdelt["mass_panic_index"]  = (-df_gdelt["goldstein_geo"]).clip(lower=0) / 10.0
        df_gdelt["fear_momentum"]     = df_gdelt["tone_variance"].fillna(0) / (df_gdelt["tone_variance"].std() + 1e-9)
        df_gdelt["nash_frozen_7d"]    = (df_gdelt["n_events_ohlcv"].rolling(7, min_periods=1).std() < 50).astype(float)

        df_gdelt_pl = pl.from_pandas(df_gdelt[
            ["fecha", "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
             "mass_panic_index", "fear_momentum", "nash_frozen_7d"]
        ]).rename({"fecha": "date"})

        # Guardar / append a gdelt_raw
        gdelt_raw_dir  = self.spel_path / "data_lake" / "gdelt_raw"
        gdelt_raw_dir.mkdir(parents=True, exist_ok=True)
        ruta_gdelt_raw = gdelt_raw_dir / f"{activo}_gdelt_daily.parquet"

        if ruta_gdelt_raw.exists():
            df_existente = pl.read_parquet(str(ruta_gdelt_raw))
            df_gdelt_pl  = pl.concat([df_existente, df_gdelt_pl]).unique(subset=["date"]).sort("date")

        df_gdelt_pl.write_parquet(str(ruta_gdelt_raw))
        print(f"    ✅ {activo} GDELT actualizado: {len(df_gdelt_pl)} filas | últimas features GDELT disponibles")
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 3 — CONSTRUCCIÓN DEL PARQUET CANÓNICO V4
    # ══════════════════════════════════════════════════════════════════════════

    def _construir_canonical(self, activo: str, forzar: bool = False) -> bool:
        """
        Fusiona OHLCV raw + entropía anual + features GDELT → canonical_v4.parquet

        Si forzar=False y el canonical ya está al día con el raw, no hace nada.
        Si forzar=True, reconstruye siempre.
        """
        # C-02 FIX S19: write to canonical/ (motor reads from here)
        _canon_dir = self.spel_path / "data_lake" / "canonical" / activo
        _canon_dir.mkdir(parents=True, exist_ok=True)
        dest = _canon_dir / f"{activo}_canon_v4.parquet"

        # ── Verificar si ya está al día ──────────────────────────────────────
        carpeta, nombre = _RUTAS_RAW[activo]
        ruta_raw = self.spel_path / carpeta / nombre

        if not ruta_raw.exists():
            print(f"    ❌ {activo}: parquet raw no encontrado en {ruta_raw}")
            return False

        if dest.exists() and not forzar:
            df_can = pl.read_parquet(str(dest))
            df_raw = pl.read_parquet(str(ruta_raw))
            col_f_raw = self._detectar_columna_fecha(df_raw)
            col_f_can = self._detectar_columna_fecha(df_can)
            if col_f_raw and col_f_can:
                if str(df_raw[col_f_raw][-1])[:10] <= str(df_can[col_f_can][-1])[:10]:
                    print(f"    ✅ {activo} canonical ya está al día — saltando")
                    return True

        # ── 1. Cargar y normalizar OHLCV raw ─────────────────────────────────
        df = pl.read_parquet(str(ruta_raw))
        col_fecha = self._detectar_columna_fecha(df)
        if not col_fecha:
            print(f"    ❌ {activo}: no se encontró columna de fecha en el raw")
            return False

        if col_fecha != "date":
            df = df.rename({col_fecha: "date"})

        # Asegurar tipos correctos
        try:
            df = df.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10))
        except Exception:
            pass

        df = df.sort("date")

        # Asegurar OHLCV estándar
        for col_orig, col_dest in [("Open","open"),("High","high"),("Low","low"),
                                    ("Close","close"),("Volume","volume")]:
            if col_orig in df.columns and col_dest not in df.columns:
                df = df.rename({col_orig: col_dest})

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df = df.with_columns(pl.lit(0.0).alias(col))

        df = df.select(["date", "open", "high", "low", "close", "volume"])

        # ── 2. Log-return ─────────────────────────────────────────────────────
        close = df["close"].to_numpy().astype(float)
        log_ret = np.concatenate([[0.0], np.log(close[1:] / (close[:-1] + 1e-12))])
        df = df.with_columns(pl.Series("log_return", log_ret))

        # ── 3. Entropía Shannon (desde parquets anuales) ──────────────────────
        df = self._join_entropia(df, activo)

        # ── 4. Features derivadas de entropía ────────────────────────────────
        df = self._calcular_features_entropia(df, activo)

        # ── 5. Features GDELT (desde gdelt_raw si existe, 0.0 si no) ─────────
        df = self._join_gdelt_features(df, activo)

        # ── 6. Verificar 24 columnas canónicas ───────────────────────────────
        for col in COLS_CANONICAS_V4:
            if col not in df.columns:
                df = df.with_columns(pl.lit(0.0).alias(col))

        df = df.select(COLS_CANONICAS_V4)

        # ── 7. Escribir ───────────────────────────────────────────────────────
        df.write_parquet(str(dest))
        kb = dest.stat().st_size // 1024
        print(f"    ✅ {activo}_canonical_v4.parquet: {len(df):,} filas · {len(df.columns)} cols · {kb} KB")
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # AUXILIARES INTERNOS
    # ══════════════════════════════════════════════════════════════════════════

    def _detectar_columna_fecha(self, df: pl.DataFrame) -> Optional[str]:
        """Detecta la columna de fecha en un DataFrame con nombres variados."""
        candidatos = ["date", "Date", "datetime", "Datetime", "timestamp",
                      "Timestamp", "time", "Time"]
        for col in candidatos:
            if col in df.columns:
                return col
        return None

    def _join_entropia(self, df: pl.DataFrame, activo: str) -> pl.DataFrame:
        """Une los parquets de entropía anuales al DataFrame OHLCV."""
        frames = []
        for f in sorted(self.entropy_dir.glob(f"{activo}_*_entropy.parquet")):
            try:
                df_ent = pl.read_parquet(str(f))
                col_f  = self._detectar_columna_fecha(df_ent)
                if col_f and col_f != "date":
                    df_ent = df_ent.rename({col_f: "date"})
                if "date" in df_ent.columns and "entropy_shannon" in df_ent.columns:
                    df_ent = df_ent.with_columns(
                        pl.col("date").cast(pl.Utf8).str.slice(0, 10)
                    )
                    frames.append(df_ent.select(["date", "entropy_shannon"]))
            except Exception:
                pass

        if frames:
            df_ent_total = pl.concat(frames).unique(subset=["date"]).sort("date")
            df = df.join(df_ent_total, on="date", how="left")
            df = df.with_columns(pl.col("entropy_shannon").fill_null(0.0))
        else:
            df = df.with_columns(pl.lit(0.0).alias("entropy_shannon"))

        return df

    def _calcular_features_entropia(self, df: pl.DataFrame, activo: str) -> pl.DataFrame:
        """Calcula decay lambda, psych VIX, fibonacci lags y vix_norm."""
        ent     = df["entropy_shannon"].to_numpy().astype(float)
        lr      = df["log_return"].to_numpy().astype(float)
        lookback = _LOOKBACK_LAMBDA.get(activo, 63)
        alpha   = 1.0 / lookback

        # Entropy decay lambda
        ent_decay = np.zeros(len(ent))
        ent_decay[0] = ent[0]
        for i in range(1, len(ent)):
            ent_decay[i] = alpha * ent[i] + (1 - alpha) * ent_decay[i - 1]

        df = df.with_columns(pl.Series("entropy_decay_lambda", ent_decay))

        # VIX proxy desde |log_return| normalizado
        vix_proxy = np.abs(lr) / (np.std(lr) + 1e-9)
        df = df.with_columns(
            pl.Series("entropy_psych_vix", vix_proxy),
            pl.Series("vix_norm",          vix_proxy),
        )

        # Fibonacci lags sobre entropía
        for lag in [1, 2, 3, 5, 8, 13, 21]:
            lag_arr = np.concatenate([np.zeros(lag), ent[:-lag]])
            df = df.with_columns(pl.Series(f"fibonacci_lag_{lag}", lag_arr))

        return df

    def _join_gdelt_features(self, df: pl.DataFrame, activo: str) -> pl.DataFrame:
        """
        Une features GDELT al DataFrame.
        Si el parquet gdelt_raw existe → usa datos reales.
        Si no existe → columnas en 0.0 (placeholder hasta ingestar GDELT).
        """
        gdelt_raw_path = self.spel_path / "data_lake" / "gdelt_raw" / f"{activo}_gdelt_daily.parquet"

        cols_gdelt = ["goldstein_geo", "n_events_ohlcv", "vitality_tesla",
                      "mass_panic_index", "fear_momentum", "nash_frozen_7d"]

        if gdelt_raw_path.exists():
            try:
                df_gdelt = pl.read_parquet(str(gdelt_raw_path))
                col_f    = self._detectar_columna_fecha(df_gdelt)
                if col_f and col_f != "date":
                    df_gdelt = df_gdelt.rename({col_f: "date"})
                df_gdelt = df_gdelt.with_columns(
                    pl.col("date").cast(pl.Utf8).str.slice(0, 10)
                )
                cols_disponibles = ["date"] + [c for c in cols_gdelt if c in df_gdelt.columns]
                df = df.join(df_gdelt.select(cols_disponibles), on="date", how="left")
                for col in cols_gdelt:
                    if col in df.columns:
                        df = df.with_columns(pl.col(col).fill_null(0.0))
                    else:
                        df = df.with_columns(pl.lit(0.0).alias(col))
                print(f"      ✅ GDELT features reales enlazadas para {activo}")
                return df
            except Exception as e:
                print(f"      ⚠️  Error al leer GDELT raw de {activo}: {e} — usando 0.0")

        # Fallback: placeholder 0.0
        for col in cols_gdelt:
            df = df.with_columns(pl.lit(0.0).alias(col))
        return df

    def _get_bq_client(self):
        """Inicialización lazy del cliente BigQuery."""
        if self._bq_client is not None:
            return self._bq_client
        try:
            from google.cloud import bigquery
            self._bq_client = bigquery.Client(project=self.gcp_project_id)
            return self._bq_client
        except ImportError:
            print("    ⚠️  google-cloud-bigquery no instalado")
            return None
        except Exception as e:
            print(f"    ⚠️  BigQuery no disponible: {e}")
            return None

    def _cargar_meta(self) -> dict:
        meta_path = self.spel_path / "SPEL_META.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text())
        return {}

    def _guardar_log_ingesta(self, resultados: dict, timestamp_inicio: datetime):
        """Guarda un registro de la última ingesta en logs/."""
        logs_dir = self.spel_path / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_entry = {
            "timestamp_utc": timestamp_inicio.isoformat(),
            "duracion_seg":  (datetime.utcnow() - timestamp_inicio).seconds,
            "resultados":    resultados,
        }
        log_path = logs_dir / "ingesta_log.json"
        # Mantener historial de últimas 30 ingestas
        historial = []
        if log_path.exists():
            try:
                historial = json.loads(log_path.read_text())
            except Exception:
                historial = []
        historial.append(log_entry)
        historial = historial[-30:]
        log_path.write_text(json.dumps(historial, indent=2, default=str))


# ══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE DATOS — Qué desbloquea cada fuente
# ══════════════════════════════════════════════════════════════════════════════
#
#  FUENTE             │ REQUIERE          │ ESTADO   │ DESBLOQUEA
#  ───────────────────┼───────────────────┼──────────┼──────────────────────────────
#  yfinance           │ pip install       │ ✅ Gratis│ OHLCV, volume, Capa A entera
#  yfinance ^VIX      │ pip install       │ ✅ Gratis│ entropy_psych_vix real (vs proxy)
#  BigQuery GDELT     │ GCP account + API │ ✅ Free  │ goldstein_geo, n_events_ohlcv,
#                     │                   │  Tier    │ vitality_tesla, mass_panic_index,
#                     │                   │          │ fear_momentum, nash_frozen_7d
#  BigQuery GKG 2.0   │ mismo GCP         │ 🟡 N10  │ GEOINT "Ojo de Dios" (Nivel 10)
#  newsdata.io        │ API key (Bug #39) │ ❌ Roto  │ Source Entropy Sensor (Nivel 4-B)
#  Dukascopy          │ download manual   │ ⏳ N9    │ Tick data sub-minuto (Nivel 9)
#
# ══════════════════════════════════════════════════════════════════════════════
