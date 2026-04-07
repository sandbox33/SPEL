"""
SPEL — Auditoría Post-Entrenamiento (Paso 3 / S24)
Corre DESPUÉS de entrenar los 4 activos core.
Verifica que cada checkpoint es válido antes de declarar el entrenamiento listo.

Uso en Colab:
    !python spel_posttraining_audit.py
    # o para un activo específico:
    !python spel_posttraining_audit.py --asset BTC
"""

import os, sys, json, hashlib, argparse
from datetime import datetime
from pathlib import Path

# ── CONFIG ───────────────────────────────────────────────────────────────────
DRIVE       = '/content/drive/MyDrive'
ROOT        = f'{DRIVE}/SPEL 3.0'
CHECKPOINTS = f'{ROOT}/checkpoints'
META        = f'{ROOT}/meta'
DATA_LAKE   = f'{ROOT}/data_lake'

SHA_REGISTRY_PATH = f'{META}/SHA_REGISTRY.json'

ASSETS_CORE = ['BTC', 'XAU', 'NIFTY50', 'NVDA']  # orden de entrenamiento

# Gates de calidad — no negociables
GATES = {
    'val_dir_min':        0.52,   # mínimo val_dir aceptable
    'godel_coverage_min': 0.30,   # mínimo activación Gödel en val set
    'godel_coverage_max': 0.48,   # máximo activación Gödel en val set
    'checkpoint_max_age_days': 7, # checkpoint más viejo que esto → warning
}

CHECKPOINT_REQUIRED_KEYS = {
    'model_state_dict': "pesos del modelo",
    'scaler':           "normalizador (necesario para inferencia)",
    'sha_parquet':      "SHA del parquet usado (trazabilidad)",
    'p90_usado':        "P90 con que se entrenó (Gödel config)",
    'val_dir':          "dirección accuracy en validación",
    'val_loss':         "loss en validación",
    'fecha':            "fecha del entrenamiento",
    'asset':            "activo entrenado",
}

# ── UTILIDADES ───────────────────────────────────────────────────────────────

def sha12(path): return hashlib.sha256(open(path,'rb').read()).hexdigest()[:12]
def ok(msg):    print(f"  ✅  {msg}")
def warn(msg):  print(f"  ⚠️   {msg}")
def fail(msg):  print(f"  ❌  {msg}")
def info(msg):  print(f"  ℹ️   {msg}")
def head(msg):  print(f"\n  {'─'*55}\n  {msg}\n  {'─'*55}")


def find_checkpoint(asset: str) -> str:
    """Busca el checkpoint más reciente para un activo."""
    if not os.path.exists(CHECKPOINTS):
        return None

    candidates = []
    for f in os.listdir(CHECKPOINTS):
        full = os.path.join(CHECKPOINTS, f)
        if asset.upper() in f.upper() and (f.endswith('.pt') or f.endswith('.pth')):
            candidates.append((os.path.getmtime(full), full))

    if not candidates:
        return None
    # Retornar el más reciente
    return sorted(candidates, reverse=True)[0][1]


def audit_checkpoint(asset: str, ckpt_path: str) -> dict:
    """Audita un checkpoint individual. Retorna dict con resultados."""
    result = {
        'asset':    asset,
        'path':     ckpt_path,
        'bugs':     [],
        'warnings': [],
        'passed':   False,
    }

    try:
        import torch
    except ImportError:
        result['bugs'].append("torch no disponible — instalar con: pip install torch")
        return result

    # Cargar checkpoint
    try:
        ckpt = torch.load(ckpt_path, map_location='cpu')
    except Exception as e:
        result['bugs'].append(f"No se pudo cargar el checkpoint: {e}")
        return result

    if not isinstance(ckpt, dict):
        result['bugs'].append(
            f"Checkpoint no es un dict (es {type(ckpt).__name__}). "
            "El trainer guardó solo los pesos sin metadata. "
            "Necesita: {'model_state_dict': ..., 'scaler': ..., 'sha_parquet': ..., ...}"
        )
        return result

    # CHECK 1 — Keys requeridas
    for key, description in CHECKPOINT_REQUIRED_KEYS.items():
        if key not in ckpt:
            severity = 'BUG' if key in ('model_state_dict', 'scaler', 'sha_parquet') else 'WARN'
            msg = f"Key faltante '{key}' ({description})"
            if severity == 'BUG':
                result['bugs'].append(msg)
            else:
                result['warnings'].append(msg)

    # CHECK 2 — val_dir supera el gate
    val_dir = ckpt.get('val_dir')
    if val_dir is None:
        result['warnings'].append("val_dir no registrado en checkpoint")
    elif val_dir < GATES['val_dir_min']:
        result['bugs'].append(
            f"val_dir={val_dir:.1%} < gate mínimo {GATES['val_dir_min']:.1%}. "
            "El modelo no tiene edge suficiente. Causas probables: "
            "(1) lookahead en normalización, "
            "(2) features incorrectas en el tensor, "
            "(3) split temporal mal configurado. "
            "NO activar este checkpoint."
        )
    else:
        ok(f"val_dir={val_dir:.1%} ✅ (gate: >{GATES['val_dir_min']:.1%})")

    # CHECK 3 — Gödel coverage en val set
    godel_cov = ckpt.get('godel_coverage_val')
    if godel_cov is None:
        result['warnings'].append(
            "godel_coverage_val no registrado. "
            "El trainer no calculó cuántos días Gödel estuvo activo en val. "
            "Añadir este campo al checkpoint en el próximo entrenamiento."
        )
    elif not (GATES['godel_coverage_min'] <= godel_cov <= GATES['godel_coverage_max']):
        result['bugs'].append(
            f"godel_coverage_val={godel_cov:.1%} fuera de rango "
            f"[{GATES['godel_coverage_min']:.0%}, {GATES['godel_coverage_max']:.0%}]. "
            f"{'Muy bajo → P90 no se aplica correctamente.' if godel_cov < GATES['godel_coverage_min'] else 'Muy alto → P90 demasiado bajo, recalibrar.'}"
        )
    else:
        ok(f"godel_coverage_val={godel_cov:.1%} ✅ (rango: 30-48%)")

    # CHECK 4 — SHA del parquet coincide con el registry actual
    sha_reg = json.load(open(SHA_REGISTRY_PATH))
    sha_in_ckpt = ckpt.get('sha_parquet', 'MISSING')
    sha_current = sha_reg.get(asset, {}).get('sha_v5', 'NOT_IN_REGISTRY')

    if sha_in_ckpt == 'MISSING':
        result['warnings'].append("sha_parquet no en el checkpoint — no hay trazabilidad")
    elif sha_in_ckpt != sha_current:
        result['warnings'].append(
            f"SHA en checkpoint ({sha_in_ckpt}) != SHA actual del parquet ({sha_current}). "
            "El parquet cambió desde el entrenamiento. "
            "Si el cambio fue intencional (más datos), reentrenar. "
            "Si no fue intencional, investigar por qué cambió el SHA."
        )
    else:
        ok(f"SHA parquet: {sha_in_ckpt} coincide con registry ✅")

    # CHECK 5 — El scaler existe y tiene los atributos esperados
    scaler = ckpt.get('scaler')
    if scaler is not None:
        has_mean  = hasattr(scaler, 'mean_')
        has_scale = hasattr(scaler, 'scale_')
        if not (has_mean and has_scale):
            result['bugs'].append(
                "El scaler en el checkpoint no tiene mean_/scale_ — "
                "fue guardado antes de hacer .fit(). "
                "El scaler debe ser guardado DESPUÉS de fit(X_train)."
            )
        else:
            n_features = len(scaler.mean_)
            if n_features != 20:
                result['warnings'].append(
                    f"Scaler tiene {n_features} features (esperadas 20 — R13). "
                    f"Verificar que el trainer usa exactamente 20 features en el tensor."
                )
            else:
                ok(f"Scaler: 20 features, fit() aplicado ✅")
    else:
        result['bugs'].append(
            "Scaler es None en el checkpoint. "
            "Inferencia normaliza sin los parámetros del training → predicciones incorrectas."
        )

    # CHECK 6 — Fecha del checkpoint no es muy antigua
    fecha_str = ckpt.get('fecha')
    if fecha_str:
        try:
            fecha = datetime.fromisoformat(fecha_str)
            age_days = (datetime.now() - fecha).days
            if age_days > GATES['checkpoint_max_age_days']:
                result['warnings'].append(
                    f"Checkpoint tiene {age_days} días de antigüedad. "
                    "Si los parquets se actualizaron desde entonces, reentrenar."
                )
            else:
                ok(f"Fecha: {fecha_str} ({age_days} días)")
        except Exception:
            result['warnings'].append(f"Fecha en formato no parseable: {fecha_str}")

    # CHECK 7 — P90 usado coincide con el registry
    p90_ckpt = ckpt.get('p90_usado')
    p90_reg  = sha_reg.get(asset, {}).get('p90_entropy')
    if p90_ckpt and p90_reg:
        diff = abs(p90_ckpt - p90_reg)
        if diff > 0.01:
            result['warnings'].append(
                f"P90 en checkpoint ({p90_ckpt:.4f}) difiere del registry ({p90_reg:.4f}). "
                "Δ={diff:.4f}. Verificar si hubo recalibración entre train y ahora."
            )
        else:
            ok(f"P90: {p90_ckpt:.4f} ✅ (registry: {p90_reg:.4f})")

    result['passed'] = len(result['bugs']) == 0
    return result


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SPEL Post-Training Audit S24')
    parser.add_argument('--asset', type=str, default=None,
                        help='Auditar solo un activo (ej: BTC)')
    args = parser.parse_args()

    assets = [args.asset.upper()] if args.asset else ASSETS_CORE

    print("\n" + "═"*60)
    print("  SPEL — Auditoría Post-Entrenamiento (Paso 3 / S24)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("═"*60)

    all_results = []

    for asset in assets:
        head(f"Activo: {asset}")

        ckpt_path = find_checkpoint(asset)
        if not ckpt_path:
            fail(f"Checkpoint no encontrado para {asset} en {CHECKPOINTS}/")
            all_results.append({'asset': asset, 'passed': False,
                                 'bugs': ['Checkpoint no encontrado'], 'warnings': []})
            continue

        info(f"Checkpoint: {os.path.basename(ckpt_path)}")
        result = audit_checkpoint(asset, ckpt_path)
        all_results.append(result)

        for bug  in result['bugs']:    fail(bug)
        for warn_ in result['warnings']: warn(warn_)
        if result['passed']:
            ok(f"{asset}: CHECKPOINT VÁLIDO ✅")

    # ── TABLA RESUMEN ─────────────────────────────────────────────────────
    print("\n" + "═"*60)
    print("  TABLA RESUMEN DE CHECKPOINTS")
    print("═"*60)
    print(f"  {'Activo':<10} {'Estado':<15} {'val_dir':<10} {'SHA match':<12} {'Bugs'}")
    print(f"  {'─'*8} {'─'*13} {'─'*8} {'─'*10} {'─'*5}")

    all_passed = True
    for r in all_results:
        asset = r['asset']
        status = "✅ LISTO" if r['passed'] else "❌ CON BUGS"
        n_bugs = len(r['bugs'])
        if not r['passed']:
            all_passed = False

        # Intentar extraer val_dir del checkpoint para la tabla
        val_dir_str = "?"
        sha_str     = "?"
        try:
            import torch
            ckpt_path = find_checkpoint(asset)
            if ckpt_path:
                ckpt = torch.load(ckpt_path, map_location='cpu')
                if isinstance(ckpt, dict):
                    vd = ckpt.get('val_dir')
                    val_dir_str = f"{vd:.1%}" if vd else "?"
                    sha_ckpt = ckpt.get('sha_parquet', '?')
                    sha_reg  = json.load(open(SHA_REGISTRY_PATH)).get(asset,{}).get('sha_v5','?')
                    sha_str  = "✅ match" if sha_ckpt == sha_reg else "❌ diff"
        except Exception:
            pass

        print(f"  {asset:<10} {status:<15} {val_dir_str:<10} {sha_str:<12} {n_bugs}")

    print()
    if all_passed:
        print("  ✅  TODOS LOS CHECKPOINTS VÁLIDOS")
        print("  Próximo paso: construir spel_score_engine.py (Paso 6)\n")
    else:
        print("  ⛔  HAY CHECKPOINTS CON BUGS")
        print("  Resolver bugs antes de activar en producción.")
        print("  Regla R23: un fix a la vez, verificar antes del siguiente.\n")

    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
