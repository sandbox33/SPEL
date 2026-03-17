# ╔══════════════════════════════════════════════════════════════════╗
# ║  SPEL — AUDITOR DE DRIVE v1.0                                    ║
# ║  Misión: escanear TODO, clasificar por ADN, no tocar nada        ║
# ║  Ejecutar en Colab con Drive montado                             ║
# ║  REGLA: este script es READ-ONLY — nunca escribe fuera de        ║
# ║         /content/spel_audit_report/                              ║
# ╚══════════════════════════════════════════════════════════════════╝

# ── CELDA 1: Instalar dependencias ────────────────────────────────
# !pip install polars pyarrow pandas rich tabulate -q

import os, sys, json, hashlib, zipfile, io, re
from pathlib import Path
from datetime import datetime
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import polars as pl
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

console = Console()

# ── CONFIGURACIÓN ─────────────────────────────────────────────────
DRIVE_ROOT   = Path("/content/drive/MyDrive")
REPORT_DIR   = Path("/content/spel_audit_report")
REPORT_DIR.mkdir(exist_ok=True)

# Raíces a escanear — en orden de prioridad
SCAN_ROOTS = [
    DRIVE_ROOT / "SPEL-v2.0",
    DRIVE_ROOT / "_SPEL_CUARENTENA",
    DRIVE_ROOT / "SPEL_PROD",
    DRIVE_ROOT / "SPEL-v1.1",
    DRIVE_ROOT,   # búsqueda general como fallback
]

# Extensiones que nos interesan
TARGET_EXTENSIONS = {".parquet", ".csv", ".zip", ".pt", ".pth", ".json", ".ipynb"}

# Patrones de nombre SPEL conocidos
SPEL_PATTERNS = [
    r"XAU", r"BTC", r"NVDA", r"NIFTY", r"GDELT", r"gdelt",
    r"spel", r"SPEL", r"ohlcv", r"entropy", r"canonical",
    r"\d{14}\.export\.CSV",   # formato GDELT nativo
    r"checkpoint", r"epoch",
    r"XAG", r"WTI", r"SPY", r"VIX",
]
SPEL_REGEX = re.compile("|".join(SPEL_PATTERNS), re.IGNORECASE)

# ── COLUMNAS CANÓNICAS CONOCIDAS ──────────────────────────────────
CANON_V4_COLS = {
    "date","open","high","low","close","volume",
    "entropy_shannon","entropy_decay_lambda","entropy_psych_vix",
    "fibonacci_lag_1","fibonacci_lag_2","fibonacci_lag_3",
    "fibonacci_lag_5","fibonacci_lag_8","fibonacci_lag_13","fibonacci_lag_21",
    "goldstein_geo","n_events_ohlcv","vitality_tesla",
    "mass_panic_index","fear_momentum","vix_norm",
    "nash_frozen_7d","log_return"
}
GDELT_ENTROPY_COLS = {
    "date","asset","entropy_shannon","zipf_concentration",
    "goldstein_mean","tone_variance","n_events","nash_frozen_7d","vitality_tesla"
}
GDELT_RAW_COLS = {
    "GlobalEventID","Day","MonthYear","Year","FractionDate",
    "Actor1Code","Actor1Name","Actor1CountryCode","Actor1Type1Code",
    "Actor2Code","Actor2Name","Actor2CountryCode","Actor2Type1Code",
    "IsRootEvent","EventCode","EventBaseCode","EventRootCode",
    "QuadClass","GoldsteinScale","NumMentions","NumSources","NumArticles",
    "AvgTone","Actor1Geo_Type","Actor1Geo_FullName","Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code","Actor1Geo_Lat","Actor1Geo_Long","Actor1Geo_FeatureID",
    "Actor2Geo_Type","Actor2Geo_FullName","Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code","Actor2Geo_Lat","Actor2Geo_Long","Actor2Geo_FeatureID",
    "ActionGeo_Type","ActionGeo_FullName","ActionGeo_CountryCode",
    "ActionGeo_ADM1Code","ActionGeo_Lat","ActionGeo_Long","ActionGeo_FeatureID",
    "DATEADDED","SOURCEURL"
}

# SHA esperados de parquets v2.0
KNOWN_SHA = {
    "NVDA_ohlcv_v5.parquet":         "3627a749da49",
    "BTC_ohlcv_v5.parquet":          "a2c4e6f6e816",
    "XAU_ohlcv_v5.parquet":          "a8e10cff2e80",
    "NIFTY50_ohlcv_v5.parquet":      "5e9624595c03",
}

# ── CLASES DE RESULTADO ───────────────────────────────────────────
VERDICT = {
    "CANON_V4":       ("✅ CANON v4",      "Parquet limpio SPEL-v2.0"),
    "GDELT_ENTROPY":  ("✅ GDELT Entropy", "Parquet entropía procesada"),
    "GDELT_RAW_ZIP":  ("🥇 GDELT Raw ZIP", "CSV nativo GDELT — procesar con gdelt_foundation"),
    "GDELT_RAW_CSV":  ("🥇 GDELT Raw CSV", "CSV nativo GDELT descomprimido"),
    "INTRADAY_CSV":   ("⚠️ Intraday CSV",  "Datos intraday crudos — convertir a parquet"),
    "INTRADAY_PQ":    ("⚠️ Intraday PQ",   "Parquet intraday — verificar schema"),
    "PARQUET_DIRTY":  ("❌ Parquet sucio", "Schema incompatible o date=String — NO USAR"),
    "CHECKPOINT_PT":  ("❌ Checkpoint",    "Modelo PyTorch — features incompatibles v1.1"),
    "OHLCV_CSV":      ("♻️ OHLCV CSV",    "CSV OHLCV crudo — ya existe como parquet v2.0"),
    "REPORT_CSV":     ("🗑️ Reporte",       "Archivo de reporte/metadata — eliminar"),
    "NOTEBOOK":       ("📓 Notebook",      "Jupyter notebook — revisar manualmente"),
    "UNKNOWN":        ("❓ Desconocido",   "Clasificar manualmente"),
}


# ═══════════════════════════════════════════════════════════════════
# FUNCIONES DE ANÁLISIS
# ═══════════════════════════════════════════════════════════════════

def sha_file(path: Path, bytes_limit: int = 50_000_000) -> str:
    """SHA MD5 de los primeros N bytes (rápido para archivos grandes)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        chunk = f.read(bytes_limit)
        h.update(chunk)
    return h.hexdigest()[:12]


def size_human(n_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def sniff_date_range(df: pd.DataFrame) -> tuple[Optional[str], Optional[str]]:
    """Detecta columna de fecha y retorna (min, max)."""
    for col in ["date", "Date", "DATE", "datetime", "timestamp", "Day"]:
        if col in df.columns:
            try:
                dates = pd.to_datetime(df[col], errors="coerce").dropna()
                if len(dates) > 0:
                    return str(dates.min().date()), str(dates.max().date())
            except Exception:
                pass
    return None, None


def detect_date_dtype_issue(df: pd.DataFrame) -> Optional[str]:
    """Detecta BUG-DATE-01: date como String."""
    for col in ["date", "Date"]:
        if col in df.columns:
            dtype = str(df[col].dtype)
            if dtype == "object":
                return f"BUG-DATE-01: '{col}' es String (object) — necesita conversión"
            if "tz" not in dtype.lower() and "datetime" in dtype.lower():
                return f"WARN: '{col}' es datetime sin timezone — convertir a UTC"
    return None


def analyze_parquet(path: Path) -> dict:
    """Analiza un archivo parquet y retorna su diagnóstico."""
    result = {
        "path": str(path), "name": path.name, "ext": ".parquet",
        "size_bytes": path.stat().st_size, "size_human": size_human(path.stat().st_size),
        "sha": None, "verdict": None, "verdict_detail": None,
        "rows": None, "cols": None, "columns": None,
        "date_from": None, "date_to": None,
        "date_dtype": None, "date_issue": None,
        "schema_match": None, "schema_missing": None, "schema_extra": None,
        "sha_match": None, "notes": [],
    }

    try:
        sha = sha_file(path)
        result["sha"] = sha

        # SHA conocido
        if path.name in KNOWN_SHA:
            expected = KNOWN_SHA[path.name]
            result["sha_match"] = sha.startswith(expected[:8])
            if result["sha_match"]:
                result["notes"].append(f"SHA verificado ✅ {expected}")
            else:
                result["notes"].append(f"SHA NO COINCIDE ❌ esperado:{expected} actual:{sha}")

        df = pd.read_parquet(path)
        result["rows"]    = len(df)
        result["cols"]    = len(df.columns)
        result["columns"] = list(df.columns)

        col_set = set(df.columns)

        # Detectar dtype de fecha
        if "date" in df.columns or df.index.name == "date":
            try:
                if df.index.name == "date":
                    dtype = str(df.index.dtype)
                else:
                    dtype = str(df["date"].dtype)
                result["date_dtype"] = dtype
                issue = detect_date_dtype_issue(df.reset_index() if df.index.name == "date" else df)
                result["date_issue"] = issue
            except Exception:
                pass

        # Rango de fechas
        df_reset = df.reset_index() if df.index.name == "date" else df
        d_from, d_to = sniff_date_range(df_reset)
        result["date_from"] = d_from
        result["date_to"]   = d_to

        # Clasificar schema
        missing_v4    = CANON_V4_COLS - col_set - {"date"}  # date puede estar en index
        missing_gdelt = GDELT_ENTROPY_COLS - col_set

        if len(missing_v4) <= 3 and result["rows"] > 1000:
            result["verdict"]        = "CANON_V4"
            result["schema_match"]   = True
            result["schema_missing"] = list(missing_v4)
            if result["date_issue"]:
                result["verdict"] = "PARQUET_DIRTY"
                result["notes"].append(result["date_issue"])

        elif len(missing_gdelt) <= 2 and "entropy_shannon" in col_set:
            result["verdict"]        = "GDELT_ENTROPY"
            result["schema_match"]   = True
            result["schema_missing"] = list(missing_gdelt)

        elif path.name in ["XAU_1m.parquet", "XAU_15m.parquet",
                           "NIFTY50_1m.parquet", "INDIA_VIX_1m.parquet"] or \
             any(x in path.name for x in ["_1m", "_15m", "_30m", "_4h", "_minute"]):
            result["verdict"] = "INTRADAY_PQ"
            result["notes"].append("Verificar schema antes de integrar")

        elif result["rows"] < 100:
            result["verdict"] = "REPORT_CSV"
            result["notes"].append("Muy pocas filas — probablemente reporte")

        else:
            result["verdict"] = "PARQUET_DIRTY"
            result["schema_missing"] = list(missing_v4)[:10]
            result["notes"].append(f"Schema desconocido — cols: {list(col_set)[:8]}")
            if result["date_issue"]:
                result["notes"].append(result["date_issue"])

        # Checkpoint PyTorch disfrazado
        if any(x in path.name.lower() for x in ["checkpoint", "epoch", "model", "weights"]):
            result["verdict"] = "CHECKPOINT_PT"

    except Exception as e:
        result["verdict"] = "UNKNOWN"
        result["notes"].append(f"Error al leer: {str(e)[:100]}")

    v = VERDICT.get(result["verdict"], VERDICT["UNKNOWN"])
    result["verdict_label"]  = v[0]
    result["verdict_detail"] = v[1]
    return result


def analyze_csv(path: Path) -> dict:
    """Analiza un CSV y retorna su diagnóstico."""
    result = {
        "path": str(path), "name": path.name, "ext": ".csv",
        "size_bytes": path.stat().st_size, "size_human": size_human(path.stat().st_size),
        "sha": sha_file(path), "verdict": None, "verdict_detail": None,
        "rows": None, "cols": None, "columns": None,
        "date_from": None, "date_to": None,
        "date_dtype": None, "date_issue": None,
        "notes": [],
    }

    try:
        # Leer solo primeras/últimas filas para no cargar todo en memoria
        df_head = pd.read_csv(path, nrows=5, low_memory=False)
        result["cols"]    = len(df_head.columns)
        result["columns"] = list(df_head.columns)

        # Contar filas eficientemente
        with open(path, "rb") as f:
            result["rows"] = sum(1 for _ in f) - 1  # -1 header

        col_set = set(df_head.columns)

        # Detectar GDELT nativo por columnas características
        gdelt_native_cols = {"GlobalEventID", "GoldsteinScale", "AvgTone", "SOURCEURL",
                             "Actor1Code", "Actor2Code", "ActionGeo_FullName"}
        if len(gdelt_native_cols & col_set) >= 4:
            result["verdict"] = "GDELT_RAW_CSV"
            # Leer sample para fecha
            df_sample = pd.read_csv(path, usecols=["Day"], nrows=1000,
                                    low_memory=False)
            if "Day" in df_sample.columns:
                days = pd.to_datetime(df_sample["Day"].astype(str), format="%Y%m%d",
                                      errors="coerce").dropna()
                if len(days):
                    result["date_from"] = str(days.min().date())
                    # Para fecha max, leer cola del archivo
                    df_tail = pd.read_csv(path, usecols=["Day"],
                                          skiprows=lambda i: i > 0 and i < max(1, result["rows"]-100),
                                          low_memory=False)
                    days_tail = pd.to_datetime(df_tail["Day"].astype(str), format="%Y%m%d",
                                               errors="coerce").dropna()
                    if len(days_tail):
                        result["date_to"] = str(days_tail.max().date())
            result["notes"].append(f"GDELT nativo — procesar con gdelt_foundation.py")

        # Intraday NIFTY sectores
        elif any(x in path.name.upper() for x in ["NIFTY", "FMCG", "CPSE", "LARGEMID",
                                                    "CONSUMPTION", "MIDCAP", "SMALLCAP"]) and \
             "minute" in path.name.lower():
            result["verdict"] = "INTRADAY_CSV"
            df_sample = pd.read_csv(path, nrows=3, low_memory=False)
            d_from, d_to = sniff_date_range(df_sample)
            result["date_from"] = d_from
            result["notes"].append("Sector NIFTY intraday — RESCATAR → convertir a parquet")

        # XAU intraday
        elif any(x in path.name.upper() for x in ["XAU"]) and \
             any(x in path.name.lower() for x in ["1m", "4h", "30m", "15m", "minute", "intraday"]):
            result["verdict"] = "INTRADAY_CSV"
            result["notes"].append("XAU intraday CSV — RESCATAR → convertir a parquet")

        # OHLCV CSV conocidos (ya tenemos como parquet)
        elif any(x in path.name for x in ["BTC_USD_Yahoo", "NVIDIA_historical",
                                           "historical_data", "_Yahoo"]):
            result["verdict"] = "OHLCV_CSV"
            result["notes"].append("Ya existe como parquet v2.0 verificado — puede borrarse")

        # Reportes
        elif any(x in path.name.lower() for x in ["report", "summary", "quality",
                                                    "structure", "manifest"]):
            result["verdict"] = "REPORT_CSV"
            result["notes"].append("Reporte/metadata — eliminar con seguridad")

        else:
            result["verdict"] = "UNKNOWN"
            result["notes"].append(f"Cols: {list(col_set)[:6]}")

    except Exception as e:
        result["verdict"] = "UNKNOWN"
        result["notes"].append(f"Error al leer: {str(e)[:100]}")

    v = VERDICT.get(result["verdict"], VERDICT["UNKNOWN"])
    result["verdict_label"]  = v[0]
    result["verdict_detail"] = v[1]
    return result


def analyze_zip(path: Path) -> dict:
    """Analiza un ZIP — especialmente los GDELT .export.CSV.zip"""
    result = {
        "path": str(path), "name": path.name, "ext": ".zip",
        "size_bytes": path.stat().st_size, "size_human": size_human(path.stat().st_size),
        "sha": sha_file(path, 10_000_000), "verdict": None, "verdict_detail": None,
        "rows": None, "cols": None, "columns": None,
        "date_from": None, "date_to": None,
        "notes": [],
    }

    # Detectar GDELT por nombre: YYYYMMDDHHMMSS.export.CSV.zip
    gdelt_name_re = re.match(r"(\d{4})(\d{2})(\d{2})\d{6}\.export\.CSV\.zip", path.name)
    if gdelt_name_re:
        y, m, d = gdelt_name_re.groups()
        date_str = f"{y}-{m}-{d}"
        result["verdict"]   = "GDELT_RAW_ZIP"
        result["date_from"] = date_str
        result["date_to"]   = date_str
        result["notes"].append(f"GDELT evento diario — fecha: {date_str}")

        # Peek sin descomprimir completo
        try:
            with zipfile.ZipFile(path) as zf:
                inner_files = zf.namelist()
                result["notes"].append(f"Archivos internos: {inner_files[:3]}")
                if inner_files:
                    with zf.open(inner_files[0]) as f:
                        sample = f.read(4096).decode("latin-1", errors="replace")
                        first_line  = sample.split("\n")[0]
                        second_line = sample.split("\n")[1] if len(sample.split("\n")) > 1 else ""
                        cols = first_line.split("\t")
                        # GDELT no tiene header — tab-separated, 57-61 cols
                        result["cols"] = len(cols)
                        if len(cols) >= 50:
                            result["notes"].append(f"✅ GDELT nativo confirmado — {len(cols)} columnas tab-separated")
                            result["notes"].append(f"Sample col[0]: {cols[0][:20]}")
                        else:
                            result["notes"].append(f"⚠️ Solo {len(cols)} cols — verificar")
        except Exception as e:
            result["notes"].append(f"No se pudo peek: {str(e)[:60]}")
    else:
        result["verdict"] = "UNKNOWN"
        result["notes"].append("ZIP no reconocido — revisar manualmente")

    v = VERDICT.get(result["verdict"], VERDICT["UNKNOWN"])
    result["verdict_label"]  = v[0]
    result["verdict_detail"] = v[1]
    return result


def analyze_checkpoint(path: Path) -> dict:
    """Detecta checkpoints PyTorch sin cargarlos (evita memoria)."""
    return {
        "path": str(path), "name": path.name, "ext": path.suffix,
        "size_bytes": path.stat().st_size, "size_human": size_human(path.stat().st_size),
        "sha": sha_file(path, 1_000_000),
        "verdict": "CHECKPOINT_PT",
        "verdict_label": VERDICT["CHECKPOINT_PT"][0],
        "verdict_detail": VERDICT["CHECKPOINT_PT"][1],
        "rows": None, "cols": None, "columns": None,
        "date_from": None, "date_to": None,
        "notes": ["Checkpoint v1.1 — features incompatibles — NO USAR para entrenamiento"],
    }


# ═══════════════════════════════════════════════════════════════════
# ESCÁNER PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def scan_drive() -> list[dict]:
    """Escanea Drive y retorna lista de diagnósticos."""
    all_results = []
    seen_paths  = set()

    console.print(Panel.fit(
        "[bold yellow]SPEL DRIVE AUDITOR v1.0[/bold yellow]\n"
        "[dim]READ-ONLY · No modifica ningún archivo[/dim]",
        border_style="yellow"
    ))

    for root in SCAN_ROOTS:
        if not root.exists():
            console.print(f"[dim]⏭  Skip (no existe): {root}[/dim]")
            continue

        console.print(f"\n[cyan]📂 Escaneando: {root}[/cyan]")
        files_found = 0

        # Para DRIVE_ROOT hacemos búsqueda superficial para no tomar horas
        max_depth = 99 if root != DRIVE_ROOT else 3

        for path in root.rglob("*"):
            # Control de profundidad para Drive root
            rel = path.relative_to(root)
            depth = len(rel.parts)
            if depth > max_depth:
                continue

            if not path.is_file():
                continue
            if path in seen_paths:
                continue
            if path.suffix.lower() not in TARGET_EXTENSIONS:
                continue

            # Filtrar por patrones SPEL en nombre o ruta
            if root == DRIVE_ROOT and not SPEL_REGEX.search(str(path)):
                continue

            seen_paths.add(path)
            files_found += 1

            ext = path.suffix.lower()
            try:
                if ext == ".parquet":
                    result = analyze_parquet(path)
                elif ext == ".csv":
                    result = analyze_csv(path)
                elif ext == ".zip":
                    result = analyze_zip(path)
                elif ext in (".pt", ".pth"):
                    result = analyze_checkpoint(path)
                elif ext == ".ipynb":
                    result = {
                        "path": str(path), "name": path.name, "ext": ".ipynb",
                        "size_bytes": path.stat().st_size,
                        "size_human": size_human(path.stat().st_size),
                        "sha": sha_file(path, 1_000_000),
                        "verdict": "NOTEBOOK",
                        "verdict_label": VERDICT["NOTEBOOK"][0],
                        "verdict_detail": VERDICT["NOTEBOOK"][1],
                        "rows": None, "cols": None, "columns": None,
                        "date_from": None, "date_to": None,
                        "notes": ["Revisar manualmente — puede tener lógica de auditoría útil"],
                    }
                else:
                    continue

                # Añadir contexto de ubicación
                result["drive_root"] = root.name
                result["relative_path"] = str(path.relative_to(DRIVE_ROOT))
                all_results.append(result)

                # Preview en consola
                v_label = result.get("verdict_label", "?")
                console.print(
                    f"  {v_label:<25} "
                    f"[dim]{result['size_human']:>8}[/dim]  "
                    f"[white]{path.name}[/white]"
                )

            except Exception as e:
                console.print(f"  [red]ERROR[/red] {path.name}: {str(e)[:60]}")

        console.print(f"  [dim]→ {files_found} archivos relevantes encontrados[/dim]")

    return all_results


# ═══════════════════════════════════════════════════════════════════
# REPORTE
# ═══════════════════════════════════════════════════════════════════

def generate_report(results: list[dict]):
    """Genera reporte completo en JSON, CSV y resumen en consola."""

    df = pd.DataFrame(results)

    # ── Reporte JSON completo ─────────────────────────────────────
    json_path = REPORT_DIR / "spel_audit_full.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    console.print(f"\n[green]✅ JSON completo: {json_path}[/green]")

    # ── Reporte CSV para Drive ────────────────────────────────────
    csv_cols = ["verdict_label", "name", "size_human", "rows", "date_from",
                "date_to", "sha", "sha_match", "date_issue", "relative_path"]
    df_csv = df[[c for c in csv_cols if c in df.columns]].copy()
    csv_path = REPORT_DIR / "spel_audit_summary.csv"
    df_csv.to_csv(csv_path, index=False)
    console.print(f"[green]✅ CSV resumen: {csv_path}[/green]")

    # ── Resumen por veredicto ─────────────────────────────────────
    console.print("\n")
    console.print(Panel.fit("[bold]RESUMEN POR VEREDICTO[/bold]", border_style="cyan"))

    verdict_counts = df.groupby("verdict").agg(
        count=("name", "count"),
        total_mb=("size_bytes", lambda x: round(x.sum() / 1_000_000, 1))
    ).reset_index().sort_values("total_mb", ascending=False)

    table_v = Table(show_header=True, header_style="bold yellow",
                    border_style="dim", padding=(0, 1))
    table_v.add_column("Veredicto",    style="white",  min_width=20)
    table_v.add_column("Archivos",     style="cyan",   justify="right")
    table_v.add_column("Tamaño total", style="green",  justify="right")
    table_v.add_column("Acción",       style="yellow")

    ACTIONS = {
        "CANON_V4":       "✅ CONSERVAR — ya está en v2.0",
        "GDELT_ENTROPY":  "✅ CONSERVAR — parquet procesado",
        "GDELT_RAW_ZIP":  "🥇 RESCATAR — procesar con gdelt_foundation",
        "GDELT_RAW_CSV":  "🥇 RESCATAR — procesar con gdelt_foundation",
        "INTRADAY_CSV":   "⚠️ RESCATAR — convertir a parquet",
        "INTRADAY_PQ":    "⚠️ VERIFICAR schema antes de integrar",
        "PARQUET_DIRTY":  "❌ ELIMINAR — contaminado",
        "CHECKPOINT_PT":  "❌ ELIMINAR — features incompatibles",
        "OHLCV_CSV":      "🗑️ ELIMINAR — duplicado de parquet v2.0",
        "REPORT_CSV":     "🗑️ ELIMINAR — metadata inútil",
        "NOTEBOOK":       "📓 REVISAR — puede tener código útil",
        "UNKNOWN":        "❓ REVISAR manualmente",
    }

    for _, row in verdict_counts.iterrows():
        v = row["verdict"]
        label = VERDICT.get(v, ("?",""))[0]
        table_v.add_row(
            label, str(row["count"]), f"{row['total_mb']} MB",
            ACTIONS.get(v, "—")
        )

    console.print(table_v)

    # ── Resumen GDELT ZIPs por fecha ──────────────────────────────
    gdelt_zips = df[df["verdict"] == "GDELT_RAW_ZIP"].copy()
    if not gdelt_zips.empty:
        console.print("\n")
        console.print(Panel.fit("[bold]GDELT ZIPs — COBERTURA TEMPORAL[/bold]",
                                border_style="yellow"))
        gdelt_zips_sorted = gdelt_zips.sort_values("date_from")

        console.print(f"  Total ZIPs: [cyan]{len(gdelt_zips)}[/cyan]")
        console.print(f"  Desde: [cyan]{gdelt_zips_sorted['date_from'].iloc[0]}[/cyan]")
        console.print(f"  Hasta: [cyan]{gdelt_zips_sorted['date_from'].iloc[-1]}[/cyan]")
        console.print(f"  Tamaño total: [cyan]{gdelt_zips['size_bytes'].sum()/1e9:.2f} GB[/cyan]")

        # Fechas únicas cubiertas
        dates_covered = sorted(gdelt_zips_sorted["date_from"].dropna().unique())

        # Gap conocido: 2026-01-01 → 2026-03-09
        gap_start = "2026-01-01"
        gap_end   = "2026-03-09"
        gap_covered = [d for d in dates_covered if gap_start <= d <= gap_end]
        console.print(f"\n  De los 68 días de gap (2026-01-01 → 2026-03-09):")
        console.print(f"  → Cubiertos por ZIPs locales: [green]{len(gap_covered)}[/green]")
        console.print(f"  → Aún por descargar: [red]{68 - len(gap_covered)}[/red]")

        # Exportar lista de fechas cubiertas
        covered_path = REPORT_DIR / "gdelt_dates_covered.json"
        with open(covered_path, "w") as f:
            json.dump({"dates": dates_covered, "total": len(dates_covered)}, f, indent=2)
        console.print(f"\n  [green]✅ Fechas guardadas: {covered_path}[/green]")

    # ── Archivos rescatables urgentes ─────────────────────────────
    rescue = df[df["verdict"].isin(["INTRADAY_CSV", "GDELT_RAW_CSV"])].copy()
    if not rescue.empty:
        console.print("\n")
        console.print(Panel.fit(
            "[bold red]⚠️ RESCATAR URGENTE — ANTES DE VACIAR PAPELERA[/bold red]",
            border_style="red"
        ))
        for _, row in rescue.iterrows():
            console.print(f"  📌 {row['name']}  [dim]{row['size_human']}[/dim]")
            console.print(f"     [dim]{row['relative_path']}[/dim]")

    # ── Estimación de espacio liberable ──────────────────────────
    deletable = df[df["verdict"].isin([
        "PARQUET_DIRTY", "CHECKPOINT_PT", "OHLCV_CSV", "REPORT_CSV"
    ])]
    if not deletable.empty:
        mb = deletable["size_bytes"].sum() / 1e6
        console.print(f"\n[bold]Espacio liberable (basura confirmada):[/bold] "
                      f"[red]{mb:.0f} MB ({mb/1024:.1f} GB)[/red]")

    total_scanned = df["size_bytes"].sum() / 1e9
    console.print(f"[bold]Total escaneado:[/bold] [cyan]{total_scanned:.2f} GB[/cyan]")

    # ── Instrucciones post-auditoría ──────────────────────────────
    console.print("\n")
    console.print(Panel(
        "[bold yellow]PRÓXIMOS PASOS[/bold yellow]\n\n"
        "1. Revisar [cyan]spel_audit_full.json[/cyan] para lista completa\n"
        "2. Copiar GDELT ZIPs rescatables a [cyan]SPEL-v2.0/gdelt_raw/[/cyan]\n"
        "3. Ejecutar [cyan]gdelt_foundation.py[/cyan] sobre los ZIPs rescatados\n"
        "4. Mover CSVs intraday a [cyan]SPEL-v2.0/data_lake/*/intraday/raw_csv/[/cyan]\n"
        "5. Eliminar todo lo marcado como [red]PARQUET_DIRTY[/red] y [red]CHECKPOINT_PT[/red]\n"
        "6. Actualizar [cyan]spel_asset_catalog.json[/cyan] con fechas reales del reporte\n"
        "7. Recién entonces subir el [cyan]spel_data_downloader.yml[/cyan] al repo",
        border_style="yellow"
    ))

    return df


# ═══════════════════════════════════════════════════════════════════
# MAIN — Ejecutar todo
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Montar Drive si no está montado
    try:
        from google.colab import drive
        if not Path("/content/drive/MyDrive").exists():
            console.print("[yellow]Montando Google Drive...[/yellow]")
            drive.mount("/content/drive")
    except ImportError:
        console.print("[dim]No es Colab — asumiendo Drive ya montado[/dim]")

    start = datetime.utcnow()
    console.print(f"[dim]Inicio: {start.strftime('%Y-%m-%d %H:%M:%S UTC')}[/dim]\n")

    results = scan_drive()

    if not results:
        console.print("[red]No se encontraron archivos relevantes.[/red]")
        console.print("Verifica que Drive está montado y las rutas en SCAN_ROOTS son correctas.")
        sys.exit(1)

    df_report = generate_report(results)

    elapsed = (datetime.utcnow() - start).seconds
    console.print(f"\n[dim]Auditoría completada en {elapsed}s · "
                  f"{len(results)} archivos analizados[/dim]")
    console.print(f"[dim]Reportes en: {REPORT_DIR}[/dim]")
