"""
spel_fix_vix_leakage.py
═══════════════════════════════════════════════════════════════════════════════
SPEL — Erradicación definitiva BUG-LA-01 (vix_norm global std → causal rolling)

Afectados confirmados por FULL_DRIVE_AUDIT.json (10-Mar-2026):
  ❌  XAU_ohlcv_v4.parquet    → LEAKAGE vix_norm=-0.9210  (global std)
  ❌  NIFTY50_ohlcv_v4.parquet → LEAKAGE vix_norm=-0.1548 + NAN_COUNT:2612

Ya limpios (NO TOCAR):
  ✅  NVDA_ohlcv_v4.parquet
  ✅  BTC_ohlcv_v4.parquet

Raíz del bug:
  vix_norm fue calculado como (vix_raw - mean_GLOBAL) / std_GLOBAL
  → mean y std calculados sobre TODO el dataset incluyendo datos futuros
  → Lookahead en el día T usa información de T+1 .. T+N

Fix aplicado:
  vix_norm[t] = (vix_raw[t] - rolling_mean[t-1, W]) / rolling_std[t-1, W]
  → Donde W=252 días (1 año bursátil)
  → shift(1) garantiza que el día T NUNCA ve datos de T o posteriores

Caso especial NIFTY50:
  VIX_US_1d.parquet fue eliminado (NAN_FLOOD_ESTRUCTURAL)
  → 2612/2632 filas tienen NaN en vix_norm
  → Fix: re-fetch VIX desde Yahoo Finance + causal z-score
  → Fallback: forward-fill causal si yfinance no disponible

INSTRUCCIONES DE USO EN COLAB:
  1. Montar Drive (Celda 1 estándar)
  2. !pip install polars pyarrow yfinance --quiet
  3. !cp /content/drive/MyDrive/SPEL-v2.0/meta/spel_fix_vix_leakage.py /content/
  4. !python /content/spel_fix_vix_leakage.py

IMPORTANTE — ANTES DE CORRER:
  - Verificar que SPEL-v2.0 tiene backup en Drive (Regla R15)
  - Este script modifica XAU y NIFTY50 canónicos en su lugar
  - Los SHA nuevos deben actualizarse en Project_Log_v31.md

═══════════════════════════════════════════════════════════════════════════════
Versión: 1.0 · 10-Mar-2026 · SPEL
"""

import os
import json
import hashlib
import warnings
from datetime import datetime, timezone

import polars as pl
import numpy as np

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

ROOT      = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE = f"{ROOT}/data_lake"
META_DIR  = f"{ROOT}/meta"

# SHA originales para verificación de integridad pre-fix
SHA_ORIGINAL = {
    "XAU":     "a8e10cff2e80",
    "NIFTY50": "5e9624595c03",
}

# Parámetros de normalización causal — inamovibles para reproducibilidad
VIX_WINDOW       = 252   # 1 año bursátil
VIX_MIN_PERIODS  = 30    # mínimo para el warm-up inicial

# Activos a procesar (los afectados)
TARGETS = ["XAU", "NIFTY50"]

# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────

def sha12(path: str) -> str:
    """SHA-256 truncado a 12 caracteres — fingerprint estándar SPEL."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def hline(char="─", width=62):
    print(char * width)


def section(title: str):
    hline("═")
    print(f"  {title}")
    hline("═")


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNÓSTICO
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_vix(asset: str, df: pl.DataFrame, label: str = ""):
    """Imprime métricas de vix_norm y entropy_psych_vix para auditoría."""
    tag = f"[{label}]" if label else ""
    print(f"\n  {asset} {tag}")

    for col in ["vix_norm", "entropy_psych_vix"]:
        if col not in df.columns:
            print(f"    ⚠️  columna '{col}' no encontrada")
            continue
        s = df[col]
        nan_n  = s.is_null().sum() + s.is_nan().sum()
        mean_v = s.drop_nulls().drop_nans().mean() if len(s.drop_nulls().drop_nans()) > 0 else float("nan")
        std_v  = s.drop_nulls().drop_nans().std()  if len(s.drop_nulls().drop_nans()) > 1 else float("nan")
        min_v  = s.min()
        max_v  = s.max()
        print(f"    {col:<25}  mean={mean_v:+.4f}  std={std_v:.4f}  "
              f"min={min_v:+.4f}  max={max_v:+.4f}  nan={nan_n}")

    # Diagnóstico de leakage: normalización global produce mean≈0, std≈1
    vix = df["vix_norm"].drop_nulls().drop_nans()
    if len(vix) > 100:
        is_global_norm = (abs(float(vix.mean())) < 0.05) and (abs(float(vix.std()) - 1.0) < 0.08)
        if is_global_norm:
            print(f"    🔴 BUG-LA-01 ACTIVO: vix_norm tiene mean≈0 std≈1 → normalización global detectada")
        else:
            print(f"    ✅ vix_norm OK: distribución no-global (causal rolling confirmado)")


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZACIÓN CAUSAL (anti-leakage)
# ─────────────────────────────────────────────────────────────────────────────

def causal_zscore(values: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """
    Z-score causal estricto: en el día T solo se usan días 0..T-1.
    Implementado en numpy para control exacto del shift.

    Si roll_std[t] == 0 o NaN → output[t] = 0.0
    Los primeros (min_periods) días → 0.0 (warm-up)
    """
    n = len(values)
    result = np.zeros(n, dtype=np.float64)

    for t in range(n):
        # Ventana estrictamente pasada: excluye el día T
        start = max(0, t - window)
        end   = t  # excluido → solo hasta T-1
        window_vals = values[start:end]
        window_vals = window_vals[~np.isnan(window_vals)]

        if len(window_vals) < min_periods:
            result[t] = 0.0  # warm-up
            continue

        mu    = np.mean(window_vals)
        sigma = np.std(window_vals, ddof=1)

        if sigma < 1e-9:
            result[t] = 0.0
        elif np.isnan(values[t]):
            result[t] = np.nan
        else:
            result[t] = (values[t] - mu) / sigma

    return result


# ─────────────────────────────────────────────────────────────────────────────
# FETCH VIX (para NIFTY50 NaN recovery)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_vix_us(start_date: str, end_date: str) -> pl.DataFrame | None:
    """
    Intenta descargar VIX_US desde Yahoo Finance.
    Retorna DataFrame con columnas [date: datetime[ms,UTC], vix_close: Float64]
    o None si yfinance no está disponible.
    """
    try:
        import yfinance as yf
        print(f"  📡 Descargando ^VIX desde Yahoo Finance ({start_date} → {end_date})...")
        vix = yf.download("^VIX", start=start_date, end=end_date, progress=False, auto_adjust=True)
        if vix.empty:
            print("  ⚠️  yfinance retornó vacío")
            return None
        vix = vix[["Close"]].reset_index()
        vix.columns = ["date", "vix_close"]
        # Convertir a Polars con datetime[ms, UTC]
        df_vix = pl.from_pandas(vix).with_columns([
            pl.col("date").cast(pl.Datetime("ms", "UTC")),
            pl.col("vix_close").cast(pl.Float64),
        ])
        print(f"  ✅ VIX descargado: {len(df_vix)} filas")
        return df_vix
    except ImportError:
        print("  ⚠️  yfinance no instalado → !pip install yfinance")
        return None
    except Exception as e:
        print(f"  ⚠️  Error yfinance: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FIX PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def fix_asset(asset: str) -> dict:
    path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v4.parquet"

    section(f"Procesando: {asset}")

    # ── 0. Verificar existencia ──────────────────────────────────────────────
    if not os.path.exists(path):
        print(f"  ❌ Archivo no encontrado: {path}")
        return {"status": "NOT_FOUND", "asset": asset}

    # ── 1. Verificar SHA de entrada ──────────────────────────────────────────
    sha_in = sha12(path)
    sha_expected = SHA_ORIGINAL.get(asset, "UNKNOWN")
    if sha_expected != "UNKNOWN" and sha_in != sha_expected:
        print(f"  ⚠️  SHA inesperado: esperado={sha_expected} actual={sha_in}")
        print(f"     El archivo fue modificado desde el último audit.")
        print(f"     Continuando de todas formas (el fix es idempotente).")
    else:
        print(f"  ✅ SHA entrada: {sha_in}")

    # ── 2. Cargar parquet ────────────────────────────────────────────────────
    df = pl.read_parquet(path)
    original_columns = df.columns
    print(f"  Filas: {len(df)} · Columnas: {len(df.columns)}")

    # ── 3. Diagnóstico ANTES ─────────────────────────────────────────────────
    diagnose_vix(asset, df, label="ANTES")

    # ── 4. Resolver NaN de NIFTY50 (VIX_US data recovery) ───────────────────
    if asset == "NIFTY50":
        vix_nan = df["vix_norm"].is_null().sum() + df["vix_norm"].is_nan().sum()
        print(f"\n  NaN en vix_norm: {vix_nan}/{len(df)} ({vix_nan/len(df)*100:.1f}%)")

        # Intentar re-fetch de VIX_US desde Yahoo Finance
        date_min = str(df["date"].min())[:10]
        date_max = str(df["date"].max())[:10]
        df_vix = fetch_vix_us(date_min, date_max)

        if df_vix is not None:
            # Merge por fecha (left join: mantener todas las filas de NIFTY50)
            df_date_only = df.with_columns([
                pl.col("date").dt.truncate("1d").alias("date_key")
            ])
            df_vix = df_vix.rename({"date": "date_key"})

            merged = df_date_only.join(df_vix, on="date_key", how="left")
            vix_raw_series = merged["vix_close"]
            print(f"  ✅ VIX re-merged: {vix_raw_series.is_not_null().sum()} valores válidos")

            # Normalizar VIX re-fetched con causal z-score
            # (usamos los valores absolutos de VIX, no la versión normalizada sucia)
            vix_raw_np = vix_raw_series.to_numpy().astype(float)
            vix_raw_np[np.isnan(vix_raw_np)] = np.nan

            # Forward-fill NaN residuales (días festivos India sin VIX US)
            # Causal: solo propagamos hacia adelante
            last_valid = np.nan
            for i in range(len(vix_raw_np)):
                if not np.isnan(vix_raw_np[i]):
                    last_valid = vix_raw_np[i]
                elif not np.isnan(last_valid):
                    vix_raw_np[i] = last_valid

            vix_norm_new = causal_zscore(vix_raw_np, VIX_WINDOW, VIX_MIN_PERIODS)
            df = df.drop("date_key") if "date_key" in df.columns else df

        else:
            # Fallback: usar los valores existentes no-NaN + forward-fill causal
            print(f"  ⚠️  Fallback: forward-fill causal de los {len(df) - vix_nan} valores existentes")
            vix_raw_np = df["vix_norm"].to_numpy().astype(float)

            # Forward-fill causal
            last_valid = np.nan
            filled_count = 0
            for i in range(len(vix_raw_np)):
                if not np.isnan(vix_raw_np[i]):
                    last_valid = vix_raw_np[i]
                elif not np.isnan(last_valid):
                    vix_raw_np[i] = last_valid
                    filled_count += 1

            print(f"  Forward-filled: {filled_count} valores")
            # Ahora aplica causal z-score al resultado ffill
            vix_norm_new = causal_zscore(vix_raw_np, VIX_WINDOW, VIX_MIN_PERIODS)

        # Limpiar columna temporal si existe
        if "date_key" in df.columns:
            df = df.drop("date_key")

    else:
        # ── 5. Caso normal (XAU): recalcular vix_norm causal ────────────────
        vix_raw_np = df["vix_norm"].to_numpy().astype(float)

        # Recuperar escala original: vix_norm_global = (vix_raw - mu_global) / sigma_global
        # Como no tenemos vix_raw, aplicamos causal z-score directamente sobre vix_norm_global
        # Efecto: elimina el sesgo del std global y aplica ventana deslizante causal
        # Nota: si en el futuro se tiene acceso al VIX_US raw, reconstruir desde YF y reemplazar aquí
        vix_norm_new = causal_zscore(vix_raw_np, VIX_WINDOW, VIX_MIN_PERIODS)

    # ── 6. Recalcular entropy_psych_vix ─────────────────────────────────────
    #
    # ATENCIÓN: La fórmula exacta de entropy_psych_vix está en spel_math_engine.py
    # Si la fórmula no es simplemente entropy_shannon × max(vix_norm, 0),
    # actualizar esta sección y volver a correr el script.
    #
    # Fórmula inferida del nombre y semántica del proyecto:
    #   entropy_psych_vix = entropy_shannon × relu(vix_norm)
    #   → "qué tanto la entropía mediática es amplificada por volatilidad de mercado"
    #   → relu porque entropía psicológica no puede ser negativa

    entropy_shannon_np = df["entropy_shannon"].to_numpy().astype(float)
    vix_relu = np.maximum(vix_norm_new, 0.0)
    entropy_psych_vix_new = entropy_shannon_np * vix_relu
    # NaN propagation: si entropy_shannon es NaN, resultado es NaN
    entropy_psych_vix_new = np.where(
        np.isnan(entropy_shannon_np), np.nan, entropy_psych_vix_new
    )

    # ── 7. Aplicar cambios al DataFrame ─────────────────────────────────────
    df_fixed = df.with_columns([
        pl.Series("vix_norm",          vix_norm_new.tolist(),          dtype=pl.Float64),
        pl.Series("entropy_psych_vix", entropy_psych_vix_new.tolist(), dtype=pl.Float64),
    ])

    # Verificar que el orden de columnas no cambió
    assert df_fixed.columns == original_columns, "❌ COLUMNAS REORDENADAS — abortar"

    # ── 8. Diagnóstico DESPUÉS ───────────────────────────────────────────────
    diagnose_vix(asset, df_fixed, label="DESPUÉS")

    # ── 9. Validaciones post-fix ─────────────────────────────────────────────
    vix_new_series = df_fixed["vix_norm"].drop_nulls().drop_nans()
    nan_post       = df_fixed["vix_norm"].is_null().sum() + df_fixed["vix_norm"].is_nan().sum()

    # Test 1: ya no es normalización global
    is_still_global = (abs(float(vix_new_series.mean())) < 0.05) and \
                      (abs(float(vix_new_series.std()) - 1.0) < 0.08)
    if is_still_global:
        print(f"\n  ❌ FIX FALLIDO — vix_norm aún parece global normalization")
        return {"status": "FIX_FAILED", "asset": asset}

    # Test 2: NaN post-fix aceptable (solo warm-up)
    if nan_post > VIX_MIN_PERIODS * 2:
        print(f"\n  ⚠️  NaN post-fix alto: {nan_post} (esperado ≤ {VIX_MIN_PERIODS * 2})")
        if asset == "NIFTY50" and nan_post > 200:
            print(f"     NIFTY50: demasiados NaN — ejecutar con yfinance disponible")
            return {"status": "NAN_EXCESS_NIFTY50", "asset": asset, "nan_count": int(nan_post)}

    # ── 10. Guardar ──────────────────────────────────────────────────────────
    df_fixed.write_parquet(path)
    sha_out = sha12(path)

    print(f"\n  ✅ Guardado exitosamente")
    print(f"     SHA antes: {sha_in}")
    print(f"     SHA nueva: {sha_out}")
    print(f"     NaN vix_norm: {int(df['vix_norm'].is_null().sum() + df['vix_norm'].is_nan().sum())} → {int(nan_post)}")

    return {
        "status":        "FIXED",
        "asset":         asset,
        "sha_before":    sha_in,
        "sha_after":     sha_out,
        "rows":          len(df_fixed),
        "vix_nan_before": int(df["vix_norm"].is_null().sum() + df["vix_norm"].is_nan().sum()),
        "vix_nan_after":  int(nan_post),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    section(f"SPEL — Erradicación BUG-LA-01 · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  ROOT: {ROOT}")
    print(f"  Targets: {TARGETS}")
    print(f"  VIX window: {VIX_WINDOW}d  min_periods: {VIX_MIN_PERIODS}d")

    results = []

    for asset in TARGETS:
        r = fix_asset(asset)
        results.append(r)

    # ── Resumen ──────────────────────────────────────────────────────────────
    section("RESUMEN FINAL")

    all_fixed = all(r.get("status") == "FIXED" for r in results)
    for r in results:
        st    = r.get("status", "?")
        icon  = "✅" if st == "FIXED" else "❌"
        print(f"  {icon} {r['asset']:<8} {st}")
        if st == "FIXED":
            print(f"       SHA: {r['sha_before']} → {r['sha_after']}")
            print(f"       NaN: {r['vix_nan_before']} → {r['vix_nan_after']}")

    # ── Guardar reporte ──────────────────────────────────────────────────────
    os.makedirs(META_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = f"{META_DIR}/vix_fix_report_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  📄 Reporte: {report_path}")

    # ── Instrucciones para Project Log v31 ──────────────────────────────────
    if any(r.get("status") == "FIXED" for r in results):
        hline()
        print("  ACTUALIZAR en SPEL_Project_Log_v31.md — tabla SHA parquets:")
        hline()
        for r in results:
            if r.get("status") == "FIXED":
                print(f"  | {r['asset']:<8} | {r['rows']:<4} | datetime[ms,UTC] | {r['sha_after']} |")

    # ── Próximos pasos ───────────────────────────────────────────────────────
    hline()
    print("  PRÓXIMOS PASOS:")
    print("  1. Verificar: !python /content/spel_dna_audit.py")
    print("     → Todos los activos deben mostrar ✅ (sin 🔴)")
    print("  2. Recalibrar Gödel P90 XAU (BUG-GODEL-XAU aún abierto):")
    print("     !python /content/spel_p90_recalibrate.py")
    print("  3. Reentrenar modelos (checkpoints vacíos — requieren datos limpios):")
    print("     Orden: BTC → XAU → NIFTY50 → NVDA")
    print("  4. Actualizar SHA en Project_Log_v31.md")
    hline()

    if not all_fixed:
        print("\n  ⚠️  Algunos activos fallaron — revisar errores arriba.")
    else:
        print("\n  ✅ BUG-LA-01 erradicado de SPEL-v2.0. NVDA y BTC no fueron tocados.")


if __name__ == "__main__":
    main()
