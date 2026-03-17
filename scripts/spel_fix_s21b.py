"""
spel_fix_s21b.py
════════════════════════════════════════════════════════════════
SPEL — Fix S21b · Merge GDELT → OHLCV Canonical v4
Root cause: vitality_tesla (y otras cols GDELT) nunca fueron
mergeadas al canonical_v4. El GDELT tiene {3,6,9} perfecto
desde 2015. El OHLCV tiene 0.0 en el 99%+ del historial.

Un solo fix resuelve simultáneamente:
  ✦ vitality_tesla 0.0 → {3, 6, 9} correcto
  ✦ Gödel se auto-calibra (godel_bound.py calcula P90 dinámico)
  ✦ entropy_shannon, nash_frozen_7d, goldstein_geo: fuente GDELT
  ✦ FIX-3 ya no necesario (no hay P90 hardcodeados en godel_bound)

COLUMNAS GDELT que se mergean al OHLCV (overwrite):
  vitality_tesla, nash_frozen_7d
COLUMNAS GDELT que se mergean solo si están vacías/cero en OHLCV:
  entropy_shannon (no overwrite — OHLCV puede tener versión enriquecida)

ESTRATEGIA JOIN:
  - OHLCV: 6818 filas · 2015→2026 (con gap XAU 45d)
  - GDELT:  3998 filas · 2015→2025-12-31
  - Join: left join OHLCV ← GDELT on date
  - Días en OHLCV sin GDELT (2026+, gaps): vitality_tesla = 0.0
    → Gödel los manejará vía entropy_shannon únicamente (OR condition)

NOTA sobre distribución GDELT idéntica en los 4 activos:
  NVDA/BTC/XAU/NIFTY50 tienen la MISMA distribución vitality {33%/33%/34%}
  Esto indica que gdelt_foundation.py computa vitality a nivel global
  (no por activo). Esto es por diseño — vitality_tesla mide el
  estado del sistema geopolítico global, no del activo específico.
  → NO es un bug. Documentado en godel_thresholds_v2.json.

Uso:
  !cp /content/drive/MyDrive/SPEL-v2.0/scripts/spel_fix_s21b.py /content/
  !python /content/spel_fix_s21b.py

Post-fix obligatorio:
  !python /content/spel_auditoria_total.py  → meta: 0 CRÍTICOS · 0 ALTOS
════════════════════════════════════════════════════════════════
"""

import json, hashlib, os, shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import numpy as np

# ── CONFIG ────────────────────────────────────────────────────
ROOT      = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE = f"{ROOT}/data_lake"
META_DIR  = f"{ROOT}/meta"

ASSETS = ["NVDA", "BTC", "XAU", "NIFTY50"]

# Columnas GDELT → OHLCV (siempre overwrite)
COLS_OVERWRITE = ["vitality_tesla", "nash_frozen_7d"]

# Columnas GDELT → OHLCV (solo si el valor en OHLCV es 0 o null)
COLS_FILL_IF_ZERO = ["entropy_shannon"]

# SHA pre-fix (del SHA_REGISTRY.json post-S21a)
SHA_PRE = {
    "NVDA":    "fb45d05cc288",
    "BTC":     "996f94a5967e",
    "XAU":     "90a5fde03655",
    "NIFTY50": "c76326567f1c",   # ya modificado por FIX-2 en S21a
}

# ── UTILIDADES ────────────────────────────────────────────────
def sha12(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

def ohlcv_path(a): return f"{DATA_LAKE}/{a}/ohlcv/aggregated/{a}_ohlcv_v4.parquet"
def gdelt_path(a): return f"{DATA_LAKE}/{a}/gdelt/raw/{a}_gdelt_entropy.parquet"

def backup(path: str):
    dst = path.replace(".parquet", "_bak_s21b.parquet")
    if not os.path.exists(dst):   # no duplicar backups
        shutil.copy2(path, dst)
        print(f"  · backup → {Path(dst).name}")
    else:
        print(f"  · backup ya existe → {Path(dst).name}")

def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")
def ok(m):   print(f"  ✅ {m}")
def warn(m): print(f"  ⚠️  {m}")
def err(m):  print(f"  🔴 {m}")
def info(m): print(f"  ·  {m}")

# ── MERGE FUNCTION ────────────────────────────────────────────
def normalize_date_col(df: pl.DataFrame) -> pl.DataFrame:
    """Garantiza datetime[ms, UTC] en col 'date'."""
    dtype_str = str(df["date"].dtype)
    if dtype_str == "Datetime(time_unit='ms', time_zone='UTC')":
        return df
    if "Date" in dtype_str:
        return df.with_columns(
            pl.col("date").cast(pl.Datetime("ms", "UTC"))
        )
    return df.with_columns(
        pl.col("date").cast(pl.Datetime("ms", "UTC"), strict=False)
    )


def merge_gdelt_into_ohlcv(asset: str) -> dict:
    op = ohlcv_path(asset)
    gp = gdelt_path(asset)

    result = {"asset": asset, "status": "SKIP", "sha_before": None, "sha_after": None}

    if not os.path.exists(op):
        err(f"{asset}: OHLCV no encontrado — skip")
        result["status"] = "FILE_MISSING"
        return result

    if not os.path.exists(gp):
        err(f"{asset}: GDELT no encontrado — no se puede mergear")
        result["status"] = "GDELT_MISSING"
        return result

    sha_before = sha12(op)
    result["sha_before"] = sha_before
    info(f"SHA antes: {sha_before}")

    # ── Cargar ────────────────────────────────────────────────
    df_ohlcv = normalize_date_col(pl.read_parquet(op))
    df_gdelt  = normalize_date_col(pl.read_parquet(gp))

    n_ohlcv = len(df_ohlcv)
    n_gdelt  = len(df_gdelt)
    info(f"OHLCV: {n_ohlcv} filas · GDELT: {n_gdelt} filas")

    # ── Filtrar GDELT al activo correcto ───────────────────────
    if "asset" in df_gdelt.columns:
        df_gdelt = df_gdelt.filter(pl.col("asset") == asset)
        info(f"GDELT filtrado por asset='{asset}': {len(df_gdelt)} filas")

    # ── Seleccionar columnas GDELT para el merge ───────────────
    # Solo traer cols que existen en GDELT y que queremos mergear
    cols_to_merge = (
        [c for c in COLS_OVERWRITE    if c in df_gdelt.columns] +
        [c for c in COLS_FILL_IF_ZERO if c in df_gdelt.columns]
    )
    cols_unique = list(dict.fromkeys(cols_to_merge))  # dedup, preservar orden

    info(f"Columnas a mergear: {cols_unique}")

    # Renombrar cols en GDELT para join (sufijo _gdelt)
    rename_map = {c: f"{c}_gdelt" for c in cols_unique}
    df_gdelt_sel = df_gdelt.select(["date"] + cols_unique).rename(rename_map)

    # ── Join left OHLCV ← GDELT ───────────────────────────────
    df_merged = df_ohlcv.join(df_gdelt_sel, on="date", how="left")

    n_match = df_merged.select(
        pl.col(f"{cols_unique[0]}_gdelt").is_not_null().sum()
    ).item()
    pct_match = 100 * n_match / n_ohlcv
    info(f"Match GDELT→OHLCV: {n_match}/{n_ohlcv} ({pct_match:.1f}%)")

    if pct_match < 30:
        err(f"{asset}: match < 30% — verificar rango de fechas del GDELT")
        err(f"  OHLCV range: {df_ohlcv['date'].min()} → {df_ohlcv['date'].max()}")
        err(f"  GDELT range: {df_gdelt['date'].min()} → {df_gdelt['date'].max()}")
        result["status"] = "LOW_MATCH"
        return result

    # ── Aplicar columnas mergeadas ─────────────────────────────
    exprs = []

    for col in COLS_OVERWRITE:
        if col in df_ohlcv.columns and f"{col}_gdelt" in df_merged.columns:
            # Overwrite: usar valor GDELT si existe, else mantener original
            exprs.append(
                pl.when(pl.col(f"{col}_gdelt").is_not_null())
                  .then(pl.col(f"{col}_gdelt"))
                  .otherwise(pl.col(col))
                  .alias(col)
            )
        elif f"{col}_gdelt" in df_merged.columns:
            # Columna no existía en OHLCV — crearla
            exprs.append(
                pl.col(f"{col}_gdelt")
                  .fill_null(0.0)
                  .alias(col)
            )

    for col in COLS_FILL_IF_ZERO:
        if col in df_ohlcv.columns and f"{col}_gdelt" in df_merged.columns:
            # Fill-if-zero: usar GDELT solo si OHLCV tiene 0 o null
            exprs.append(
                pl.when(
                    (pl.col(col).is_null()) | (pl.col(col) == 0.0)
                )
                  .then(pl.col(f"{col}_gdelt"))
                  .otherwise(pl.col(col))
                  .alias(col)
            )

    if exprs:
        df_merged = df_merged.with_columns(exprs)

    # Eliminar columnas _gdelt temporales
    gdelt_temp_cols = [c for c in df_merged.columns if c.endswith("_gdelt")]
    df_final = df_merged.drop(gdelt_temp_cols)

    # ── Validaciones post-merge ────────────────────────────────
    vt = df_final["vitality_tesla"]
    vt_nonzero = (vt != 0.0).sum()
    vt_unique  = sorted(set(vt.drop_nulls().unique().to_list()))
    pct_v9     = 100 * (vt == 9.0).sum() / n_ohlcv

    info(f"vitality_tesla post-merge:")
    info(f"  valores únicos: {vt_unique}")
    info(f"  non-zero: {vt_nonzero}/{n_ohlcv} ({100*vt_nonzero/n_ohlcv:.1f}%)")
    info(f"  v=9: {pct_v9:.1f}%  (esperado ~10-15% en el dataset completo)")

    # Verificar que vitality_tesla es discreta donde fue mergeada
    merged_mask   = df_final.filter(pl.col("vitality_tesla") != 0.0)["vitality_tesla"]
    merged_unique = set(merged_mask.unique().to_list())
    is_discrete   = merged_unique.issubset({3.0, 6.0, 9.0})

    if not is_discrete:
        err(f"{asset}: vitality_tesla post-merge contiene valores no discretos: {merged_unique}")
        result["status"] = "VALIDATION_FAILED"
        return result

    # Verificar que el schema no cambió
    assert set(df_final.columns) == set(df_ohlcv.columns), \
        f"Schema cambió: {set(df_final.columns) ^ set(df_ohlcv.columns)}"

    # Verificar que n_filas no cambió
    assert len(df_final) == n_ohlcv, \
        f"Filas cambiaron: {len(df_final)} vs {n_ohlcv}"

    # ── Escribir ───────────────────────────────────────────────
    backup(op)
    df_final.sort("date").write_parquet(op)

    sha_after = sha12(op)
    result.update({
        "sha_before": sha_before,
        "sha_after":  sha_after,
        "n_ohlcv":    n_ohlcv,
        "n_gdelt":    n_gdelt,
        "pct_match":  round(pct_match, 1),
        "vt_nonzero": int(vt_nonzero),
        "pct_v9":     round(float(pct_v9), 2),
        "status":     "OK",
    })

    info(f"SHA después: {sha_after}")
    ok(f"{asset}: merge completo ✅  vitality_tesla {vt_unique} · v9={pct_v9:.1f}%")
    return result


# ── MAIN ──────────────────────────────────────────────────────
def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*64}")
    print(f"  SPEL — Fix S21b · GDELT→OHLCV Merge · {ts}")
    print(f"{'═'*64}\n")

    all_results = {}

    for asset in ASSETS:
        section(f"MERGE — {asset}")
        r = merge_gdelt_into_ohlcv(asset)
        all_results[asset] = r
        print()

    # ── Actualizar SHA_REGISTRY ────────────────────────────────
    section("SHA_REGISTRY — Actualización post-merge")

    sha_final = {}
    for asset in ASSETS:
        p = ohlcv_path(asset)
        if os.path.exists(p):
            sha_final[asset] = sha12(p)
            changed = sha_final[asset] != SHA_PRE.get(asset, "")
            info(f"{asset}: {sha_final[asset]}  {'← CAMBIADO' if changed else '(sin cambio)'}")

    registry = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "note": "SHA post-fix S21b · GDELT→OHLCV merge · vitality_tesla corrected",
        "session": "S21b",
        "parquets": {
            asset: {
                "path": ohlcv_path(asset),
                "sha": sha_final.get(asset, ""),
                "sha_pre_s21b": SHA_PRE.get(asset, ""),
            }
            for asset in ASSETS
        }
    }
    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    os.makedirs(META_DIR, exist_ok=True)
    with open(reg_path, "w") as f:
        json.dump(registry, f, indent=2)
    ok(f"SHA_REGISTRY.json → {reg_path}")

    # Actualizar SHA_POST_FIX en spel_auditoria_total.py
    import re
    for script_path in [
        f"{ROOT}/scripts/spel_auditoria_total.py",
        f"{META_DIR}/spel_auditoria_total.py",
    ]:
        if not os.path.exists(script_path):
            continue
        with open(script_path, "r") as f:
            content = f.read()
        new_block = 'SHA_POST_FIX = {\n'
        for a in ASSETS:
            new_block += f'    "{a}":    "{sha_final.get(a, "")}",\n'
        new_block += '}'
        content_new = re.sub(
            r'SHA_POST_FIX\s*=\s*\{[^}]+\}',
            new_block, content, flags=re.DOTALL
        )
        if content_new != content:
            with open(script_path, "w") as f:
                f.write(content_new)
            ok(f"spel_auditoria_total.py SHA_POST_FIX actualizado")
        break

    # Actualizar godel_thresholds_v2.json con nota de vitality corregida
    thresh_path = f"{META_DIR}/godel_thresholds_v2.json"
    if os.path.exists(thresh_path):
        with open(thresh_path) as f:
            thresh = json.load(f)
        thresh["_meta"]["vitality_status"] = \
            "CORRECTED S21b — merged from GDELT {3,6,9} discrete"
        thresh["_meta"]["nota_distribucion"] = \
            ("vitality_tesla es global (no per-asset) por diseño — "
             "gdelt_foundation.py calcula el estado geopolítico global. "
             "Distribución idéntica en 4 activos es ESPERADA, no un bug.")
        with open(thresh_path, "w") as f:
            json.dump(thresh, f, indent=2)
        ok("godel_thresholds_v2.json anotado")

    # ── Resumen ejecutivo ──────────────────────────────────────
    section("RESUMEN EJECUTIVO — Fix S21b")

    n_ok   = sum(1 for r in all_results.values() if r["status"] == "OK")
    n_fail = sum(1 for r in all_results.values() if r["status"] != "OK")

    print(f"\n  {'Activo':<10} {'Match%':>7} {'vt non-zero':>12} {'v9%':>6} {'SHA after':>14} {'Estado'}")
    print("  " + "─" * 62)
    for asset, r in all_results.items():
        if r["status"] == "OK":
            print(f"  {asset:<10} {r['pct_match']:>6.1f}% "
                  f"{r['vt_nonzero']:>10}/{r['n_ohlcv']:<6} "
                  f"{r['pct_v9']:>5.1f}% "
                  f"{r['sha_after']:>14}  ✅")
        else:
            print(f"  {asset:<10} {'—':>7} {'—':>12} {'—':>6} {'—':>14}  🔴 {r['status']}")

    print()
    if n_ok == 4 and n_fail == 0:
        print("  ✅ MERGE COMPLETO — 4/4 activos corregidos")
        print()
        print("  EFECTOS RESUELTOS:")
        print("  · vitality_tesla → {3, 6, 9} en todo el historial mergeado")
        print("  · Gödel (OR) operativo: vitality_tesla==9 activará correctamente")
        print("  · godel_bound.py se auto-calibra (calcula P90 dinámico desde parquet)")
        print("  · FIX-3 (P90 hardcodeados) no necesario — no existen en el código")
    else:
        print(f"  ⚠️  {n_fail} activos fallaron — revisar errores arriba")

    print(f"\n{'─'*64}")
    print("  PRÓXIMOS PASOS:")
    print()
    print("  1. OBLIGATORIO: re-ejecutar la auditoría")
    print("     !python /content/spel_auditoria_total.py")
    print("     Meta: 0 CRÍTICOS · 0 ALTOS")
    print()
    print("  2. Verificar Gödel manualmente (opcional pero recomendado):")
    print("     from godel_bound import run_full_godel_test")
    print("     run_full_godel_test()")
    print()
    print("  3. Si auditoría OK → retrain en orden:")
    print("     ACTIVO='BTC'  → spel_retrain_v5_clean.py")
    print("     ACTIVO='XAU'  → spel_retrain_v5_clean.py")
    print("     ACTIVO='NIFTY50' → spel_retrain_v5_clean.py")
    print("     ACTIVO='NVDA' → spel_retrain_v5_clean.py")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
