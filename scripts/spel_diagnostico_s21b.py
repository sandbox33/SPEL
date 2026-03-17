"""
spel_diagnostico_s21b.py
════════════════════════════════════════════════════════════════
DIAGNÓSTICO PREVIO AL FIX-1 CORRECTO
Corre esto ANTES de spel_fix_s21b.py

Responde 4 preguntas:
  Q1. ¿Qué hay en vitality_tesla de los parquets GDELT?
      (discreta {3/6/9} vs continua vs ceros)
  Q2. ¿Cuántas filas del OHLCV tienen vitality_tesla != 0.0?
      (nos dice qué período cubrió el merge parcial)
  Q3. ¿Qué estructura exacta usa godel_bound.py para los P90?
      (para que el FIX-3 parchee correctamente)
  Q4. ¿El GDELT tiene suficiente cobertura pre-2024 para reconstruir vitality?

Uso:
  !python /content/spel_diagnostico_s21b.py
  → pegar el output aquí antes de ejecutar spel_fix_s21b.py
════════════════════════════════════════════════════════════════
"""
import os, json
from pathlib import Path
from datetime import datetime, timezone
import polars as pl
import numpy as np

ROOT      = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE = f"{ROOT}/data_lake"
ASSETS    = ["NVDA", "BTC", "XAU", "NIFTY50"]

def hline(): print("─" * 64)
def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")

# ── Q1 + Q4: GDELT vitality_tesla ────────────────────────────
section("Q1/Q4 — GDELT parquets: vitality_tesla y cobertura")

for asset in ASSETS:
    gpath = f"{DATA_LAKE}/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet"
    if not os.path.exists(gpath):
        print(f"  {asset}: GDELT NO ENCONTRADO")
        continue

    gdf = pl.read_parquet(gpath)
    print(f"\n  {asset} GDELT ({len(gdf)} filas)")
    print(f"  cols: {gdf.columns}")

    # Date range
    if "date" in gdf.columns:
        print(f"  rango: {gdf['date'].min()} → {gdf['date'].max()}")

    if "vitality_tesla" in gdf.columns:
        vt = gdf["vitality_tesla"].drop_nulls()
        unique_vals = sorted(set(vt.unique().to_list()))
        is_discrete = set(unique_vals).issubset({3.0, 6.0, 9.0, 0.0})
        all_zero    = all(v == 0.0 for v in unique_vals)

        print(f"  vitality_tesla: {len(unique_vals)} valores únicos")
        print(f"    is_discrete={is_discrete}  all_zero={all_zero}")
        if len(unique_vals) <= 10:
            print(f"    valores: {unique_vals}")
        else:
            print(f"    muestra: {unique_vals[:5]} ... {unique_vals[-5:]}")
            print(f"    min={min(unique_vals):.4f}  max={max(unique_vals):.4f}  mean={float(vt.mean()):.4f}")

        # Distribución {3/6/9} si es discreta
        if is_discrete and not all_zero:
            for v in [3.0, 6.0, 9.0]:
                n = (gdf["vitality_tesla"] == v).sum()
                pct = 100 * n / len(gdf)
                print(f"    v={v}: {n} ({pct:.1f}%)")
    else:
        print(f"  vitality_tesla: COLUMNA AUSENTE")

# ── Q2: OHLCV vitality_tesla cobertura temporal ───────────────
section("Q2 — OHLCV: vitality_tesla cobertura temporal")

for asset in ASSETS:
    opath = f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v4.parquet"
    if not os.path.exists(opath):
        continue

    df = pl.read_parquet(opath)

    if "vitality_tesla" not in df.columns:
        print(f"  {asset}: vitality_tesla AUSENTE en OHLCV")
        continue

    vt = df["vitality_tesla"]
    n_nonzero = (vt != 0.0).sum()
    n_total   = len(df)
    pct       = 100 * n_nonzero / n_total

    # Fecha del primer y último valor != 0
    if n_nonzero > 0:
        nonzero_mask = vt != 0.0
        dates_nonzero = df.filter(nonzero_mask)["date"]
        first_nonzero = dates_nonzero.min()
        last_nonzero  = dates_nonzero.max()
    else:
        first_nonzero = last_nonzero = None

    print(f"\n  {asset}: vitality_tesla != 0.0 → {n_nonzero}/{n_total} ({pct:.1f}%)")
    print(f"    primer valor != 0: {first_nonzero}")
    print(f"    último valor != 0: {last_nonzero}")

    # Cuántos ceros hay en el período de training (≤ 2023-12-31)
    cutoff = datetime(2023, 12, 31, tzinfo=timezone.utc)
    if str(df["date"].dtype) == "Datetime(time_unit='ms', time_zone='UTC')":
        df_train = df.filter(pl.col("date") <= pl.lit(cutoff))
    else:
        df_train = df.head(int(n_total * 0.7))  # approx

    vt_train = df_train["vitality_tesla"]
    n_zero_train = (vt_train == 0.0).sum()
    n_train = len(df_train)
    print(f"    en train (≤2023-12-31): {n_train} filas, zeros={n_zero_train} ({100*n_zero_train/n_train:.1f}%)")

# ── Q3: godel_bound.py estructura ────────────────────────────
section("Q3 — godel_bound.py: estructura del P90 dict")

godel_path = f"{ROOT}/codigo/core/godel_bound.py"
if os.path.exists(godel_path):
    with open(godel_path, "r") as f:
        lines = f.readlines()

    print(f"  Archivo: {godel_path}")
    print(f"  Total líneas: {len(lines)}")
    print()

    # Buscar sección de P90 / thresholds / umbral
    keywords = ["p90", "P90", "threshold", "THRESHOLD", "umbral", "entropy_p90",
                "0.95", "1.35", "1.18", "1.1898", "NVDA", "BTC", "XAU", "NIFTY50"]
    relevant_lines = []
    for i, line in enumerate(lines):
        if any(kw in line for kw in keywords):
            relevant_lines.append((i+1, line.rstrip()))

    print("  Líneas relevantes (P90/thresholds):")
    for lineno, content in relevant_lines[:40]:
        print(f"    {lineno:4d}: {content}")

    if not relevant_lines:
        print("  (ninguna línea con keywords P90/threshold encontrada)")
        print("  → Mostrando primeras 30 líneas del archivo:")
        for i, line in enumerate(lines[:30]):
            print(f"    {i+1:4d}: {line.rstrip()}")
else:
    print(f"  godel_bound.py NO encontrado en {godel_path}")
    # Buscar en otras rutas
    for alt in [f"{ROOT}/codigo/godel_bound.py",
                f"/content/spel_root/codigo/core/godel_bound.py",
                "/content/godel_bound.py"]:
        if os.path.exists(alt):
            print(f"  Encontrado en: {alt}")
            break

print(f"\n{'═'*64}")
print("  Pega este output completo antes de ejecutar spel_fix_s21b.py")
print(f"{'═'*64}\n")
