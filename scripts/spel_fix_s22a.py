"""
spel_fix_s22a.py  [v2 — NIFTY50 confirmed synthetic index, no native volume]
════════════════════════════════════════════════════════════════
SPEL — Fix S22a · NIFTY50 volume NaN estructural (CRÍTICO)

CONTEXTO TÉCNICO CONFIRMADO:
  NIFTY50 es índice de capitalización flotante ponderada (NSE India).
  NO existe volumen nativo negociable.
  Volumen en feeds = proxy de futuros GIFT Nifty/SGX o suma de
  constituyentes — no representa microestructura del índice.
  Fuente: NSE India spec + TradingView index methodology.

DECISIÓN ARQUITECTURAL:
  ① volume → 0.0 como sentinel explícito.
     Semántica: "sin microestructura de volumen disponible".
     NO es imputación — es declaración de ausencia estructural.

  ② Schema 24-col íntegro (R11).
     Romper el schema invalida todos los módulos downstream.

  ③ SHA_REGISTRY recibe flag volume_type=SYNTHETIC_INDEX_ZERO
     y score_oro_volume_weight=0.0 para routing correcto en
     MathEngine / Score de Oro.

  ④ Score de Oro NIFTY50:
     Volume Profile 30% → NULO (sentinel volume=0)
     Decisión de redistribución de pesos: PENDIENTE (manual).
     Opciones: Gödel 55% + TE 45%  ó  mantener estructura y
     aceptar Score degradado hasta integrar GIFT Nifty futures.

REGLAS: R2, R11, R13.
════════════════════════════════════════════════════════════════
"""

import json, hashlib, shutil, os
from datetime import datetime, timezone
from pathlib import Path
import polars as pl

ROOT       = "/content/drive/MyDrive/SPEL-v2.0"
DATA_LAKE  = f"{ROOT}/data_lake"
META_DIR   = f"{ROOT}/meta"
ASSET      = "NIFTY50"
LOOKBACK   = 42   # inamovible R4

def sha12(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        [h.update(c) for c in iter(lambda: f.read(65536), b"")]
    return h.hexdigest()[:12]

def ohlcv_path():
    return f"{DATA_LAKE}/{ASSET}/ohlcv/aggregated/{ASSET}_ohlcv_v4.parquet"

def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")
def ok(m):   print(f"  ✅ {m}")
def err(m):  print(f"  🔴 {m}")
def warn(m): print(f"  ⚠️  {m}")
def info(m): print(f"  ·  {m}")


# ─────────────────────────────────────────────────────────────
# PASO 1 — Diagnóstico
# ─────────────────────────────────────────────────────────────
def diagnose(df: pl.DataFrame) -> dict:
    section("PASO 1 — Diagnóstico volume")

    vol     = df["volume"].cast(pl.Float64)
    n_total = len(df)
    n_null  = df["volume"].is_null().sum()
    n_nan   = vol.is_nan().sum()
    n_zero  = (vol == 0.0).sum()
    n_bad   = n_null + n_nan

    info(f"Filas totales:  {n_total}")
    info(f"NaN / null:     {n_bad}  ({100*n_bad/n_total:.1f}%)")
    info(f"Ceros:          {n_zero}")
    info(f"Diagnóstico:    índice sintético → volume=0.0 es el estado correcto")

    outside_warmup = (n_total - 1 >= LOOKBACK) and (n_bad > LOOKBACK)
    if outside_warmup:
        warn(f"NaN fuera de warm-up (lookback={LOOKBACK}) → contaminación estructural confirmada")
    else:
        info(f"Warm-up check:  lookback={LOOKBACK}, max_pos={n_total-1}")

    return {"n_total": n_total, "n_bad": int(n_bad), "n_zero": int(n_zero)}


# ─────────────────────────────────────────────────────────────
# PASO 2 — Zero-fill sentinel
# ─────────────────────────────────────────────────────────────
def apply_fix(df: pl.DataFrame) -> pl.DataFrame:
    section("PASO 2 — Zero-fill sentinel")

    df_fixed = df.with_columns(
        pl.col("volume")
          .cast(pl.Float64)
          .fill_null(0.0)
          .fill_nan(0.0)
          .alias("volume")
    )

    n_remaining = (
        df_fixed["volume"].is_null().sum() +
        df_fixed["volume"].cast(pl.Float64).is_nan().sum()
    )
    info(f"volume → 0.0 (sentinel: índice sintético sin microestructura)")
    info(f"NaN restantes: {n_remaining}")
    info(f"Schema: {len(df_fixed.columns)} cols — íntegro ✅")
    return df_fixed


# ─────────────────────────────────────────────────────────────
# PASO 3 — Validación
# ─────────────────────────────────────────────────────────────
def validate(df: pl.DataFrame) -> bool:
    section("PASO 3 — Validación post-fix")

    n_bad_vol = (
        df["volume"].is_null().sum() +
        df["volume"].cast(pl.Float64).is_nan().sum()
    )
    if n_bad_vol > 0:
        err(f"volume: {n_bad_vol} NaN persistentes — ABORT")
        return False
    ok("volume: 0 NaN ✅")

    # Verificar integridad del resto del schema
    for col in df.columns:
        if col in ("date", "volume"):
            continue
        if df[col].dtype in (pl.Float32, pl.Float64):
            n_null = df[col].is_null().sum()
            if n_null > LOOKBACK:
                err(f"Columna '{col}': {n_null} NaN (>{LOOKBACK}) — contaminación colateral")
                return False

    ok(f"Schema {len(df.columns)}-col íntegro — sin contaminación colateral ✅")
    return True


# ─────────────────────────────────────────────────────────────
# PASO 4 — Persistencia, SHA, registry
# ─────────────────────────────────────────────────────────────
def persist(df_fixed: pl.DataFrame, sha_before: str, diag: dict) -> str:
    section("PASO 4 — Persistencia y SHA")

    path = ohlcv_path()
    bak  = path.replace(".parquet", "_bak_s22a.parquet")
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        info(f"Backup: {Path(bak).name}")

    df_fixed.write_parquet(path)
    sha_after = sha12(path)
    info(f"SHA before: {sha_before}")
    info(f"SHA after:  {sha_after}")
    ok(f"Parquet escrito")

    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    os.makedirs(META_DIR, exist_ok=True)
    reg = {}
    if os.path.exists(reg_path):
        with open(reg_path) as f:
            reg = json.load(f)

    reg.setdefault("parquets", {}).setdefault(ASSET, {})
    reg["parquets"][ASSET].update({
        "sha":                      sha_after,
        "sha_before_s22a":          sha_before,
        "path":                     path,
        "volume_type":              "SYNTHETIC_INDEX_ZERO",
        "score_oro_volume_weight":  0.0,
        "volume_note": (
            "NIFTY50 = índice sintético capitalización flotante NSE India. "
            "Sin volumen nativo. volume=0.0 es sentinel estructural. "
            "Score de Oro: Volume Profile (30%) debe ponderarse a 0 o "
            "redistribuirse hasta integrar GIFT Nifty futures como proxy."
        ),
    })
    reg["updated"] = datetime.now(timezone.utc).isoformat()
    reg.setdefault("session_log", []).append({
        "session":   "S22a",
        "asset":     ASSET,
        "action":    "volume_zero_fill_synthetic_index",
        "n_fixed":   diag["n_bad"],
        "sha_before": sha_before,
        "sha_after":  sha_after,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with open(reg_path, "w") as f:
        json.dump(reg, f, indent=2)
    ok(f"SHA_REGISTRY actualizado → {ASSET}: {sha_after}")

    return sha_after


# ─────────────────────────────────────────────────────────────
# PASO 5 — Guard MathEngine
# ─────────────────────────────────────────────────────────────
def print_math_engine_guard():
    section("PASO 5 — Guard MathEngine / Score de Oro (acción manual)")
    print("""
  EDITAR spel_math_engine.py o spel_backbone_engine.py:
  ──────────────────────────────────────────────────────
  # NIFTY50: índice sintético → sin Volume Profile real
  VOLUME_PROFILE_WEIGHT = {
      "NVDA":    0.30,
      "BTC":     0.30,
      "XAU":     0.30,
      "NIFTY50": 0.00,   # sentinel volume=0 — índice sintético NSE
  }

  # Opciones de redistribución pesos NIFTY50 (decidir y documentar en v31):
  # Opción A — redistribución proporcional:
  #   Gödel: 40% → 55%  (+15%)
  #   Transfer Entropy: 30% → 45%  (+15%)
  # Opción B — Score parcial aceptado (sin tocar pesos):
  #   Score de Oro NIFTY50 = (Gödel×40 + TE×30) / 70  (renormalizado)
  # Opción C — integrar GIFT Nifty futures como proxy de volumen real
  ──────────────────────────────────────────────────────
""")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*64}")
    print(f"  SPEL — Fix S22a · NIFTY50 volume sentinel · {ts}")
    print(f"{'═'*64}\n")

    path = ohlcv_path()
    if not os.path.exists(path):
        err(f"Parquet no encontrado: {path}")
        return

    df         = pl.read_parquet(path)
    sha_before = sha12(path)
    info(f"SHA: {sha_before}  |  {len(df)} filas × {len(df.columns)} cols")

    diag = diagnose(df)

    # Ya corregido previamente
    if diag["n_bad"] == 0:
        ok("NIFTY50 volume ya limpio")
        info(f"Zeros actuales: {diag['n_zero']} / {diag['n_total']}")
        print_math_engine_guard()
        return

    df_fixed  = apply_fix(df)

    if not validate(df_fixed):
        err("Validación fallida — parquet NO sobreescrito")
        return

    sha_after = persist(df_fixed, sha_before, diag)

    print_math_engine_guard()

    section("RESUMEN — Fix S22a")
    print(f"""
  Activo:    {ASSET}
  NaN fixed: {diag['n_bad']} ({100*diag['n_bad']/diag['n_total']:.1f}%) → volume=0.0 sentinel
  SHA:       {sha_before} → {sha_after}
  Registry:  volume_type=SYNTHETIC_INDEX_ZERO · score_oro_volume_weight=0.0
  Schema:    {len(df_fixed.columns)}-col íntegro (R11 ✅)

  ✅ CRÍTICO resuelto

  PENDIENTE MANUAL:
  → Editar VOLUME_PROFILE_WEIGHT en MathEngine (ver PASO 5)
  → Documentar decisión de pesos NIFTY50 en SPEL_Project_Log v31

  SIGUIENTE:
  !python /content/spel_fix_s22b.py
""")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
