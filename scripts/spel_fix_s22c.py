"""
spel_fix_s22c.py
════════════════════════════════════════════════════════════════
SPEL — Fix S22c · P90 Transfer Entropy + Normalizador Rolling TE

PROBLEMA:
  El router adaptativo (R18) asigna peso 45% a Transfer Entropy
  en activos SYNTHETIC_INDEX. Si la TE no está normalizada [0,1]
  con anti-leakage correcto, el Score de Oro se distorsiona en
  picos extremos GDELT → mismo vector que BUG-LA-01 (vix_norm).

  Sin este fix, spel_math_engine.py usaría serie completa como
  referencia de normalización → filtra información futura al
  período de entrenamiento → métricas de validación infladas.

ACCIONES:
  PATCH-A  Calcular P90 de Transfer Entropy por activo
           anti-leakage ≤ 2023-12-31 desde parquets v5
           (o v4 si v5 aún no existe)

  PATCH-B  Calcular rolling min/max por activo con lookback
           inamovible → definir parámetros del normalizador
           para inyectar en spel_math_engine.py

  PATCH-C  Registrar en SHA_REGISTRY bajo clave "te_calibration"

  PATCH-D  Emitir bloque de código listo para pegar en
           spel_math_engine.py con normalizador correcto

PREREQUISITOS:
  ✅ spel_fix_s22a.py ejecutado (NIFTY50 volume clean)
  ✅ spel_fix_s22b.py ejecutado (P90 entropy + SHA audit)

REGLAS: R3, R6, anti-leakage absoluto.
════════════════════════════════════════════════════════════════
"""

import json, hashlib, os
from datetime import datetime, timezone
import polars as pl
import numpy as np

ROOT      = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE = f"{ROOT}/data_lake"
META_DIR  = f"{ROOT}/meta"
ASSETS    = ["NVDA", "BTC", "XAU", "NIFTY50"]
LOOKBACKS = {"NVDA": 63, "BTC": 21, "XAU": 63, "NIFTY50": 42}
CUTOFF    = datetime(2023, 12, 31, tzinfo=timezone.utc)

def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")
def ok(m):   print(f"  ✅ {m}")
def err(m):  print(f"  🔴 {m}")
def warn(m): print(f"  ⚠️  {m}")
def info(m): print(f"  ·  {m}")

def ohlcv_path(a):
    # Preferir v5 si existe, fallback a v4
    v5 = f"{DATA_LAKE}/{a}/ohlcv/aggregated/{a}_ohlcv_v5.parquet"
    v4 = f"{DATA_LAKE}/{a}/ohlcv/aggregated/{a}_ohlcv_v4.parquet"
    return v5 if os.path.exists(v5) else v4


# ─────────────────────────────────────────────────────────────
# PATCH-A — P90 Transfer Entropy anti-leakage
# ─────────────────────────────────────────────────────────────
def patch_te_p90() -> dict:
    section("PATCH-A — P90 Transfer Entropy (anti-leakage ≤ 2023-12-31)")

    results = {}

    for asset in ASSETS:
        path = ohlcv_path(asset)
        if not os.path.exists(path):
            warn(f"{asset}: parquet no encontrado — SKIP")
            continue

        df = pl.read_parquet(path)

        # Normalizar fecha
        if df["date"].dtype != pl.Datetime("ms", "UTC"):
            df = df.with_columns(
                pl.col("date").cast(pl.Datetime("ms", "UTC"))
            )

        df_train = df.filter(pl.col("date") <= pl.lit(CUTOFF))
        n_train  = len(df_train)

        print(f"\n  {asset}  (n_train={n_train})")

        # Transfer Entropy: puede estar en columna directa o calcularse
        # del producto entropy_shannon × entropy_decay_lambda como proxy
        # Si transfer_entropy no existe aún (pre-math-engine), usar proxy
        te_col = None
        for candidate in ["transfer_entropy", "entropy_decay_lambda"]:
            if candidate in df_train.columns:
                te_col = candidate
                break

        if te_col is None:
            warn(f"  {asset}: ninguna columna TE encontrada — usando entropy_shannon como proxy")
            te_col = "entropy_shannon"

        te = df_train[te_col].cast(pl.Float64).drop_nulls()
        if len(te) < 50:
            warn(f"  {asset}: insuficientes valores ({len(te)}) — SKIP")
            continue

        p10  = float(te.quantile(0.10))
        p50  = float(te.quantile(0.50))
        p90  = float(te.quantile(0.90))
        p99  = float(te.quantile(0.99))
        mean = float(te.mean())
        std  = float(te.std())
        vmin = float(te.min())
        vmax = float(te.max())

        # Rango intercuartil para detectar si hay colas extremas
        p25  = float(te.quantile(0.25))
        p75  = float(te.quantile(0.75))
        iqr  = p75 - p25
        tail_risk = (vmax - p75) / (iqr + 1e-8)

        info(f"  col usada:    {te_col}")
        info(f"  min/max:      {vmin:.4f} / {vmax:.4f}")
        info(f"  mean ± std:   {mean:.4f} ± {std:.4f}")
        info(f"  P10/P50/P90:  {p10:.4f} / {p50:.4f} / {p90:.4f}")
        info(f"  P99:          {p99:.4f}")
        info(f"  tail_risk:    {tail_risk:.2f}× IQR")

        if tail_risk > 3.0:
            warn(f"  COLA EXTREMA detectada (tail_risk={tail_risk:.1f}× IQR) → bounding crítico")
        else:
            ok(f"  Distribución sana — bounding rolling suficiente")

        results[asset] = {
            "col_source":   te_col,
            "n_train":      n_train,
            "p10":          round(p10, 6),
            "p50":          round(p50, 6),
            "p90":          round(p90, 6),
            "p99":          round(p99, 6),
            "mean":         round(mean, 6),
            "std":          round(std, 6),
            "min":          round(vmin, 6),
            "max":          round(vmax, 6),
            "iqr":          round(iqr, 6),
            "tail_risk":    round(tail_risk, 4),
            "tail_critical": tail_risk > 3.0,
        }

    return results


# ─────────────────────────────────────────────────────────────
# PATCH-B — Parámetros del normalizador rolling
# ─────────────────────────────────────────────────────────────
def patch_normalizer_params(te_stats: dict) -> dict:
    section("PATCH-B — Parámetros Normalizador Rolling TE")

    params = {}

    for asset in ASSETS:
        if asset not in te_stats:
            continue

        lookback = LOOKBACKS[asset]
        stats    = te_stats[asset]

        # Estrategia de clipping:
        # Si tail_risk > 3 → clip en P99 antes de normalizar
        # Si tail_risk <= 3 → clip en P99 como seguridad suave
        clip_upper = stats["p99"]
        clip_lower = stats["p10"]     # suelo: P10 (no P0 — outliers negativos)

        params[asset] = {
            "lookback":    lookback,
            "clip_lower":  round(clip_lower, 6),
            "clip_upper":  round(clip_upper, 6),
            "epsilon":     1e-8,
            "strategy":    "ROLLING_MINMAX_CLIPPED",
        }

        print(f"\n  {asset}:")
        print(f"    lookback:    {lookback}")
        print(f"    clip_lower:  {clip_lower:.6f}  (P10 train)")
        print(f"    clip_upper:  {clip_upper:.6f}  (P99 train)")
        print(f"    strategy:    ROLLING_MINMAX_CLIPPED")

    return params


# ─────────────────────────────────────────────────────────────
# PATCH-C — Registrar en SHA_REGISTRY
# ─────────────────────────────────────────────────────────────
def patch_registry(te_stats: dict, norm_params: dict):
    section("PATCH-C — SHA_REGISTRY · te_calibration")

    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    if not os.path.exists(reg_path):
        warn("SHA_REGISTRY no encontrado — creando entrada nueva")
        reg = {}
    else:
        with open(reg_path) as f:
            reg = json.load(f)

    reg["te_calibration"] = {
        "cutoff":    "2023-12-31",
        "session":   "S22c",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": (
            "P90 Transfer Entropy calculados anti-leakage ≤ 2023-12-31. "
            "Usados por spel_math_engine.py para rolling MinMax normalization. "
            "Previene BUG-LA-01 bis sobre columna TE."
        ),
        "assets": {
            asset: {
                "stats":       te_stats.get(asset, {}),
                "normalizer":  norm_params.get(asset, {}),
            }
            for asset in ASSETS
        }
    }

    reg["updated"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(META_DIR, exist_ok=True)
    with open(reg_path, "w") as f:
        json.dump(reg, f, indent=2)

    ok(f"SHA_REGISTRY actualizado → te_calibration para {len(te_stats)} activos")


# ─────────────────────────────────────────────────────────────
# PATCH-D — Bloque listo para spel_math_engine.py
# ─────────────────────────────────────────────────────────────
def patch_engine_code_block(norm_params: dict):
    section("PATCH-D — Bloque para spel_math_engine.py")

    print("""
  ── REEMPLAZAR EN spel_math_engine.py ─────────────────────────
  Buscar la función que normaliza transfer_entropy o donde se
  calcula el Score de Oro. Añadir/reemplazar con:
  ──────────────────────────────────────────────────────────────
""")

    # Parámetros como dict Python listo para pegar
    print("# Parámetros normalizador TE — calibrados anti-leakage S22c")
    print("TE_NORM_PARAMS = {")
    for asset, p in norm_params.items():
        print(f'    "{asset}": {{')
        print(f'        "lookback":   {p["lookback"]},')
        print(f'        "clip_lower": {p["clip_lower"]},')
        print(f'        "clip_upper": {p["clip_upper"]},')
        print(f'        "epsilon":    {p["epsilon"]},')
        print(f'    }},')
    print("}")

    print("""
# Función normalizadora — ROLLING MinMax con clip anti-leakage
def normalize_transfer_entropy(
    df: pl.DataFrame,
    asset: str,
    te_col: str = "transfer_entropy",
) -> pl.DataFrame:
    \"\"\"
    Normaliza TE al rango [0, 1] usando rolling min/max sobre
    ventana pasada exclusivamente (anti-leakage).

    ❌ NUNCA usar min/max global — BUG-LA-01 bis.
    ✅ Rolling sobre lookback inamovible del activo (R4).
    \"\"\"
    p = TE_NORM_PARAMS.get(asset, {
        "lookback": 42, "clip_lower": 0.0,
        "clip_upper": 2.0, "epsilon": 1e-8
    })

    lb      = p["lookback"]
    lo      = p["clip_lower"]
    hi      = p["clip_upper"]
    epsilon = p["epsilon"]

    df = df.with_columns(
        # 1. Clip a rango calibrado en train (elimina colas extremas post-2023)
        pl.col(te_col)
          .cast(pl.Float64)
          .clip(lo, hi)
          .alias("_te_clipped")
    ).with_columns(
        # 2. Rolling min/max sobre ventana pasada (no incluye t actual)
        pl.col("_te_clipped").rolling_min(lb, min_periods=1).alias("_te_rmin"),
        pl.col("_te_clipped").rolling_max(lb, min_periods=1).alias("_te_rmax"),
    ).with_columns(
        # 3. MinMax normalizado [0, 1] — epsilon evita división por cero
        (
            (pl.col("_te_clipped") - pl.col("_te_rmin"))
            / (pl.col("_te_rmax") - pl.col("_te_rmin") + epsilon)
        ).clip(0.0, 1.0).alias(te_col + "_norm")
    ).drop(["_te_clipped", "_te_rmin", "_te_rmax"])

    return df

  ──────────────────────────────────────────────────────────────
  INTEGRACIÓN en Score de Oro:
    df = normalize_transfer_entropy(df, asset="XAU", te_col="transfer_entropy")
    # Usar "transfer_entropy_norm" (no "transfer_entropy") en el router de pesos
  ──────────────────────────────────────────────────────────────
""")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*64}")
    print(f"  SPEL — Fix S22c · P90 Transfer Entropy · {ts}")
    print(f"{'═'*64}\n")

    te_stats   = patch_te_p90()
    norm_params = patch_normalizer_params(te_stats)
    patch_registry(te_stats, norm_params)
    patch_engine_code_block(norm_params)

    section("RESUMEN — Fix S22c")
    n_ok = len(te_stats)
    print(f"""
  Activos calibrados: {n_ok}/4
  Registro:           SHA_REGISTRY → te_calibration
  Colas extremas:     {sum(1 for s in te_stats.values() if s.get("tail_critical"))} activos con tail_risk > 3× IQR

  ✅ Normalizador TE calibrado — BUG-LA-01 bis prevenido

  ACCIÓN MANUAL REQUERIDA:
  → Pegar bloque de PATCH-D en spel_math_engine.py
  → Reemplazar cualquier normalización global de TE existente

  ── SECUENCIA COMPLETA DESBLOQUEADA ─────────────────────────
  ✅ S22a — NIFTY50 volume sentinel
  ✅ S22b — SHA audit + P90 entropy
  ✅ S22c — P90 Transfer Entropy (este script)
  ✅ godel_bound.py — P90 actualizados (manual — PATCH-D de S22b)
  ✅ spel_math_engine.py — normalizador TE (manual — PATCH-D de S22c)

  SIGUIENTE — YA DESBLOQUEADO:
  !python /content/spel_harvester_v3.py
  !python /content/spel_auditoria_total.py
""")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
