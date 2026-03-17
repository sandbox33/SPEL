"""
spel_patch_godel.py
Actualiza P90_ENTROPY en godel_bound.py automáticamente.
Lee los valores calculados en S22b desde SHA_REGISTRY.
Sin pasos manuales.
"""
import json, os, re
from datetime import datetime, timezone

ROOT     = "/content/drive/MyDrive/SPEL-v2.0"
META_DIR = f"{ROOT}/meta"

# P90 post-S22b (si SHA_REGISTRY no existe aún, estos son los valores)
P90_FALLBACK = {
    "NVDA":    1.1571,
    "BTC":     1.1709,
    "XAU":     1.3229,
    "NIFTY50": 1.1868,
}

GODEL_CANDIDATES = [
    f"{ROOT}/codigo/core/godel_bound.py",
    "/content/spel_root/godel_bound.py",
    "/content/godel_bound.py",
]

def load_p90_from_registry() -> dict:
    reg_path = f"{META_DIR}/SHA_REGISTRY.json"
    if not os.path.exists(reg_path):
        print("  ⚠️  SHA_REGISTRY no encontrado → usando P90 fallback S22b")
        return P90_FALLBACK
    with open(reg_path) as f:
        reg = json.load(f)
    cal = reg.get("p90_calibrated", {})
    if not cal:
        print("  ⚠️  p90_calibrated no encontrado en registry → usando fallback")
        return P90_FALLBACK
    return {asset: d["p90"] for asset, d in cal.items()}

def find_godel_file() -> str | None:
    for path in GODEL_CANDIDATES:
        if os.path.exists(path):
            return path
    return None

def patch_godel(p90: dict):
    path = find_godel_file()
    if not path:
        print("  🔴 godel_bound.py no encontrado en rutas conocidas")
        print("     Buscar manualmente y re-ejecutar con ruta correcta")
        print("     O añadir la ruta en GODEL_CANDIDATES al inicio del script")
        _print_manual_block(p90)
        return

    with open(path) as f:
        content = f.read()

    # Construir bloque nuevo
    new_block = "P90_ENTROPY = {\n"
    for asset, val in p90.items():
        new_block += f'    "{asset}": {val},\n'
    new_block += "}"

    # Intentar reemplazar bloque existente
    pattern = r'P90_ENTROPY\s*=\s*\{[^}]+\}'
    if re.search(pattern, content, re.DOTALL):
        new_content = re.sub(pattern, new_block, content, flags=re.DOTALL)
        with open(path, "w") as f:
            f.write(new_content)
        print(f"  ✅ {path}")
        print(f"     P90 actualizado: {p90}")
    else:
        print(f"  ⚠️  Patrón P90_ENTROPY no encontrado en {path}")
        print(f"     Añadir manualmente al archivo:")
        _print_manual_block(p90)

def _print_manual_block(p90: dict):
    print("\n  ── AÑADIR ESTE BLOQUE EN godel_bound.py ──")
    print("  P90_ENTROPY = {")
    for asset, val in p90.items():
        print(f'      "{asset}": {val},')
    print("  }")
    print("  ──────────────────────────────────────────\n")

if __name__ == "__main__":
    print("\n══════════════════════════════════════════")
    print("  SPEL — Patch godel_bound.py P90")
    print("══════════════════════════════════════════\n")
    p90 = load_p90_from_registry()
    print(f"  P90 a aplicar: {p90}")
    patch_godel(p90)
    print("\n  SIGUIENTE: !python /content/spel_fix_s22c.py\n")
