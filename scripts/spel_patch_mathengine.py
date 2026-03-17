"""
spel_patch_mathengine.py
Inyecta el normalizador rolling TE en spel_math_engine.py automáticamente.
Lee parámetros calculados en S22c desde SHA_REGISTRY.
Sin pasos manuales.
"""
import json, os, re
from datetime import datetime, timezone

ROOT     = "/content/drive/MyDrive/SPEL-v2.0"
META_DIR = f"{ROOT}/meta"

MATH_CANDIDATES = [
    f"{ROOT}/codigo/core/spel_math_engine.py",
    "/content/spel_root/spel_math_engine.py",
    "/content/spel_math_engine.py",
]

# Parámetros fallback si SHA_REGISTRY no tiene te_calibration
TE_NORM_FALLBACK = {
    "NVDA":    {"lookback": 63, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-8},
    "BTC":     {"lookback": 21, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-8},
    "XAU":     {"lookback": 63, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-8},
    "NIFTY50": {"lookback": 42, "clip_lower": 0.0, "clip_upper": 2.0, "epsilon": 1e-8},
}

# Código del normalizador a inyectar
NORMALIZER_CODE = '''
# ── SPEL: Normalizador Transfer Entropy rolling (anti-leakage) ──
# Inyectado por spel_patch_mathengine.py · S22c
# ❌ NUNCA usar min/max global — BUG-LA-01 bis
# ✅ Rolling sobre lookback inamovible (R4) ─────────────────────

TE_NORM_PARAMS = {asset_params}

def normalize_transfer_entropy(
    df,
    asset: str,
    te_col: str = "transfer_entropy",
):
    """
    Normaliza Transfer Entropy a [0,1] con rolling MinMax anti-leakage.
    Usa parámetros calibrados en train <= 2023-12-31 (S22c).
    """
    import polars as pl
    p = TE_NORM_PARAMS.get(asset, {
        "lookback": 42, "clip_lower": 0.0,
        "clip_upper": 2.0, "epsilon": 1e-8,
    })
    lb, lo, hi, eps = p["lookback"], p["clip_lower"], p["clip_upper"], p["epsilon"]

    return (
        df.with_columns(
            pl.col(te_col).cast(pl.Float64).clip(lo, hi).alias("_te_c")
        )
        .with_columns(
            pl.col("_te_c").rolling_min(lb, min_periods=1).alias("_te_rmin"),
            pl.col("_te_c").rolling_max(lb, min_periods=1).alias("_te_rmax"),
        )
        .with_columns(
            ((pl.col("_te_c") - pl.col("_te_rmin"))
             / (pl.col("_te_rmax") - pl.col("_te_rmin") + eps))
            .clip(0.0, 1.0)
            .alias(te_col + "_norm")
        )
        .drop(["_te_c", "_te_rmin", "_te_rmax"])
    )

# Router Score de Oro — adaptativo por volume_type
def score_oro(godel: float, transfer_entropy_norm: float,
              volume_profile: float, volume_type: str) -> float:
    """
    Pesos adaptativos según semántica de volumen (R16, R17).
    SYNTHETIC_INDEX / YIELD_INSTRUMENT: Volume Profile = 0%.
    NATIVE_FUTURES / SPOT_CRYPTO:       Volume Profile = 30%.
    TICK_PROXY (forex):                 Volume Profile = 15%.
    """
    if volume_type in ("SYNTHETIC_INDEX", "YIELD_INSTRUMENT"):
        return godel * 0.55 + transfer_entropy_norm * 0.45
    elif volume_type == "TICK_PROXY":
        return volume_profile * 0.15 + godel * 0.45 + transfer_entropy_norm * 0.40
    else:  # NATIVE_FUTURES, SPOT_CRYPTO
        return volume_profile * 0.30 + godel * 0.40 + transfer_entropy_norm * 0.30

# ── FIN SPEL normalizador TE ────────────────────────────────────
'''

def load_te_params() -> dict:
    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    if not os.path.exists(reg_path):
        print("  ⚠️  SHA_REGISTRY no encontrado → usando parámetros fallback")
        return TE_NORM_FALLBACK
    with open(reg_path) as f:
        reg = json.load(f)
    cal = reg.get("te_calibration", {}).get("assets", {})
    if not cal:
        print("  ⚠️  te_calibration no encontrado → usando fallback")
        return TE_NORM_FALLBACK
    result = {}
    for asset, data in cal.items():
        norm = data.get("normalizer", {})
        if norm:
            result[asset] = {
                "lookback":   norm.get("lookback", 42),
                "clip_lower": norm.get("clip_lower", 0.0),
                "clip_upper": norm.get("clip_upper", 2.0),
                "epsilon":    norm.get("epsilon", 1e-8),
            }
    return result or TE_NORM_FALLBACK

def find_math_engine() -> str | None:
    for path in MATH_CANDIDATES:
        if os.path.exists(path):
            return path
    return None

def patch_math_engine(te_params: dict):
    path = find_math_engine()

    # Construir el dict de parámetros como string Python
    params_str = "{\n"
    for asset, p in te_params.items():
        params_str += (f'    "{asset}": {{'
                       f'"lookback": {p["lookback"]}, '
                       f'"clip_lower": {p["clip_lower"]}, '
                       f'"clip_upper": {p["clip_upper"]}, '
                       f'"epsilon": {p["epsilon"]}}},\n')
    params_str += "}"

    code_to_inject = NORMALIZER_CODE.replace("{asset_params}", params_str)

    if not path:
        print("  🔴 spel_math_engine.py no encontrado en rutas conocidas")
        print("     Añadir la ruta en MATH_CANDIDATES al inicio del script")
        print("\n  ── COPIAR ESTE BLOQUE AL INICIO DE spel_math_engine.py ──")
        print(code_to_inject)
        return

    with open(path) as f:
        content = f.read()

    # Si ya fue inyectado, reemplazar
    marker = "# ── SPEL: Normalizador Transfer Entropy rolling (anti-leakage) ──"
    end_marker = "# ── FIN SPEL normalizador TE ────────────────────────────────────"

    if marker in content:
        start_idx = content.find(marker)
        end_idx   = content.find(end_marker)
        if end_idx != -1:
            end_idx += len(end_marker) + 1
            content = content[:start_idx] + code_to_inject + content[end_idx:]
            print(f"  ✅ Reemplazado bloque existente en {path}")
        else:
            print(f"  ⚠️  Marcador fin no encontrado — añadiendo al inicio")
            content = code_to_inject + "\n" + content
    else:
        # Insertar después de los imports (buscar primer def o class)
        insert_at = 0
        for keyword in ["\nclass ", "\ndef ", "\nimport polars"]:
            idx = content.find(keyword)
            if idx != -1:
                insert_at = idx
                break
        content = content[:insert_at] + "\n" + code_to_inject + content[insert_at:]
        print(f"  ✅ Bloque inyectado en {path}")

    with open(path, "w") as f:
        f.write(content)

    print(f"     TE_NORM_PARAMS: {list(te_params.keys())}")
    print(f"     score_oro() router: SYNTHETIC/NATIVE/TICK")

if __name__ == "__main__":
    print("\n══════════════════════════════════════════")
    print("  SPEL — Patch spel_math_engine.py")
    print("══════════════════════════════════════════\n")
    te_params = load_te_params()
    patch_math_engine(te_params)
    print("\n  ✅ Math engine listo")
    print("  SIGUIENTE: !python /content/spel_harvester_v3.py\n")
