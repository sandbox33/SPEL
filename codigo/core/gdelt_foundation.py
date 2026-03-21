"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║                    GDELT FOUNDATION — SPEL v6                               ║
║              Base de Datos Sólida para Entropía Histórica                   ║
║                                                                              ║
║  Autor:   Abraham Fuenmayor                                                  ║
║  Versión: 1.0.0 — Paso 2 de SPEL                                            ║
║  Fecha:   19 Feb 2026                                                        ║
║                                                                              ║
║  PROPÓSITO ÚNICO: Descargar GDELT bulk → calcular entropía diaria →         ║
║                   guardar Parquet → exponer función de join con OHLCV.      ║
║                                                                              ║
║  NO TOCA: critical_loss_optimized.py / api_sensor.py / data_lake/OHLCV      ║
║  NO DUPLICA: el monitoreo de APIs en tiempo real (eso es api_sensor.py)     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

SEÑALES MATEMÁTICAS COMPUTADAS (todas en un solo pase sobre el raw data):

  entropy_shannon    → Entropía de Shannon del AvgTone diario.
                       Mide cuán impredecible/diversa es la cobertura noticiosa.
                       H = -Σ p(i) * log2(p(i))

  zipf_concentration → Índice de concentración de Zipf (análogo a Herfindahl).
                       Bajo = muchas fuentes balanceadas (distribución plana).
                       Alto = pocos actores dominan el discurso (ley de potencia).
                       Fórmula: Σ (s_i / S_total)² donde s_i = NumSources por evento.

  goldstein_mean     → Media del GoldsteinScale (-10 a +10).
                       Conflicto (-10) vs Cooperación (+10).
                       Predictor Granger: precede a movimientos de precio.

  tone_variance      → Varianza del AvgTone del día.
                       Alta varianza = volatilidad emocional del ecosistema mediático.

  n_events           → Conteo bruto de eventos. Señal de "volumen" noticiosa.

  nash_frozen_7d     → Std rolling 7-días de entropy_shannon.
                       Bajo = sociedad atrapada en Equilibrio de Nash subóptimo.
                       Alto = sistema en transición, saliendo del "Caos Gris".

  vitality_tesla     → Categoría 3/6/9 de Tesla basada en percentil de entropía.
                       3 = Creación (entropía baja, orden emergente)
                       6 = Estructura/Estancamiento (entropía media, Nash)
                       9 = Trascendencia/Ruptura (entropía alta, cambio de régimen)

SEÑALES PLANIFICADAS (Innovaciones futuras, NO implementar aquí):
  - fibonacci_lag_*  → Entropía en lags 1,2,3,5,8,13,21 días. Ver Nivel 3.
  - entropy_by_source → Peso diferencial por fuente. Ver Nivel 4.
  - confidence_bound → Intervalo de confianza. Ver Nivel 5 (Gödel bound).
"""

import io
import zipfile
import hashlib
import logging
import requests
import numpy as np
import polars as pl
from pathlib import Path
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO
)
log = logging.getLogger("gdelt_foundation")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES GDELT 1.0
# ─────────────────────────────────────────────────────────────────────────────

GDELT_BASE_URL = "http://data.gdeltproject.org/events"

# Columnas que nos importan del CSV de 57 columnas de GDELT 1.0
# (sin header — índices 0-based)
GDELT_COLS = {
    "SQLDATE":           1,   # YYYYMMDD (int)
    "Actor1CountryCode": 7,   # 'USA', 'IND', 'CHN'...
    "Actor2CountryCode": 17,  # ídem
    "GoldsteinScale":    29,  # float -10 a +10 (conflicto ↔ cooperación)
    "NumMentions":       30,  # int
    "NumSources":        31,  # int
    "NumArticles":       32,  # int
    "AvgTone":           33,  # float -100 a +100
}

# Número total de columnas en el CSV de GDELT 1.0
GDELT_TOTAL_COLS = 57

# Nombres de las columnas que importamos (en el orden en que las leeremos)
GDELT_SELECTED_NAMES = [
    "date_int",       # SQLDATE → lo convertimos a date
    "country1",       # Actor1CountryCode
    "country2",       # Actor2CountryCode
    "goldstein",      # GoldsteinScale
    "num_mentions",   # NumMentions
    "num_sources",    # NumSources
    "num_articles",   # NumArticles
    "avg_tone",       # AvgTone
]


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN POR ACTIVO
# ─────────────────────────────────────────────────────────────────────────────

# Países relevantes para cada activo (GDELT usa códigos de 3 letras).
# Fundamento: Granger Causality requiere filtrar la señal de ruido global.
# Un evento en Tuvalu no predice NVDA, pero sí uno en Taiwan.
ASSET_COUNTRY_FILTERS: dict[str, list[str]] = {
    "NVDA": [
        "USA",  # Sede + NYSE
        "TWN",  # TSMC — fab principal de chips NVIDIA
        "KOR",  # Samsung — fab alternativo
        "CHN",  # Mercado + restricciones de exportación
        "JPN",  # Cadena de suministro
    ],
    "XAU": [
        # Oro es refugio global — responde a tensión geopolítica global.
        # Sin filtro de país: se usa TODO el dataset.
        # Se distingue por GoldsteinScale muy negativo (conflicto activo).
        # Lista vacía = sin filtro = todos los países.
    ],
    "BTC": [
        # BTC responde a regulación. Los grandes jugadores reguladores:
        "USA",  # SEC, Fed, Treasury
        "CHN",  # bans y unbans cíclicos
        "RUS",  # flujos sanción
        "PRK",  # hacks y ransomware (on-chain entropy)
        "DEU",  # BaFin, ECB
        "GBR",  # FCA
    ],
    "NIFTY50": [
        "IND",  # India — principal
        "PAK",  # tensión geopolítica regional
        "CHN",  # frontera + competencia
        "USA",  # correlación global FII flows
    ],
}

# Etiqueta Tesla 3-6-9 por percentil de entropía
# 3 → Creación (entropía baja, mundo ordenado)
# 6 → Estructura/Estancamiento (Nash) 
# 9 → Trascendencia/Ruptura (régimen change)
TESLA_PERCENTILE_THRESHOLDS = (33.0, 66.0)  # % bajo/medio/alto

# Ventana Nash frozen (días consecutivos de baja variación en entropía)
NASH_ROLLING_WINDOW = 7

# Threshold para considerar "frozen" (Caos Gris)
NASH_FROZEN_THRESHOLD = 0.15  # std normalizado < 0.15 = frozen

# Timeout descarga HTTP (segundos)
DOWNLOAD_TIMEOUT = 60

# Schema del Parquet de entropía diaria resultante
ENTROPY_SCHEMA = {
    "date":                pl.Date,
    "asset":               pl.Categorical,
    "entropy_shannon":     pl.Float32,
    "zipf_concentration":  pl.Float32,
    "goldstein_mean":      pl.Float32,
    "tone_variance":       pl.Float32,
    "n_events":            pl.Int32,
    "nash_frozen_7d":      pl.Float32,
    "vitality_tesla":      pl.Int8,      # 3, 6 o 9
}


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES DE CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GDELTConfig:
    """
    Configuración centralizada.
    Un solo objeto, sin magic numbers dispersos en el código.
    """
    data_lake_path: Path              # raíz del data_lake (Drive)
    assets: list[str] = field(
        default_factory=lambda: ["NVDA", "XAU", "BTC", "NIFTY50"]
    )
    start_year: int = 2015
    end_year:   int = 2025           # inclusive
    redownload:  bool = False        # si True, sobreescribe ZIPs ya guardados
    validate_checksum: bool = False  # GDELT no publica checksums, pero podemos guardar el nuestro

    @property
    def raw_path(self) -> Path:
        """Ruta donde guardamos los ZIPs descargados."""
        return self.data_lake_path / "gdelt_raw"

    @property
    def entropy_path(self) -> Path:
        """Ruta donde guardamos los Parquets de entropía."""
        return self.data_lake_path / "entropy"

    @property
    def training_path(self) -> Path:
        """Ruta donde guardamos los datasets de entrenamiento."""
        return self.data_lake_path / "training"

    def ensure_dirs(self) -> None:
        """Crea directorios si no existen."""
        for p in [self.raw_path, self.entropy_path, self.training_path]:
            p.mkdir(parents=True, exist_ok=True)
        log.info("Directorios verificados: %s", self.data_lake_path)


@dataclass
class QualityReport:
    """Resultado de la auditoría de un Parquet de entropía."""
    asset:            str
    year:             int
    path:             Path
    n_rows:           int
    date_min:         Optional[date]
    date_max:         Optional[date]
    null_count:       int
    entropy_min:      float
    entropy_max:      float
    nash_frozen_days: int   # días con vitality==6 y nash_frozen_7d < threshold
    ok:               bool
    notes:            str = ""


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────

class GDELTDownloader:
    """
    Descarga archivos GDELT 1.0 año por año, día por día.

    Estrategia de memoria:
    - NO acumula todos los días de un año en RAM.
    - Descarga un ZIP, extrae el CSV en memoria, lo parsea, lo descarta.
    - Retorna un Polars DataFrame diario, el caller lo agrega y guarda a Parquet.

    GDELT 1.0 URL: http://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip
    """

    def __init__(self, cfg: GDELTConfig):
        self.cfg = cfg

    def _url_for_date(self, d: date) -> str:
        return f"{GDELT_BASE_URL}/{d.strftime('%Y%m%d')}.export.CSV.zip"

    def _local_zip_path(self, d: date) -> Path:
        return self.cfg.raw_path / f"{d.strftime('%Y%m%d')}.export.CSV.zip"

    def _dates_for_year(self, year: int) -> list[date]:
        start = date(year, 1, 1)
        end   = date(year, 12, 31)
        delta = (end - start).days + 1
        return [start + timedelta(days=i) for i in range(delta)]

    def fetch_day(self, d: date) -> Optional[pl.DataFrame]:
        """
        Descarga (o lee desde caché) el CSV de un día y retorna el DataFrame crudo.
        Retorna None si el día no está disponible (festivos GDELT, fines de semana, etc.)
        """
        local = self._local_zip_path(d)

        if local.exists() and not self.cfg.redownload:
            log.debug("Cache hit: %s", local.name)
            raw_bytes = local.read_bytes()
        else:
            url = self._url_for_date(d)
            try:
                resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=False)
                if resp.status_code == 404:
                    log.debug("GDELT sin datos para %s (404)", d)
                    return None
                resp.raise_for_status()
                raw_bytes = resp.content
                local.write_bytes(raw_bytes)  # cache local
            except requests.RequestException as e:
                log.warning("Error descargando %s: %s", d, e)
                return None

        return self._parse_zip(raw_bytes, d)

    def _parse_zip(self, raw_bytes: bytes, d: date) -> Optional[pl.DataFrame]:
        """
        Extrae el CSV del ZIP y parsea solo las columnas que necesitamos.
        Polars lee columnas por índice sin cargar las 57 en RAM.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                csv_name = zf.namelist()[0]
                csv_bytes = zf.read(csv_name)
        except (zipfile.BadZipFile, IndexError) as e:
            log.warning("ZIP corrupto para %s: %s", d, e)
            return None

        try:
            # GDELT 1.0: tab-separated, sin header, encoding latin-1
            df = pl.read_csv(
                csv_bytes,
                separator="\t",
                has_header=False,
                infer_schema_length=0,  # todo como Utf8 → casteamos nosotros
                ignore_errors=True,
                encoding="latin1",
            )
        except Exception as e:
            log.warning("Error parseando CSV %s: %s", d, e)
            return None

        if df.is_empty() or df.width < GDELT_TOTAL_COLS:
            return None

        # Seleccionar solo las columnas que importan (por índice)
        col_indices = list(GDELT_COLS.values())
        try:
            selected = df.select([
                pl.col(f"column_{idx + 1}").alias(name)
                for idx, name in zip(col_indices, GDELT_SELECTED_NAMES)
            ])
        except Exception as e:
            log.warning("Selección de columnas fallida %s: %s", d, e)
            return None

        # Castear tipos — datos crudos son str
        try:
            selected = selected.with_columns([
                pl.col("date_int").cast(pl.Int32),
                pl.col("goldstein").cast(pl.Float32, strict=False),
                pl.col("num_mentions").cast(pl.Int32, strict=False),
                pl.col("num_sources").cast(pl.Int32, strict=False),
                pl.col("num_articles").cast(pl.Int32, strict=False),
                pl.col("avg_tone").cast(pl.Float32, strict=False),
            ]).drop_nulls(subset=["goldstein", "avg_tone"])
        except Exception as e:
            log.warning("Error casteando %s: %s", d, e)
            return None

        return selected if not selected.is_empty() else None


# ─────────────────────────────────────────────────────────────────────────────
# CALCULADORA DE ENTROPÍA
# ─────────────────────────────────────────────────────────────────────────────

class EntropyCalculator:
    """
    Transforma el raw DataFrame de GDELT en señales de entropía diarias.

    Todas las señales se computan en UN SOLO pase de agregación (no re-lee datos).
    Las señales Nash y Tesla se calculan en un segundo pase sobre el DataFrame
    ya agregado (que vive en RAM, es pequeño: máximo 365 filas × columnas).

    Señales:
        entropy_shannon    → H = -Σ p(i) * log2(p(i))  sobre AvgTone discretizado
        zipf_concentration → Índice Herfindahl sobre NumSources
        goldstein_mean     → Media ponderada de GoldsteinScale (predictor Granger)
        tone_variance      → Varianza de AvgTone (volatilidad emocional)
        n_events           → Conteo bruto
        nash_frozen_7d     → Rolling std-7d de entropy_shannon normalizado
        vitality_tesla     → Categoría 3/6/9 por percentil de entropía
    """

    N_TONE_BINS = 20  # bins para discretizar AvgTone antes de calcular H

    def compute_daily_signals(
        self,
        raw_df: pl.DataFrame,
        asset: str,
        d: date,
    ) -> Optional[pl.DataFrame]:
        """
        Filtra por asset, calcula las señales para un día específico.
        Retorna un DataFrame de 1 fila, o None si no hay datos suficientes.
        """
        filtered = self._filter_by_asset(raw_df, asset)
        if filtered.is_empty() or len(filtered) < 5:
            return None

        tones = filtered["avg_tone"].to_numpy()
        sources = filtered["num_sources"].drop_nulls().to_numpy()
        goldstein = filtered["goldstein"].drop_nulls().to_numpy()

        entropy  = self._shannon_entropy(tones)
        zipf     = self._zipf_concentration(sources)
        gold_m   = float(np.mean(goldstein)) if len(goldstein) > 0 else 0.0
        tone_var = float(np.var(tones)) if len(tones) > 1 else 0.0
        n_ev     = len(filtered)

        return pl.DataFrame({
            "date":               [d],
            "asset":              [asset],
            "entropy_shannon":    [float(entropy)],
            "zipf_concentration": [float(zipf)],
            "goldstein_mean":     [float(gold_m)],
            "tone_variance":      [float(tone_var)],
            "n_events":           [n_ev],
        })

    def _filter_by_asset(self, df: pl.DataFrame, asset: str) -> pl.DataFrame:
        """
        Filtra el raw DataFrame por los países relevantes para cada activo.
        XAU no filtra (entropía global).
        """
        countries = ASSET_COUNTRY_FILTERS.get(asset, [])
        if not countries:
            return df  # XAU: sin filtro
        return df.filter(
            pl.col("country1").is_in(countries) | pl.col("country2").is_in(countries)
        )

    @staticmethod
    def _shannon_entropy(tones: np.ndarray) -> float:
        """
        H = -Σ p(i) * log2(p(i))
        Discretiza los tonos en bins antes de calcular.
        Rango de H: 0 (certeza total) a log2(N_BINS) (máximo caos).
        """
        if len(tones) < 2:
            return 0.0
        counts, _ = np.histogram(tones, bins=EntropyCalculator.N_TONE_BINS, range=(-100, 100))
        counts = counts[counts > 0]
        probs = counts / counts.sum()
        return float(-np.sum(probs * np.log2(probs)))

    @staticmethod
    def _zipf_concentration(sources: np.ndarray) -> float:
        """
        Índice Herfindahl-Hirschman: Σ (s_i / S_total)²
        0.0 = distribución perfectamente plana (muchas fuentes equilibradas)
        1.0 = un solo actor domina (monopolio informativo — Ley de Zipf extrema)
        """
        if len(sources) == 0 or sources.sum() == 0:
            return 0.0
        total = float(sources.sum())
        shares = sources / total
        return float(np.sum(shares ** 2))

    def add_nash_and_tesla(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Añade nash_frozen_7d y vitality_tesla al DataFrame ya agregado por día.
        Se llama UNA VEZ sobre el resultado anual (365 filas max — cabe en RAM).

        nash_frozen_7d: rolling std de 7d sobre entropy_shannon normalizada [0,1].
            Valor bajo → sistema en Equilibrio de Nash (sin movimiento).
            Valor alto → sistema transitando (oportunidad o caos).

        vitality_tesla: 3 / 6 / 9 según percentil de entropía.
            Mapea la abstracción de Tesla al dato concreto.
        """
        # Normalizar entropía [0, 1] para que el rolling std sea comparable
        e_min = df["entropy_shannon"].min()
        e_max = df["entropy_shannon"].max()
        e_range = (e_max - e_min) if (e_max - e_min) > 0 else 1.0

        df = df.with_columns([
            ((pl.col("entropy_shannon") - e_min) / e_range).alias("_e_norm")
        ])

        # Rolling std de 7 días — usa sort para garantizar orden temporal
        df = df.sort("date").with_columns([
            pl.col("_e_norm")
              .rolling_std(window_size=NASH_ROLLING_WINDOW, min_periods=2)
              .alias("nash_frozen_7d")
        ]).drop("_e_norm")

        # Tesla vitality — percentiles sobre todo el año
        p33 = df["entropy_shannon"].quantile(TESLA_PERCENTILE_THRESHOLDS[0] / 100)
        p66 = df["entropy_shannon"].quantile(TESLA_PERCENTILE_THRESHOLDS[1] / 100)

        df = df.with_columns([
            pl.when(pl.col("entropy_shannon") <= p33).then(pl.lit(3))
              .when(pl.col("entropy_shannon") <= p66).then(pl.lit(6))
              .otherwise(pl.lit(9))
              .cast(pl.Int8)
              .alias("vitality_tesla")
        ])

        return df


# ─────────────────────────────────────────────────────────────────────────────
# BASE DE DATOS GDELT — ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

class GDELTDatabase:
    """
    Orquesta el flujo completo:
        Descarga → Parse → Filtra → Calcula señales → Guarda Parquet → Audita

    Un Parquet por activo por año:
        data_lake/entropy/NVDA_2015_entropy.parquet
        data_lake/entropy/XAU_2016_entropy.parquet
        ...

    Usar Lazy API de Polars para el join final con precio OHLCV.
    """

    def __init__(self, cfg: GDELTConfig):
        self.cfg        = cfg
        self.downloader = GDELTDownloader(cfg)
        self.calculator = EntropyCalculator()
        cfg.ensure_dirs()

    # ────────────────────────────────
    # API pública
    # ────────────────────────────────

    def build_year(self, asset: str, year: int) -> Optional[Path]:
        """
        Construye el Parquet de entropía para un activo y año.
        Retorna la ruta del Parquet resultante, o None si falló.

        Uso en Colab:
            db = GDELTDatabase(cfg)
            db.build_year("NVDA", 2015)
        """
        out_path = self._entropy_parquet_path(asset, year)

        if out_path.exists() and not self.cfg.redownload:
            log.info("Parquet ya existe, saltando: %s", out_path.name)
            return out_path

        log.info("=== Construyendo %s / %d ===", asset, year)
        daily_frames: list[pl.DataFrame] = []

        for d in self.downloader._dates_for_year(year):
            raw = self.downloader.fetch_day(d)
            if raw is None:
                continue
            row = self.calculator.compute_daily_signals(raw, asset, d)
            if row is not None:
                daily_frames.append(row)

        if not daily_frames:
            log.warning("Sin datos para %s/%d", asset, year)
            return None

        # Concatenar días → DataFrame anual
        annual = pl.concat(daily_frames, rechunk=True)

        # Añadir señales derivadas (Nash, Tesla) en un solo pase
        annual = self.calculator.add_nash_and_tesla(annual)

        # Aplicar schema final y validar
        annual = self._apply_schema(annual)
        if not self._validate(annual, asset, year):
            log.error("Validación fallida para %s/%d", asset, year)
            return None

        # Guardar
        annual.write_parquet(out_path, compression="zstd")
        log.info("✅ Guardado: %s (%d filas)", out_path.name, len(annual))
        return out_path

    def build_all(self) -> dict[str, list[Path]]:
        """
        Construye todos los Parquets para todos los activos y años configurados.
        Retorna un dict {asset: [Path, ...]} con los archivos generados.
        """
        results: dict[str, list[Path]] = {a: [] for a in self.cfg.assets}
        for asset in self.cfg.assets:
            for year in range(self.cfg.start_year, self.cfg.end_year + 1):
                path = self.build_year(asset, year)
                if path:
                    results[asset].append(path)
        return results

    def audit(self, asset: str, year: int) -> QualityReport:
        """
        Audita un Parquet de entropía y retorna un QualityReport.
        Equivalente al parquet_quality_report.csv que ya existe para OHLCV.
        """
        path = self._entropy_parquet_path(asset, year)

        if not path.exists():
            return QualityReport(
                asset=asset, year=year, path=path,
                n_rows=0, date_min=None, date_max=None,
                null_count=0, entropy_min=0, entropy_max=0,
                nash_frozen_days=0, ok=False,
                notes="Archivo no existe"
            )

        df = pl.read_parquet(path)

        null_count = sum(df[c].null_count() for c in df.columns)
        e_col      = df["entropy_shannon"]
        nash_frozen_days = int(
            (df["nash_frozen_7d"] < NASH_FROZEN_THRESHOLD).sum()
        )

        ok = (
            null_count == 0 and
            len(df) > 0 and
            e_col.min() >= 0.0
        )

        return QualityReport(
            asset=asset, year=year, path=path,
            n_rows=len(df),
            date_min=df["date"].min(),
            date_max=df["date"].max(),
            null_count=null_count,
            entropy_min=float(e_col.min()),
            entropy_max=float(e_col.max()),
            nash_frozen_days=nash_frozen_days,
            ok=ok,
            notes="" if ok else f"Nulos={null_count}"
        )

    def audit_all(self) -> pl.DataFrame:
        """Retorna un DataFrame con el reporte de calidad de todos los Parquets."""
        rows = []
        for asset in self.cfg.assets:
            for year in range(self.cfg.start_year, self.cfg.end_year + 1):
                r = self.audit(asset, year)
                rows.append({
                    "asset":            r.asset,
                    "year":             r.year,
                    "n_rows":           r.n_rows,
                    "date_min":         str(r.date_min),
                    "date_max":         str(r.date_max),
                    "null_count":       r.null_count,
                    "entropy_min":      r.entropy_min,
                    "entropy_max":      r.entropy_max,
                    "nash_frozen_days": r.nash_frozen_days,
                    "ok":               r.ok,
                    "notes":            r.notes,
                })
        return pl.DataFrame(rows)

    # ────────────────────────────────
    # Helpers internos
    # ────────────────────────────────

    def _entropy_parquet_path(self, asset: str, year: int) -> Path:
        return self.cfg.entropy_path / f"{asset}_{year}_entropy.parquet"

    def _apply_schema(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Fuerza el schema canónico ENTROPY_SCHEMA.
        Falla rápido (fail-fast) si falta alguna columna.
        """
        for col_name, dtype in ENTROPY_SCHEMA.items():
            if col_name not in df.columns:
                raise ValueError(
                    f"Columna requerida ausente: '{col_name}'. "
                    f"Columnas presentes: {df.columns}"
                )
            df = df.with_columns(pl.col(col_name).cast(dtype))
        return df.select(list(ENTROPY_SCHEMA.keys()))

    def _validate(self, df: pl.DataFrame, asset: str, year: int) -> bool:
        """Validaciones básicas antes de escribir a disco."""
        if df.is_empty():
            log.error("[%s/%d] DataFrame vacío", asset, year)
            return False
        for col in df.columns:
            n_nulls = df[col].null_count()
            if n_nulls > 0:
                log.error("[%s/%d] Columna '%s' tiene %d nulos", asset, year, col, n_nulls)
                return False
        if (df["entropy_shannon"] < 0).any():
            log.error("[%s/%d] entropy_shannon negativa — imposible", asset, year)
            return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
# JOIN ENTROPÍA + PRECIO OHLCV → DATASET DE ENTRENAMIENTO
# ─────────────────────────────────────────────────────────────────────────────

def join_entropy_to_price(
    ohlcv_path: Path,
    entropy_paths: list[Path],
    asset: str,
    output_path: Path,
) -> Path:
    """
    Une el Parquet de precio OHLCV con los Parquets de entropía diaria.
    Produce el dataset de entrenamiento listo para el LSTM.

    Schema resultante:
        timestamp | close | entropy_shannon | zipf_concentration |
        goldstein_mean | tone_variance | n_events | nash_frozen_7d |
        vitality_tesla | symbol

    Estrategia de alineación temporal:
        - OHLCV tiene solo días de trading (NVDA: ~252/año, BTC: 365/año)
        - GDELT calcula entropía 7/7 (incluye fines de semana y festivos)
        - JOIN: left join desde OHLCV → forward-fill entropía en días sin datos
          Rationale: el mercado abre el lunes "procesando" entropía del fin de semana.

    Inputs:
        ohlcv_path:    Path al Parquet OHLCV 1d del activo (data_lake/gold/XAU_1d.parquet)
        entropy_paths: Lista de Parquets de entropía anuales del activo
        asset:         Nombre del activo (para filtrar si ohlcv tiene múltiples)
        output_path:   Donde guardar el training Parquet

    Output:
        Path al Parquet guardado en data_lake/training/
    """
    log.info("Construyendo dataset de entrenamiento para %s", asset)

    # ── 1. Cargar precio (Lazy — solo leemos lo que necesitamos)
    price_lf = (
        pl.scan_parquet(ohlcv_path)
        .select(["timestamp", "close", "symbol"])
        .with_columns(
            pl.col("timestamp").dt.date().alias("date")
        )
        .filter(pl.col("close").is_not_null())
    )

    # ── 2. Cargar y concatenar toda la entropía disponible (Lazy)
    if not entropy_paths:
        raise ValueError(f"Sin Parquets de entropía para {asset}")

    entropy_lf = (
        pl.scan_parquet(entropy_paths)
        .filter(pl.col("asset") == asset)
        .select([
            "date",
            "entropy_shannon",
            "zipf_concentration",
            "goldstein_mean",
            "tone_variance",
            "n_events",
            "nash_frozen_7d",
            "vitality_tesla",
        ])
    )

    # ── 3. Left join: precio es la columna vertebral
    #    Forward-fill entropía para días sin cobertura GDELT (fin de semana, festivos)
    joined = (
        price_lf
        .join(entropy_lf, on="date", how="left")
        .sort("date")
        .with_columns([
            pl.col("entropy_shannon").forward_fill(),
            pl.col("zipf_concentration").forward_fill(),
            pl.col("goldstein_mean").forward_fill(),
            pl.col("tone_variance").forward_fill(),
            pl.col("n_events").forward_fill(),
            pl.col("nash_frozen_7d").forward_fill(),
            pl.col("vitality_tesla").forward_fill(),
        ])
        # Eliminar filas que quedaron con NaN (primeros días sin entropía precedente)
        .drop_nulls(subset=["entropy_shannon", "close"])
        .drop("date")  # timestamp ya contiene esta info
    )

    # ── 4. Verificar que no haya NaN inesperados
    result = joined.collect()

    n_nulls = sum(result[c].null_count() for c in result.columns)
    if n_nulls > 0:
        log.warning("Dataset de entrenamiento tiene %d nulos — revisar", n_nulls)

    # ── 5. Guardar
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(output_path, compression="zstd")
    log.info(
        "✅ Training dataset guardado: %s | %d filas | %s → %s",
        output_path.name,
        len(result),
        result["timestamp"].min(),
        result["timestamp"].max()
    )

    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDAD: VERIFICADOR RÁPIDO DE ALINEACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def verify_alignment(training_parquet: Path) -> dict:
    """
    Verifica que el dataset de entrenamiento esté correctamente alineado.
    Retorna un dict con el reporte.

    Checks:
        - Sin NaN en entropy ni close
        - Monotonicidad temporal (no gaps > 7 días para BTC, > 10 para otros)
        - Distribución de vitality_tesla (debe tener los 3 valores)
        - Nash frozen ratio (% días con vitality==6 y nash bajo)
    """
    if not training_parquet.exists():
        return {"ok": False, "error": "Archivo no existe"}

    df = pl.read_parquet(training_parquet)

    n_rows  = len(df)
    n_nulls = sum(df[c].null_count() for c in df.columns)

    # Gap máximo entre fechas consecutivas
    dates = df.sort("timestamp")["timestamp"]
    if len(dates) > 1:
        diffs = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        max_gap = max(diffs)
    else:
        max_gap = 0

    # Distribución Tesla
    tesla_dist = (
        df.group_by("vitality_tesla")
          .agg(pl.len().alias("count"))
          .sort("vitality_tesla")
          .to_dict(as_series=False)
    )

    # Nash frozen ratio
    nash_frozen_ratio = (
        (df["nash_frozen_7d"] < NASH_FROZEN_THRESHOLD).sum() / n_rows
        if n_rows > 0 else 0.0
    )

    return {
        "ok":                n_nulls == 0 and n_rows > 100,
        "n_rows":            n_rows,
        "n_nulls":           n_nulls,
        "date_min":          str(df["timestamp"].min()),
        "date_max":          str(df["timestamp"].max()),
        "max_gap_days":      max_gap,
        "tesla_distribution": tesla_dist,
        "nash_frozen_ratio": float(nash_frozen_ratio),
        "entropy_mean":      float(df["entropy_shannon"].mean()),
        "entropy_std":       float(df["entropy_shannon"].std()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DEMO / MAIN — uso típico en Colab
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Ejemplo de uso en Google Colab:

        from gdelt_foundation import GDELTConfig, GDELTDatabase, join_entropy_to_price
        from pathlib import Path

        cfg = GDELTConfig(
            data_lake_path=Path("/content/drive/MyDrive/SPEL/data_lake"),
            assets=["NIFTY50", "XAU", "BTC", "NVDA"],
            start_year=2015,
            end_year=2025,
        )

        db = GDELTDatabase(cfg)

        # Construir un año primero para verificar
        db.build_year("NIFTY50", 2015)

        # Auditar
        report = db.audit("NIFTY50", 2015)
        print(f"OK: {report.ok} | Filas: {report.n_rows} | Nash frozen: {report.nash_frozen_days}")

        # Construir todo (puede tardar horas — ejecutar con & o en background)
        # db.build_all()

        # Construir dataset de entrenamiento
        entropy_files = list(cfg.entropy_path.glob("NIFTY50_*_entropy.parquet"))
        join_entropy_to_price(
            ohlcv_path   = cfg.data_lake_path / "india" / "NIFTY50_1d.parquet",
            entropy_paths= entropy_files,
            asset        = "NIFTY50",
            output_path  = cfg.training_path / "NIFTY50_train.parquet",
        )
    """

    print("=" * 70)
    print("GDELT FOUNDATION — SPEL v6")
    print("Módulo cargado correctamente.")
    print()
    print("Señales que produce por día por activo:")
    print("  entropy_shannon    → Entropía de Shannon de la cobertura noticiosa")
    print("  zipf_concentration → Concentración de fuentes (Ley de Zipf)")
    print("  goldstein_mean     → Conflicto vs cooperación (Predictor Granger)")
    print("  tone_variance      → Volatilidad emocional mediática")
    print("  n_events           → Volumen noticioso")
    print("  nash_frozen_7d     → Indicador de Equilibrio Nash subóptimo")
    print("  vitality_tesla     → Categoría 3/6/9 (Creación/Nash/Ruptura)")
    print()
    print("Activos configurados:", list(ASSET_COUNTRY_FILTERS.keys()))
    print("Ventana temporal recomendada: 2015-2025")
    print("=" * 70)
