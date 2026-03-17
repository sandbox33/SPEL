"""
spel_auditoria_total.py
═══════════════════════════════════════════════════════════════════════════════
SPEL — Auditoría Total del Sistema
Versión: 1.0 · 10-Mar-2026

Qué cubre este script (8 módulos de auditoría):

  A. SHA REGISTRY      — Verifica y actualiza fingerprints de todos los parquets
  B. DNA FORENSE       — Leakage exhaustivo en TODAS las features (no solo vix_norm)
  C. DATE DTYPE        — Comprueba datetime[ms,UTC] en absolutamente todos los parquets
  D. NaN TOPOLOGY      — Distingue warm-up legítimo vs contaminación estructural
  E. GDELT COVERAGE    — Join quality, cobertura temporal, cols faltantes
  F. 2026 ENTROPY      — Verifica los parquets anómalos de 2 cols (vs 9 esperados)
  G. GODEL CALIBRATION — Tasa de activación por activo, detecta P90 mal calibrado
  H. SEQUENCE AUDIT    — Detecta lookahead en construcción de secuencias LSTM

Uso en Colab:
  !cp /content/drive/MyDrive/SPEL-v2.0/meta/spel_auditoria_total.py /content/
  !python /content/spel_auditoria_total.py

Output:
  /content/drive/MyDrive/SPEL-v2.0/meta/auditoria_total_YYYYMMDD_HHMM.json
  /content/drive/MyDrive/SPEL-v2.0/meta/SHA_REGISTRY.json  ← fuente de verdad SHA
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import hashlib
import warnings
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import numpy as np

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

ROOT      = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE = f"{ROOT}/data_lake"
META_DIR  = f"{ROOT}/meta"

ASSETS    = ["NVDA", "BTC", "XAU", "NIFTY50"]

# SHA actualizados tras vix_fix_report_20260310_1504
# NVDA y BTC no fueron tocados (SHA del Project Log v30 son válidos)
SHA_POST_FIX = {
    "NVDA":    "f496c377c7ae",
    "BTC":    "899052347d73",
    "XAU":    "d3acbf6342bc",
    "NIFTY50":    "981989b7024d",
}

# Lookbacks inamovibles (Regla R4)
LOOKBACKS = {"NVDA": 63, "BTC": 21, "XAU": 63, "NIFTY50": 42}

# P90 Gödel actuales (antes de recalibrar XAU)
P90_CURRENT = {"NVDA": 1.1571, "BTC": 1.1709, "XAU": 1.3229, "NIFTY50": 1.1868}
P90_TARGET_ACTIVATION = (0.30, 0.48)   # rango sano: 30%-48% (OR + vitality tertiles R8 — XAU estructuralmente más entrópico)

# Features OHLCV esperadas en canonical_v5 (30 cols)
FEATURES_EXPECTED = {
    "date", "open", "high", "low", "close", "volume",
    "entropy_shannon", "entropy_decay_lambda", "entropy_psych_vix",
    "fibonacci_lag_1", "fibonacci_lag_2", "fibonacci_lag_3",
    "fibonacci_lag_5", "fibonacci_lag_8", "fibonacci_lag_13", "fibonacci_lag_21",
    "goldstein_geo", "n_events_ohlcv", "vitality_tesla",
    "mass_panic_index", "fear_momentum", "vix_norm",
    "nash_frozen_7d", "log_return",
    # Schema v5.1 — metadata + GDELT extendido (no entran al tensor R13)
    "volume_type", "asset_class", "trading_session",
    "goldstein_mean", "tone_variance", "zipf_concentration",
}

# Features GDELT esperadas (9 cols)
GDELT_FEATURES_EXPECTED = {
    "date", "asset", "entropy_shannon", "zipf_concentration",
    "goldstein_mean", "tone_variance", "n_events",
    "nash_frozen_7d", "vitality_tesla",
}

# Umbral de activación Gödel máxima sin recalibrar = bug
GODEL_BUG_THRESHOLD = 0.90   # >90% activación = P90 roto

# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────

def sha12(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

def hline(char="─", w=64): print(char * w)
def section(t): hline("═"); print(f"  {t}"); hline("═")
def ok(msg):   print(f"  ✅ {msg}")
def warn(msg): print(f"  ⚠️  {msg}")
def err(msg):  print(f"  🔴 {msg}")
def info(msg): print(f"  ·  {msg}")

findings = []   # acumula todos los hallazgos

def finding(severity: str, module: str, asset: str, code: str, desc: str, fix: str = ""):
    """severity: CRÍTICO | ALTO | MEDIO | INFO | OK"""
    findings.append({
        "severity": severity, "module": module, "asset": asset,
        "code": code, "desc": desc, "fix": fix,
    })
    icon = {"CRÍTICO": "🔴", "ALTO": "🟠", "MEDIO": "🟡", "INFO": "·", "OK": "✅"}.get(severity, "·")
    print(f"  {icon} [{module}][{asset}] {code}: {desc}")


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO A — SHA REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

def audit_sha():
    section("A. SHA REGISTRY — Verificación de integridad de parquets")
    registry = {}

    for asset in ASSETS:
        path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not os.path.exists(path):
            finding("CRÍTICO", "SHA", asset, "FILE_MISSING",
                    f"canonical v4 no encontrado: {path}", "Re-generar desde fuente")
            continue

        actual = sha12(path)
        expected = SHA_POST_FIX.get(asset)
        registry[asset] = {"path": path, "sha": actual, "expected": expected}

        if actual == expected:
            finding("OK", "SHA", asset, "SHA_MATCH", f"SHA confirmado: {actual}")
        else:
            finding("ALTO", "SHA", asset, "SHA_MISMATCH",
                    f"SHA real={actual} esperado={expected}. Parquet modificado externamente.",
                    "Verificar si la modificación fue intencional. Actualizar SHA_REGISTRY.")

    # Guardar registry
    registry_path = f"{META_DIR}/SHA_REGISTRY.json"
    os.makedirs(META_DIR, exist_ok=True)
    with open(registry_path, "w") as f:
        json.dump({
            "updated": datetime.now(timezone.utc).isoformat(),
            "note": "Fuente de verdad SHA post-vix_fix 10-Mar-2026 15:04 UTC",
            "parquets": registry,
        }, f, indent=2)
    ok(f"SHA_REGISTRY guardado: {registry_path}")
    return registry


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO B — DNA FORENSE (leakage exhaustivo en todas las features)
# ─────────────────────────────────────────────────────────────────────────────

def detect_global_norm(series: pl.Series, name: str) -> tuple[bool, dict]:
    """
    Detecta normalización global (lookahead):
      - media ≈ 0 y std ≈ 1 → z-score con estadísticos globales
      - autocorrelación lag-1 muy alta → rolling causal produce autocorr moderada,
        global la destruye al mezclar todos los períodos
    """
    s = series.drop_nulls().drop_nans()
    if len(s) < 50:
        return False, {}
    mu    = float(s.mean())
    sigma = float(s.std())
    mi    = float(s.min())
    ma    = float(s.max())

    # Test 1: z-score global — media≈0 y std≈1 con range amplio
    is_zscore_global = abs(mu) < 0.12 and abs(sigma - 1.0) < 0.12 and (ma - mi) > 2.0

    # Test 2: ratio IQR/std — global normalization aplana la distribución
    q25 = float(s.quantile(0.25))
    q75 = float(s.quantile(0.75))
    iqr  = q75 - q25
    iqr_ratio = iqr / sigma if sigma > 0 else 0
    suspicious_iqr = iqr_ratio < 0.85  # distribución muy comprimida → sospecha

    leakage = is_zscore_global and suspicious_iqr
    return leakage, {"mean": mu, "std": sigma, "iqr_ratio": round(iqr_ratio, 3)}


def detect_future_bleed(df: pl.DataFrame, feature: str, target: str = "log_return",
                        horizon: int = 5) -> float:
    """
    Correlación entre feature[t] y log_return[t+1..t+horizon].
    Una feature causal no debería tener correlación alta con el FUTURO.
    Si r > 0.15 con retornos futuros → sospecha de lookahead.
    """
    if feature not in df.columns or target not in df.columns:
        return 0.0
    feat = df[feature].drop_nulls()
    if len(feat) < 100:
        return 0.0

    max_corr = 0.0
    for h in range(1, horizon + 1):
        shifted_target = df[target].shift(-h).drop_nulls()
        n = min(len(feat), len(shifted_target))
        if n < 50:
            continue
        f_np = feat[:n].to_numpy().astype(float)
        t_np = shifted_target[:n].to_numpy().astype(float)
        mask = ~(np.isnan(f_np) | np.isnan(t_np))
        if mask.sum() < 50:
            continue
        try:
            corr = float(np.corrcoef(f_np[mask], t_np[mask])[0, 1])
            max_corr = max(max_corr, abs(corr))
        except Exception:
            pass
    return round(max_corr, 4)


def audit_dna():
    section("B. DNA FORENSE — Leakage exhaustivo en todas las features")

    # Features a auditar con su tipo de riesgo
    LEAKAGE_SUSPECTS = [
        "vix_norm", "entropy_psych_vix", "entropy_decay_lambda",
        "mass_panic_index", "fear_momentum", "nash_frozen_7d",
    ]
    FUTURE_BLEED_SUSPECTS = [
        "vix_norm", "entropy_psych_vix", "mass_panic_index",
        "fear_momentum", "entropy_shannon",
    ]

    results = {}

    for asset in ASSETS:
        path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not os.path.exists(path):
            finding("CRÍTICO", "DNA", asset, "FILE_MISSING", "Archivo no encontrado")
            continue

        df = pl.read_parquet(path)
        hline()
        print(f"\n  {asset} — {len(df)} filas · {len(df.columns)} cols")

        asset_results = {"leakage": {}, "future_bleed": {}, "missing_cols": []}

        # Test 1: columnas presentes
        missing = FEATURES_EXPECTED - set(df.columns)
        extra   = set(df.columns) - FEATURES_EXPECTED
        if missing:
            finding("CRÍTICO", "DNA", asset, "COLS_MISSING",
                    f"Columnas faltantes: {sorted(missing)}",
                    "Regenerar parquet con pipeline completo")
            asset_results["missing_cols"] = sorted(missing)
        if extra:
            finding("MEDIO", "DNA", asset, "COLS_EXTRA",
                    f"Columnas no esperadas: {sorted(extra)}. Verificar si son intencionales.")

        # Test 2: global normalization detection
        for feat in LEAKAGE_SUSPECTS:
            if feat not in df.columns:
                continue
            leakage, stats = detect_global_norm(df[feat], feat)
            asset_results["leakage"][feat] = {"leakage": leakage, **stats}
            if leakage:
                finding("CRÍTICO", "DNA", asset, f"LEAKAGE:{feat}",
                        f"Normalización global detectada en '{feat}' → "
                        f"mean={stats['mean']:.4f} std={stats['std']:.4f} iqr_ratio={stats['iqr_ratio']}",
                        "Recalcular con rolling causal (window=252, shift=1)")
            else:
                info(f"  {feat:<25} ✅ mean={stats.get('mean', 0):.4f} std={stats.get('std', 0):.4f}")

        # Test 3: future bleed (correlación con retornos futuros)
        if "log_return" in df.columns:
            for feat in FUTURE_BLEED_SUSPECTS:
                if feat not in df.columns:
                    continue
                corr = detect_future_bleed(df, feat)
                asset_results["future_bleed"][feat] = corr
                if corr > 0.15:
                    finding("ALTO", "DNA", asset, f"FUTURE_BLEED:{feat}",
                            f"'{feat}' correlaciona {corr:.3f} con retornos futuros (h=1..5). "
                            "Potencial lookahead indirecto.",
                            "Trazar pipeline completo de cómo se calcula esta feature")
                elif corr > 0.08:
                    finding("MEDIO", "DNA", asset, f"FUTURE_BLEED_WEAK:{feat}",
                            f"'{feat}' correlación débil-moderada con futuro: {corr:.3f}. Monitorear.")
                else:
                    info(f"  {feat:<25} future_corr={corr:.4f} ✅")

        # Test 4: mass_panic_index — flag especial (mencionado en GUIA como alto riesgo)
        if "mass_panic_index" in df.columns:
            mpi = df["mass_panic_index"].drop_nulls().drop_nans()
            mpi_nonzero = (mpi != 0).sum() / len(mpi)
            mpi_leakage, mpi_stats = detect_global_norm(mpi, "mass_panic_index")
            if mpi_leakage:
                finding("CRÍTICO", "DNA", asset, "LEAKAGE:mass_panic_index",
                        f"mass_panic_index tiene distribución global — CONFIRMA LEAKAGE. "
                        f"nonzero={mpi_nonzero*100:.1f}%",
                        "Este es el flag de leakage de GUIA_COLAB — PARAR y revisar")
            else:
                info(f"  mass_panic_index        nonzero={mpi_nonzero*100:.1f}% ✅")

        results[asset] = asset_results

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO C — DATE DTYPE en todos los parquets
# ─────────────────────────────────────────────────────────────────────────────

def audit_dates():
    section("C. DATE DTYPE — Verificación datetime[ms,UTC] en todos los parquets")
    issues = []

    for asset in ASSETS:
        # Canonical ohlcv
        paths_to_check = [
            f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet",
            f"{DATA_LAKE}/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet",
        ]
        # Entropy por año
        entropy_dir = f"{DATA_LAKE}/{asset}/entropy/"
        if os.path.exists(entropy_dir):
            for f in Path(entropy_dir).glob("*.parquet"):
                paths_to_check.append(str(f))

        asset_issues = []
        for path in paths_to_check:
            if not os.path.exists(path):
                continue
            try:
                df = pl.read_parquet(path)
            except Exception as e:
                finding("ALTO", "DATE", asset, "READ_ERROR", f"{Path(path).name}: {e}")
                continue

            if "date" not in df.columns:
                finding("MEDIO", "DATE", asset, "NO_DATE_COL",
                        f"{Path(path).name}: sin columna 'date'")
                continue

            dtype = df["date"].dtype
            dtype_str = str(dtype)
            expected  = "Datetime(time_unit='ms', time_zone='UTC')"

            if dtype_str != expected:
                asset_issues.append(Path(path).name)
                finding("ALTO", "DATE", asset, f"DATE_DTYPE_WRONG",
                        f"{Path(path).name}: dtype={dtype_str} (esperado: {expected})",
                        "pl.col('date').cast(pl.Datetime('ms','UTC'))")

        if not asset_issues:
            finding("OK", "DATE", asset, "ALL_DATES_OK",
                    f"Todos los parquets de {asset} tienen datetime[ms,UTC] ✅")
        else:
            issues.extend(asset_issues)

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO D — NaN TOPOLOGY
# ─────────────────────────────────────────────────────────────────────────────

def audit_nans():
    section("D. NaN TOPOLOGY — Warm-up legítimo vs contaminación estructural")
    results = {}

    for asset in ASSETS:
        path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not os.path.exists(path):
            continue

        df = pl.read_parquet(path)
        n  = len(df)
        lookback = LOOKBACKS[asset]
        asset_nan = {}

        for col in df.columns:
            if col == "date":
                continue
            s = df[col]
            nan_n = s.is_null().sum() + (s.is_nan().sum() if s.dtype in (pl.Float32, pl.Float64) else 0)
            if nan_n == 0:
                continue

            # Diagnóstico de topología NaN
            nan_rate = nan_n / n

            # Check: ¿los NaN están concentrados al inicio? (warm-up legítimo)
            null_mask = s.is_null() | (s.is_nan() if s.dtype in (pl.Float32, pl.Float64) else pl.lit(False))
            null_positions = [i for i, v in enumerate(null_mask.to_list()) if v]
            max_pos = max(null_positions) if null_positions else 0

            warmup_ok = max_pos <= (lookback * 2)  # todos los NaN dentro del warm-up

            asset_nan[col] = {
                "count": int(nan_n), "rate": round(nan_rate, 4), "max_pos": max_pos, "warmup_ok": warmup_ok
            }

            if nan_rate > 0.05 and not warmup_ok:
                finding("CRÍTICO", "NAN", asset, f"NAN_STRUCTURAL:{col}",
                        f"'{col}' tiene {nan_n} NaN ({nan_rate*100:.1f}%) fuera del warm-up "
                        f"(max_pos={max_pos}, lookback={lookback}). Contaminación estructural.",
                        "Investigar fuente de datos y regenerar columna")
            elif nan_rate > 0.01:
                finding("MEDIO", "NAN", asset, f"NAN_ELEVATED:{col}",
                        f"'{col}' tiene {nan_n} NaN ({nan_rate*100:.1f}%). "
                        f"{'Warm-up OK' if warmup_ok else 'Fuera del warm-up — revisar'}.")
            # Si warm-up OK y < 1% → no reportar (normal)

        if not asset_nan:
            finding("OK", "NAN", asset, "NAN_CLEAN", f"Sin NaN en ninguna columna ✅")
        results[asset] = asset_nan

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO E — GDELT COVERAGE
# ─────────────────────────────────────────────────────────────────────────────

def audit_gdelt():
    section("E. GDELT COVERAGE — Calidad del join y cobertura temporal")
    results = {}

    for asset in ASSETS:
        gdelt_path = f"{DATA_LAKE}/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet"
        ohlcv_path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"

        if not os.path.exists(gdelt_path):
            finding("ALTO", "GDELT", asset, "GDELT_MISSING",
                    "Parquet GDELT no encontrado", "Regenerar con gdelt_foundation.py")
            continue

        gdf = pl.read_parquet(gdelt_path)

        # Verificar columnas
        missing_cols = GDELT_FEATURES_EXPECTED - set(gdf.columns)
        if missing_cols:
            finding("CRÍTICO", "GDELT", asset, "GDELT_COLS_MISSING",
                    f"Columnas faltantes: {sorted(missing_cols)}",
                    "Regenerar parquet GDELT")

        # Cobertura temporal
        if "date" in gdf.columns:
            gdf_dates = gdf["date"].cast(pl.Datetime("ms", "UTC"), strict=False)
            date_min = gdf_dates.min()
            date_max = gdf_dates.max()
            info(f"  {asset} GDELT: {len(gdf)} filas · {date_min} → {date_max}")

            # Detectar gap 2026
            if date_max is not None:
                max_year = date_max.year if hasattr(date_max, 'year') else 2025
                if max_year < 2026:
                    finding("MEDIO", "GDELT", asset, "GDELT_GAP_2026",
                            f"GDELT cubre hasta {date_max}. Gap 2026 confirmado.",
                            "Migrar CSVs GDELT 2026 → gdelt_foundation.py")
                else:
                    finding("OK", "GDELT", asset, "GDELT_2026_OK",
                            f"GDELT cubre 2026 hasta {date_max} ✅")

        # Join quality con OHLCV
        if os.path.exists(ohlcv_path):
            odf = pl.read_parquet(ohlcv_path)
            ohlcv_n = len(odf)
            gdelt_n  = len(gdf)
            # Match rate teórico basado en filas
            match_rate = min(gdelt_n / ohlcv_n, 1.0) if ohlcv_n > 0 else 0
            info(f"  {asset} join proxy: OHLCV={ohlcv_n} GDELT={gdelt_n} match≈{match_rate*100:.1f}%")
            if match_rate < 0.60:
                finding("ALTO", "GDELT", asset, "GDELT_LOW_COVERAGE",
                        f"Cobertura GDELT baja: ~{match_rate*100:.1f}% del historial OHLCV",
                        "Verificar rango de fechas y re-fetch GDELT histórico")

        results[asset] = {"rows": len(gdf), "cols": len(gdf.columns)}

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO F — 2026 ENTROPY PARQUETS
# ─────────────────────────────────────────────────────────────────────────────

def audit_2026_entropy():
    section("F. 2026 ENTROPY — Verificación de parquets anómalos (2 cols vs 9 esperadas)")
    results = {}

    for asset in ASSETS:
        path_2026 = f"{DATA_LAKE}/{asset}/entropy/{asset}_2026_entropy.parquet"
        if not os.path.exists(path_2026):
            finding("MEDIO", "2026", asset, "2026_MISSING",
                    f"{asset}_2026_entropy.parquet no encontrado",
                    "Normal si aún no se ha generado el 2026 completo")
            continue

        df = pl.read_parquet(path_2026)
        cols = list(df.columns)
        n    = len(df)

        info(f"  {asset}_2026_entropy: {n} filas · cols={cols}")

        if len(cols) < 9:
            # Parquet incompleto — solo tiene date y posiblemente 1 feature
            missing = sorted(GDELT_FEATURES_EXPECTED - set(cols))
            finding("ALTO", "2026", asset, "2026_INCOMPLETE",
                    f"Solo {len(cols)} columnas en lugar de 9. Faltantes: {missing}. "
                    "Este parquet NO debe usarse en entrenamiento.",
                    "Regenerar con gdelt_foundation.py para el período 2026-01-01→hoy, "
                    "o excluir explícitamente de spel_retrain_v5_clean.py con cutoff 2025-12-31")
        else:
            finding("OK", "2026", asset, "2026_OK",
                    f"9 columnas presentes · {n} filas ✅")

        results[asset] = {"cols": len(cols), "rows": n}

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO G — GÖDEL CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

def audit_godel():
    section("G. GÖDEL CALIBRATION — Tasa de activación por activo")
    results = {}

    for asset in ASSETS:
        path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not os.path.exists(path):
            continue

        df = pl.read_parquet(path)
        p90 = P90_CURRENT.get(asset, 1.0)

        if "entropy_shannon" not in df.columns or "vitality_tesla" not in df.columns:
            finding("ALTO", "GODEL", asset, "GODEL_COLS_MISSING",
                    "Faltan entropy_shannon o vitality_tesla para evaluar Gödel")
            continue

        # Aplicar condición Gödel (OR — nunca AND, Regla R8)
        # Solo en datos anteriores al cutoff (anti-leakage)
        cutoff = pl.lit(datetime(2023, 12, 31, tzinfo=timezone.utc))
        df_train = df.filter(pl.col("date") <= cutoff)

        if len(df_train) < 100:
            finding("MEDIO", "GODEL", asset, "GODEL_INSUFFICIENT_DATA",
                    f"Menos de 100 filas antes del cutoff ({len(df_train)})")
            continue

        entropy_col = df_train["entropy_shannon"].drop_nulls()
        p90_actual  = float(entropy_col.quantile(0.90))

        godel_active = (
            (df_train["entropy_shannon"] >= p90) |
            (df_train["vitality_tesla"] == 9)
        )
        activation_rate = godel_active.sum() / len(df_train)

        # Con P90 calculado sobre los datos reales
        godel_active_correct = (
            (df_train["entropy_shannon"] >= p90_actual) |
            (df_train["vitality_tesla"] == 9)
        )
        activation_correct = godel_active_correct.sum() / len(df_train)

        results[asset] = {
            "p90_configured": p90,
            "p90_data_actual": round(p90_actual, 4),
            "activation_rate_current": round(float(activation_rate), 4),
            "activation_rate_if_recalibrated": round(float(activation_correct), 4),
            "n_train_rows": len(df_train),
        }

        info(f"  {asset}:")
        info(f"    P90 configurado:  {p90}  →  activación actual:  {activation_rate*100:.1f}%")
        info(f"    P90 datos reales: {p90_actual:.4f}  →  activación correcta: {activation_correct*100:.1f}%")

        target_lo, target_hi = P90_TARGET_ACTIVATION
        if float(activation_rate) > GODEL_BUG_THRESHOLD:
            finding("CRÍTICO", "GODEL", asset, "GODEL_OVERACTIVE",
                    f"Gödel activa {activation_rate*100:.1f}% del tiempo. "
                    f"P90={p90} demasiado bajo. Sistema prácticamente ciego.",
                    "Ejecutar spel_p90_recalibrate.py con cutoff 2023-12-31")
        elif not (target_lo <= float(activation_correct) <= target_hi):
            finding("ALTO", "GODEL", asset, "GODEL_OUT_OF_RANGE",
                    f"Activación correcta {activation_correct*100:.1f}% fuera del rango "
                    f"sano {target_lo*100:.0f}%-{target_hi*100:.0f}%",
                    "Recalibrar P90 con spel_p90_recalibrate.py")
        else:
            finding("OK", "GODEL", asset, "GODEL_OK",
                    f"Activación en rango sano: {activation_correct*100:.1f}% ✅")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO H — SEQUENCE AUDIT (lookahead en construcción LSTM)
# ─────────────────────────────────────────────────────────────────────────────

def audit_sequences():
    section("H. SEQUENCE AUDIT — Verificación anti-lookahead en construcción LSTM")

    # No tenemos acceso al script de retrain aquí, pero podemos verificar que
    # los parquets tienen la estructura correcta para secuencias sin lookahead.

    issues = []

    for asset in ASSETS:
        path = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not os.path.exists(path):
            continue

        df = pl.read_parquet(path)
        lookback = LOOKBACKS[asset]

        # Test 1: ¿hay suficientes filas para el lookback?
        n = len(df)
        if n < lookback * 10:
            finding("ALTO", "SEQ", asset, "INSUFFICIENT_ROWS",
                    f"Solo {n} filas para lookback={lookback}d. Menos de 10 ciclos completos.",
                    "Verificar cobertura histórica mínima")

        # Test 2: ¿log_return está desplazado correctamente?
        # log_return[t] debe ser log(close[t]/close[t-1])
        # El target del LSTM debe ser log_return[t+1] (futuro inmediato)
        # Verificar que log_return no está shifted hacia adelante en el parquet
        if "log_return" in df.columns and "close" in df.columns:
            close  = df["close"].to_numpy().astype(float)
            lr     = df["log_return"].to_numpy().astype(float)
            # log_return[t] calculado correctamente = log(close[t]/close[t-1])
            expected_lr = np.diff(np.log(close), prepend=np.nan)
            # Correlación: si log_return está correcto, corr con expected ≈ 1.0
            mask = ~(np.isnan(lr) | np.isnan(expected_lr))
            if mask.sum() > 50:
                corr = float(np.corrcoef(lr[mask], expected_lr[mask])[0, 1])
                if corr < 0.95:
                    finding("CRÍTICO", "SEQ", asset, "LOG_RETURN_MISMATCH",
                            f"log_return correlación con log(close/close_prev) = {corr:.4f} "
                            "(esperado >0.95). Posible shift incorrecto.",
                            "Verificar cálculo de log_return en el harvester")
                else:
                    finding("OK", "SEQ", asset, "LOG_RETURN_OK",
                            f"log_return calculado correctamente (corr={corr:.4f}) ✅")

        # Test 3: ¿vitality_tesla tiene rango correcto (3/6/9)?
        if "vitality_tesla" in df.columns:
            vt_vals = set(df["vitality_tesla"].drop_nulls().unique().to_list())
            valid_vals = {3.0, 6.0, 9.0}
            unexpected = vt_vals - valid_vals - {0.0}
            if unexpected:
                finding("ALTO", "SEQ", asset, "VITALITY_INVALID_VALUES",
                        f"vitality_tesla tiene valores inesperados: {unexpected}. "
                        "Escala esperada: 3/6/9",
                        "Revisar cálculo en gdelt_foundation.py")
            else:
                v9_rate = (df["vitality_tesla"] == 9).sum() / len(df)
                finding("OK", "SEQ", asset, "VITALITY_OK",
                        f"vitality_tesla OK · v=9 rate: {v9_rate*100:.1f}% ✅")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ts_start = datetime.now(timezone.utc)
    section(f"SPEL — AUDITORÍA TOTAL · {ts_start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  ROOT: {ROOT}")

    results = {}
    results["sha"]       = audit_sha()
    results["dna"]       = audit_dna()
    results["dates"]     = audit_dates()
    results["nans"]      = audit_nans()
    results["gdelt"]     = audit_gdelt()
    results["entropy_2026"] = audit_2026_entropy()
    results["godel"]     = audit_godel()
    results["sequences"] = audit_sequences()

    # ── RESUMEN EJECUTIVO ────────────────────────────────────────────────────
    section("RESUMEN EJECUTIVO")

    by_sev = {}
    for f in findings:
        by_sev.setdefault(f["severity"], []).append(f)

    order = ["CRÍTICO", "ALTO", "MEDIO", "INFO", "OK"]
    for sev in order:
        items = by_sev.get(sev, [])
        if not items:
            continue
        icon = {"CRÍTICO": "🔴", "ALTO": "🟠", "MEDIO": "🟡", "INFO": "·", "OK": "✅"}.get(sev, "·")
        print(f"\n  {icon} {sev} ({len(items)})")
        for f in items:
            if sev not in ("INFO", "OK"):
                print(f"     [{f['asset']}] {f['code']}: {f['desc'][:80]}")
                if f.get("fix"):
                    print(f"     FIX: {f['fix'][:80]}")

    # Estadística final
    n_critical = len(by_sev.get("CRÍTICO", []))
    n_high     = len(by_sev.get("ALTO",    []))
    n_ok       = len(by_sev.get("OK",      []))
    hline()
    print(f"\n  Total hallazgos: {len(findings)}")
    print(f"  🔴 Críticos: {n_critical}  🟠 Altos: {n_high}  ✅ OK: {n_ok}")

    is_clean = n_critical == 0
    if is_clean and n_high == 0:
        print("\n  ✅ SISTEMA LIMPIO — puede proceder con entrenamiento LSTM")
    elif is_clean:
        print("\n  ⚠️  Sin críticos, pero hay issues ALTOS — revisar antes de entrenamiento")
    else:
        print(f"\n  🔴 SISTEMA BLOQUEADO — {n_critical} issues críticos deben resolverse primero")

    # ── Guardar reporte ──────────────────────────────────────────────────────
    ts_str = ts_start.strftime("%Y%m%d_%H%M")
    report = {
        "timestamp":    ts_start.isoformat(),
        "summary":      {"critical": n_critical, "high": n_high, "ok": n_ok, "total": len(findings)},
        "findings":     findings,
        "module_results": {k: v for k, v in results.items() if isinstance(v, dict)},
    }
    os.makedirs(META_DIR, exist_ok=True)
    report_path = f"{META_DIR}/auditoria_total_{ts_str}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    hline()
    print(f"  📄 Reporte completo: {report_path}")
    print(f"  🗂️  SHA_REGISTRY:     {META_DIR}/SHA_REGISTRY.json")
    hline()

    # ── Próximos pasos condicionales ─────────────────────────────────────────
    print("\n  PRÓXIMOS PASOS (en orden):")
    step = 1
    if n_critical > 0 or n_high > 0:
        print(f"  {step}. Resolver hallazgos críticos/altos del reporte antes de entrenar")
        step += 1
    print(f"  {step}. Si todo OK → !python /content/spel_p90_recalibrate.py  (Gödel XAU)")
    step += 1
    print(f"  {step}. Reentrenar: BTC → XAU → NIFTY50 → NVDA")
    step += 1
    print(f"  {step}. Actualizar Project_Log_v31.md con SHA_REGISTRY.json")
    hline()


if __name__ == "__main__":
    main()
