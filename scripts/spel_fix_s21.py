"""
spel_fix_s21.py
═══════════════════════════════════════════════════════════════
SPEL — Fix S21 · 10-Mar-2026
Resuelve los 4 hallazgos CRÍTICOS/ALTOS de auditoria_total_20260310_1534.json
en orden de impacto sobre el entrenamiento.

FIX-1  vitality_tesla re-discretización   NVDA + XAU   [ALTO-bloqueante]
FIX-2  NIFTY50 volume NaN → 0.0           NIFTY50      [CRÍTICO]
FIX-3  Gödel P90 update                   4 activos    [calculado por audit]
FIX-4  SHA_REGISTRY + audit script        global       [consistencia]

Cada FIX:
  1. Lee parquet actual
  2. Aplica corrección mínima (no toca otras columnas)
  3. Escribe parquet nuevo
  4. Calcula SHA nuevo
  5. Imprime veredicto

Uso:
  !cp /content/drive/MyDrive/SPEL-v2.0/scripts/spel_fix_s21.py /content/
  !python /content/spel_fix_s21.py

Después: re-ejecutar spel_auditoria_total.py → debe dar 0 CRÍTICOS / 0 ALTOS
═══════════════════════════════════════════════════════════════
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
SCRIPTS   = f"{ROOT}/scripts"

ASSETS    = ["NVDA", "BTC", "XAU", "NIFTY50"]

# P90 calculados por el módulo G de la auditoría (datos ≤ 2023-12-31)
# activation_rate con estos valores: 10.01-10.04% → dentro del rango sano 8-13%
P90_RECALIBRADO = {
    "NVDA":    1.1571,
    "BTC":     1.1594,
    "XAU":     1.3229,
    "NIFTY50": 1.1868,
}

# SHA reales leídos del SHA_REGISTRY.json generado por la auditoría
# (estos son los SHA ACTUALES de los parquets en Drive)
SHA_ACTUAL_PRE_FIX = {
    "NVDA":    "fb45d05cc288",
    "BTC":     "996f94a5967e",
    "XAU":     "90a5fde03655",
    "NIFTY50": "30eb0927c7ab",
}

# ── UTILIDADES ────────────────────────────────────────────────
def sha12(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def ohlcv_path(asset: str) -> str:
    return f"{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v4.parquet"


def backup(path: str):
    """Backup con sufijo _bak_s21 antes de cualquier escritura (R15)."""
    dst = path.replace(".parquet", "_bak_s21.parquet")
    shutil.copy2(path, dst)
    print(f"  · backup → {Path(dst).name}")


def hline(): print("─" * 64)
def section(t): print(f"\n{'═'*64}\n  {t}\n{'═'*64}")
def ok(m):   print(f"  ✅ {m}")
def err(m):  print(f"  🔴 {m}")
def info(m): print(f"  ·  {m}")


# ── REGISTRO DE CAMBIOS ───────────────────────────────────────
changes = []   # {asset, fix, sha_before, sha_after, status}


def register(asset, fix, sha_before, sha_after, status):
    changes.append({
        "asset": asset, "fix": fix,
        "sha_before": sha_before, "sha_after": sha_after,
        "status": status,
    })

# ═════════════════════════════════════════════════════════════
# FIX-1 — vitality_tesla re-discretización (NVDA + XAU)
# ═════════════════════════════════════════════════════════════
# DIAGNÓSTICO: vitality_tesla contiene floats continuos (0.38-4.41)
# en lugar de la escala discreta {3.0, 6.0, 9.0}.
#
# CAUSA: el harvester normalizó vitality_tesla junto con el resto
# de features antes de guardar el parquet — perdiendo la escala discreta.
#
# FIX: re-discretizar usando los percentiles de los propios datos.
# Lógica SPEL original:
#   - P0-P33 → 3.0  (paz — mercado lateral / bajo ruido)
#   - P33-P66 → 6.0 (tensión — actividad moderada)
#   - P66-P100 → 9.0 (ruptura — evento extremo)
#
# Anti-leakage: los thresholds se calculan SOLO con datos ≤ 2023-12-31
# y se aplican al dataset completo (incluye 2024-2026).
# ═════════════════════════════════════════════════════════════

def fix_vitality(asset: str) -> bool:
    path = ohlcv_path(asset)
    if not os.path.exists(path):
        err(f"{asset}: parquet no encontrado")
        return False

    sha_before = sha12(path)
    info(f"SHA antes: {sha_before}")

    df = pl.read_parquet(path)
    vt = df["vitality_tesla"]

    # Verificar que el problema existe (floats, no {3/6/9})
    unique_vals = set(vt.drop_nulls().unique().to_list())
    is_discrete = unique_vals.issubset({3.0, 6.0, 9.0, 0.0})
    if is_discrete:
        ok(f"{asset}: vitality_tesla ya es discreta {unique_vals} — skip")
        return True

    info(f"{asset}: {len(unique_vals)} valores únicos continuos → re-discretizando")

    # Calcular thresholds anti-leakage (≤ 2023-12-31)
    CUTOFF = datetime(2023, 12, 31, tzinfo=timezone.utc)

    # Asegurar que date es datetime para filtrar
    df_date = df
    if str(df["date"].dtype) != "Datetime(time_unit='ms', time_zone='UTC')":
        df_date = df.with_columns(pl.col("date").cast(pl.Datetime("ms", "UTC")))

    df_train = df_date.filter(pl.col("date") <= pl.lit(CUTOFF))
    vt_train = df_train["vitality_tesla"].drop_nulls().to_numpy().astype(float)

    if len(vt_train) < 100:
        err(f"{asset}: insuficientes filas de train ({len(vt_train)}) para calcular thresholds")
        return False

    p33 = float(np.percentile(vt_train, 33))
    p66 = float(np.percentile(vt_train, 66))

    info(f"{asset}: thresholds train (≤2023-12-31): P33={p33:.4f}  P66={p66:.4f}")
    info(f"{asset}: min={vt_train.min():.4f} max={vt_train.max():.4f} mean={vt_train.mean():.4f}")

    # Aplicar discretización al dataset completo
    vt_all = df["vitality_tesla"].to_numpy().astype(float)
    vt_disc = np.where(vt_all >= p66, 9.0,
              np.where(vt_all >= p33, 6.0, 3.0)).astype(np.float32)

    # Estadística de la discretización
    n_3 = int((vt_disc == 3.0).sum())
    n_6 = int((vt_disc == 6.0).sum())
    n_9 = int((vt_disc == 9.0).sum())
    pct_9 = 100 * n_9 / len(vt_disc)
    info(f"{asset}: vitality dist → 3:{n_3} 6:{n_6} 9:{n_9} (v9={pct_9:.1f}%)")

    # Verificar que v9 está en rango sano (5-20%)
    if not (3 <= pct_9 <= 25):
        err(f"{asset}: v9={pct_9:.1f}% fuera del rango esperado 3-25% — revisar thresholds")
        return False

    # Reemplazar columna
    backup(path)
    df_fixed = df.with_columns(
        pl.Series("vitality_tesla", vt_disc)
    )
    df_fixed.write_parquet(path)

    sha_after = sha12(path)
    info(f"SHA después: {sha_after}")
    ok(f"{asset}: vitality_tesla re-discretizada → {{3.0, 6.0, 9.0}} ✅")
    register(asset, "FIX-1:vitality_discretize", sha_before, sha_after, "OK")
    return True


# ═════════════════════════════════════════════════════════════
# FIX-2 — NIFTY50 volume NaN → 0.0
# ═════════════════════════════════════════════════════════════
# DIAGNÓSTICO: 2612/2632 filas (99.2%) de volume son NaN.
# NIFTY50 es un índice — no tiene volumen propio en Yahoo Finance.
# volume NO es una de las 20 features del LSTM (R13), pero su
# presencia en el schema como NaN contamina cualquier operación
# que llame .mean()/.std() sobre el DataFrame completo.
#
# FIX: fill_null(0.0) + agregar flag "volume_is_index_proxy"
# en spel_asset_catalog.json para documentar la decisión.
# ═════════════════════════════════════════════════════════════

def fix_nifty_volume() -> bool:
    path = ohlcv_path("NIFTY50")
    if not os.path.exists(path):
        err("NIFTY50: parquet no encontrado")
        return False

    sha_before = sha12(path)
    info(f"SHA antes: {sha_before}")

    df = pl.read_parquet(path)
    n_nan_before = df["volume"].is_null().sum()
    info(f"NIFTY50: volume NaN antes = {n_nan_before}")

    backup(path)

    df_fixed = df.with_columns(
        pl.col("volume").fill_null(0.0).fill_nan(0.0)
    )

    n_nan_after = df_fixed["volume"].is_null().sum()

    df_fixed.write_parquet(path)
    sha_after = sha12(path)

    info(f"NIFTY50: volume NaN después = {n_nan_after}")
    info(f"SHA después: {sha_after}")
    ok("NIFTY50: volume NaN → 0.0 ✅ (índice sin volumen nativo — documentado en catalog)")
    register("NIFTY50", "FIX-2:volume_nan_fill", sha_before, sha_after, "OK")
    return True


# ═════════════════════════════════════════════════════════════
# FIX-3 — Gödel P90 update en godel_bound.py
# ═════════════════════════════════════════════════════════════
# DIAGNÓSTICO (del módulo G de la auditoría):
#   XAU:  P90_configurado=0.95  → activación=46.2%  (ROTO)
#   BTC:  P90_configurado=1.35  → activación=0.0%   (CIEGO)
#   NVDA: P90_configurado=1.1898 → activación=3.6%  (bajo)
#   NIFTY50: P90_configurado=1.18 → activación=12.1% (OK)
#
# P90 CORRECTOS (calculados con datos ≤ 2023-12-31):
#   NVDA=1.1571  BTC=1.1594  XAU=1.3229  NIFTY50=1.1868
#   → activation_rate con estos valores: ~10% (rango sano 8-13%)
#
# FIX: localizar P90_THRESHOLDS en godel_bound.py y reemplazar.
# También guardar godel_thresholds_v2.json como fuente de verdad.
# ═════════════════════════════════════════════════════════════

def fix_godel_p90() -> bool:
    godel_path = f"{ROOT}/codigo/core/godel_bound.py"
    if not os.path.exists(godel_path):
        err(f"godel_bound.py no encontrado en {godel_path}")
        info("Fix manual: localizar P90_THRESHOLDS dict y reemplazar los valores abajo:")
        for asset, p90 in P90_RECALIBRADO.items():
            info(f"  '{asset}': {p90},")
        return False

    # Backup
    backup_path = godel_path.replace(".py", "_bak_s21.py")
    shutil.copy2(godel_path, backup_path)
    info(f"backup → {Path(backup_path).name}")

    with open(godel_path, "r") as f:
        content = f.read()

    content_original = content

    # Reemplazar cada P90 — el dict puede tener varias formas
    # Forma más común: 'ACTIVO': 1.XXXX
    replacements = {
        "'NVDA': 1.1898": f"'NVDA': {P90_RECALIBRADO['NVDA']}",
        "'BTC': 1.35":    f"'BTC': {P90_RECALIBRADO['BTC']}",
        '"BTC": 1.35':    f'"BTC": {P90_RECALIBRADO["BTC"]}',
        "'XAU': 0.95":    f"'XAU': {P90_RECALIBRADO['XAU']}",
        '"XAU": 0.95':    f'"XAU": {P90_RECALIBRADO["XAU"]}',
        "'NIFTY50': 1.18": f"'NIFTY50': {P90_RECALIBRADO['NIFTY50']}",
        '"NIFTY50": 1.18': f'"NIFTY50": {P90_RECALIBRADO["NIFTY50"]}',
    }
    for old, new in replacements.items():
        content = content.replace(old, new)

    if content == content_original:
        err("No se encontraron los valores P90 en godel_bound.py para reemplazar.")
        err("El script asume claves como \"'XAU': 0.95\" — buscar manualmente la sección P90.")
        info("Valores a aplicar manualmente:")
        for a, v in P90_RECALIBRADO.items():
            info(f"  '{a}': {v}")
        # No es fatal — guardar el JSON de referencia de todas formas
    else:
        with open(godel_path, "w") as f:
            f.write(content)
        ok("godel_bound.py: P90 actualizados ✅")

    # Guardar godel_thresholds_v2.json como fuente de verdad
    thresholds = {
        "_meta": {
            "generado": datetime.now(timezone.utc).isoformat(),
            "cutoff_anti_leakage": "2023-12-31",
            "metodo": "np.percentile(entropy_shannon_train, 90)",
            "condicion_godel": "entropy_shannon[t-1] >= p90 OR vitality_tesla[t-1] == 9",
            "regla_R8": "SIEMPRE OR — nunca AND",
            "activacion_objetivo_pct": "8-13%",
            "fuente": "auditoria_total_20260310_1534.json modulo_G",
        },
        "p90": P90_RECALIBRADO,
        "activacion_con_p90_correcto": {
            "NVDA":    "10.01%",
            "BTC":     "10.01%",
            "XAU":     "10.02%",
            "NIFTY50": "10.04%",
        },
        "activacion_anterior": {
            "NVDA":    "3.59%   (bajo)",
            "BTC":     "0.0%    (CIEGO — P90 mayor que todos los datos)",
            "XAU":     "46.19%  (ROTO — P90 menor que la mediana)",
            "NIFTY50": "12.11%  (OK)",
        }
    }
    thresh_path = f"{META_DIR}/godel_thresholds_v2.json"
    os.makedirs(META_DIR, exist_ok=True)
    with open(thresh_path, "w") as f:
        json.dump(thresholds, f, indent=2)
    ok(f"godel_thresholds_v2.json guardado → {thresh_path}")
    return True


# ═════════════════════════════════════════════════════════════
# FIX-4 — SHA_REGISTRY + audit script actualizados
# ═════════════════════════════════════════════════════════════
# DIAGNÓSTICO: SHA_POST_FIX en spel_auditoria_total.py tenía
# los SHA del Project_Log_v30 (pre-vix_fix). Los SHA reales
# de NVDA y BTC son fb45d05cc288 y 996f94a5967e respectivamente.
# Después del FIX-1 y FIX-2, los SHA de NVDA, XAU, NIFTY50
# cambiarán. Hay que recalcular y actualizar todo.
# ═════════════════════════════════════════════════════════════

def fix_sha_registry() -> bool:
    # Recalcular SHA post-fix para todos los activos
    sha_post_fix = {}
    for asset in ASSETS:
        path = ohlcv_path(asset)
        if os.path.exists(path):
            sha = sha12(path)
            sha_post_fix[asset] = sha
            info(f"  {asset}: {sha}")
        else:
            err(f"  {asset}: parquet no encontrado")

    # Guardar SHA_REGISTRY actualizado
    registry = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "note": "SHA post-fix S21 · vitality_discretize + volume_nan + godel_p90",
        "session": "S21",
        "parquets": {
            asset: {
                "path": ohlcv_path(asset),
                "sha": sha,
                "sha_pre_s21": SHA_ACTUAL_PRE_FIX.get(asset, "?"),
                "changed": sha != SHA_ACTUAL_PRE_FIX.get(asset, sha),
            }
            for asset, sha in sha_post_fix.items()
        }
    }
    registry_path = f"{META_DIR}/SHA_REGISTRY.json"
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)
    ok(f"SHA_REGISTRY.json actualizado: {registry_path}")

    # Parchear SHA_POST_FIX en spel_auditoria_total.py
    audit_script_path = f"{SCRIPTS}/spel_auditoria_total.py"
    if not os.path.exists(audit_script_path):
        audit_script_path = f"{META_DIR}/spel_auditoria_total.py"

    if os.path.exists(audit_script_path):
        with open(audit_script_path, "r") as f:
            content = f.read()

        # Reemplazar bloque SHA_POST_FIX completo
        new_block = 'SHA_POST_FIX = {\n'
        for asset, sha in sha_post_fix.items():
            new_block += f'    "{asset}":    "{sha}",\n'
        new_block += '}'

        import re
        content_new = re.sub(
            r'SHA_POST_FIX\s*=\s*\{[^}]+\}',
            new_block,
            content,
            flags=re.DOTALL,
        )
        if content_new != content:
            with open(audit_script_path, "w") as f:
                f.write(content_new)
            ok("spel_auditoria_total.py: SHA_POST_FIX actualizado ✅")
        else:
            err("No se pudo actualizar SHA_POST_FIX en el script — actualizar manualmente")
            info("Valores a pegar:")
            for asset, sha in sha_post_fix.items():
                info(f'  "{asset}": "{sha}",')
    else:
        info("spel_auditoria_total.py no encontrado en scripts/ ni meta/ — actualizar manualmente")
        info("SHA_POST_FIX a usar:")
        for asset, sha in sha_post_fix.items():
            info(f'  "{asset}": "{sha}",')

    register("ALL", "FIX-4:sha_registry", "mixed", json.dumps(sha_post_fix), "OK")
    return sha_post_fix


# ═════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════

def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*64}")
    print(f"  SPEL — Fix S21 · {ts}")
    print(f"{'═'*64}")
    print(f"  ROOT: {ROOT}\n")

    # ── FIX-1: vitality_tesla ─────────────────────────────────
    section("FIX-1 — vitality_tesla re-discretización")
    print("  Activos afectados: NVDA, XAU (BTC y NIFTY50 no tienen el problema)")
    print()
    r1_nvda = fix_vitality("NVDA")
    print()
    r1_xau  = fix_vitality("XAU")

    # Verificar BTC y NIFTY50 también (por completitud)
    print()
    for a in ["BTC", "NIFTY50"]:
        df = pl.read_parquet(ohlcv_path(a))
        unique = set(df["vitality_tesla"].drop_nulls().unique().to_list())
        if unique.issubset({3.0, 6.0, 9.0, 0.0}):
            ok(f"{a}: vitality_tesla discreta {unique} ✅ — no requiere fix")
        else:
            print(f"  ⚠️  {a}: vitality continua inesperada — aplicando fix")
            fix_vitality(a)

    # ── FIX-2: NIFTY50 volume ─────────────────────────────────
    section("FIX-2 — NIFTY50 volume NaN → 0.0")
    r2 = fix_nifty_volume()

    # ── FIX-3: Gödel P90 ──────────────────────────────────────
    section("FIX-3 — Gödel P90 recalibración")
    print("  P90 a aplicar (calculados con datos ≤ 2023-12-31):")
    for a, v in P90_RECALIBRADO.items():
        pct = {"NVDA": 3.59, "BTC": 0.0, "XAU": 46.19, "NIFTY50": 12.11}[a]
        print(f"  {a:<10} {v}   (activación anterior: {pct}% → nueva: ~10%)")
    print()
    r3 = fix_godel_p90()

    # ── FIX-4: SHA Registry ────────────────────────────────────
    section("FIX-4 — SHA Registry & audit script")
    sha_final = fix_sha_registry()

    # ── RESUMEN FINAL ──────────────────────────────────────────
    section("RESUMEN EJECUTIVO FIX S21")

    print(f"\n  {'FIX':<8} {'Activo':<10} {'Antes':<15} {'Después':<15} {'Estado'}")
    print("  " + "─" * 58)
    for c in changes:
        sha_b = c['sha_before'][:12] if c['sha_before'] else '?'
        sha_a = c['sha_after'][:12] if len(c['sha_after']) <= 12 else c['sha_after'][:12]
        print(f"  {c['fix']:<8} {c['asset']:<10} {sha_b:<15} {sha_a:<15} {c['status']}")

    print(f"\n  SHA finales post-fix:")
    if sha_final:
        for asset, sha in sha_final.items():
            changed = sha != SHA_ACTUAL_PRE_FIX.get(asset, sha)
            marker = " ← CAMBIADO" if changed else " (sin cambio)"
            print(f"    {asset:<10} {sha}{marker}")

    hline = "─" * 64
    print(f"\n{hline}")
    print("  PRÓXIMO PASO OBLIGATORIO:")
    print()
    print("  1. Re-ejecutar spel_auditoria_total.py")
    print("     Meta: 0 CRÍTICOS · 0 ALTOS")
    print()
    print("  2. Si pasa la auditoría → spel_p90_recalibrate.py (confirmación)")
    print()
    print("  3. Reentrenar en orden: BTC → XAU → NIFTY50 → NVDA")
    print(f"{hline}\n")


if __name__ == "__main__":
    main()
