"""
SPEL — Limpieza de Drive (Paso 1 / S24)
Borra solo lo que está confirmado como obsoleto.
Verifica SHA_REGISTRY y los 4 parquets core ANTES de borrar.
Si cualquier verificación falla → ABORT, no borra nada.

Uso en Colab:
    # Modo dry-run (muestra qué borraría sin borrar):
    !python spel_drive_cleanup.py --dry-run

    # Modo real (borra después de verificar):
    !python spel_drive_cleanup.py
"""

import os, sys, json, hashlib, shutil, argparse
from pathlib import Path
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────────────────────
DRIVE     = '/content/drive/MyDrive'
ROOT      = f'{DRIVE}/SPEL 3.0'
META      = f'{ROOT}/meta'
DATA_LAKE = f'{ROOT}/data_lake'

SHA_REGISTRY_PATH = f'{META}/SHA_REGISTRY.json'

ASSETS_CORE = ['NVDA', 'BTC', 'XAU', 'NIFTY50']

# ── LO QUE SE BORRA (confirmado obsoleto post-S23) ──────────────────────────
TO_DELETE = [
    {
        'path':   f'{DRIVE}/SPEL-v1.1',
        'reason': 'DEPRECADA desde S20. Todo lo útil fue migrado a SPEL 3.0.',
    },
    {
        'path':   f'{DRIVE}/_SPEL_CUARENTENA',
        'reason': 'Contenedor de rescate usado en S20. Ya no sirve.',
    },
    {
        'path':   f'{DRIVE}/SPEL_PROD',
        'reason': 'Raíz antigua contaminada con BUG-LA-01 y otros.',
    },
    {
        'path':   f'{DRIVE}/spel_root',
        'reason': 'Runtime de sesiones anteriores. Se regenera en cada sesión Colab.',
    },
    # Nota: NO borramos ROOT/checkpoints aquí — puede tener checkpoints válidos.
    # Los checkpoints se evalúan por separado.
]

# ── LO QUE DEBE EXISTIR (verificación antes de borrar) ──────────────────────
MUST_EXIST_BEFORE_DELETE = [
    f'{SHA_REGISTRY_PATH}',
    f'{DATA_LAKE}/NVDA/ohlcv/aggregated/NVDA_ohlcv_v5.parquet',
    f'{DATA_LAKE}/BTC/ohlcv/aggregated/BTC_ohlcv_v5.parquet',
    f'{DATA_LAKE}/XAU/ohlcv/aggregated/XAU_ohlcv_v5.parquet',
    f'{DATA_LAKE}/NIFTY50/ohlcv/aggregated/NIFTY50_ohlcv_v5.parquet',
    f'{ROOT}/codigo/core/spel_math_engine.py',
    f'{ROOT}/codigo/core/gdelt_foundation.py',
    f'{ROOT}/codigo/core/critical_loss_optimized.py',
    f'{ROOT}/codigo/core/godel_bound.py',
]

# ── UTILIDADES ───────────────────────────────────────────────────────────────

def sha12(path: str) -> str:
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]

def size_mb(path: str) -> float:
    total = 0
    if os.path.isfile(path):
        return os.path.getsize(path) / 1_048_576
    for dirpath, _, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total / 1_048_576

def ok(msg):   print(f"  ✅  {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def fail(msg): print(f"  ❌  {msg}")
def info(msg): print(f"  ℹ️   {msg}")

# ── VERIFICACIÓN PRE-DELETE ──────────────────────────────────────────────────

def verify_before_delete() -> bool:
    """Retorna True solo si es seguro proceder con la limpieza."""
    print("\n  ── Verificación pre-delete ──")

    # 1. Los archivos core deben existir
    all_ok = True
    for path in MUST_EXIST_BEFORE_DELETE:
        if os.path.exists(path):
            ok(f"Existe: {os.path.basename(path)}")
        else:
            fail(f"FALTA: {path}")
            all_ok = False

    if not all_ok:
        fail("Archivos core faltantes — ABORT. No se borra nada.")
        return False

    # 2. SHA_REGISTRY no está vacío
    try:
        reg = json.load(open(SHA_REGISTRY_PATH))
        if not reg:
            fail("SHA_REGISTRY.json está vacío — ABORT")
            return False
        ok(f"SHA_REGISTRY.json: {len(reg)} activos registrados")
    except Exception as e:
        fail(f"No se pudo leer SHA_REGISTRY.json: {e} — ABORT")
        return False

    # 3. SHA de los 4 parquets core coincide con el registry
    sha_errors = []
    for asset in ASSETS_CORE:
        path     = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        actual   = sha12(path)
        expected = reg.get(asset, {}).get('sha_v5', 'MISSING')
        if actual == expected:
            ok(f"SHA {asset}: {actual} ✅")
        else:
            sha_errors.append(f"{asset}: actual={actual} esperado={expected}")
            fail(f"SHA mismatch {asset}: actual={actual} esperado={expected}")

    if sha_errors:
        fail("SHA mismatch detectado — ABORT. Resolver antes de limpiar.")
        return False

    # 4. Confirmar que el contenido de TO_DELETE no es necesario
    print("\n  ── Confirmando que lo que se borrará es realmente obsoleto ──")
    for item in TO_DELETE:
        path = item['path']
        if not os.path.exists(path):
            info(f"Ya no existe: {path}")
            continue

        # Verificar que ningún parquet core está dentro de lo que se va a borrar
        for asset in ASSETS_CORE:
            core_parquet = f'/{asset}_ohlcv_v5.parquet'
            if os.path.exists(path) and os.path.isdir(path):
                for root_dir, dirs, files in os.walk(path):
                    for f in files:
                        if asset in f and 'ohlcv_v5' in f:
                            fail(f"PARQUET CORE ENCONTRADO en ruta a borrar: "
                                 f"{os.path.join(root_dir, f)}")
                            fail("ABORT — verificar que SPEL 3.0 tiene este parquet antes de borrar")
                            return False

        mb = size_mb(path)
        info(f"A borrar: {path} ({mb:.1f} MB) — {item['reason']}")

    ok("Verificación completa — seguro proceder")
    return True


# ── LIMPIEZA ─────────────────────────────────────────────────────────────────

def cleanup(dry_run: bool = True):
    print("\n" + "═"*60)
    print("  SPEL — Limpieza de Drive (Paso 1 / S24)")
    print(f"  Modo: {'DRY-RUN (no borra nada)' if dry_run else 'REAL (borrará archivos)'}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("═"*60)

    # Verificar antes de borrar
    if not verify_before_delete():
        print("\n  ⛔  ABORT: verificación falló. No se borró nada.\n")
        sys.exit(1)

    # Ejecutar limpieza
    print("\n  ── Limpieza ──")
    total_mb_freed = 0.0
    deleted = []
    skipped = []

    for item in TO_DELETE:
        path = item['path']

        if not os.path.exists(path):
            info(f"Ya no existe (limpio): {path}")
            skipped.append(path)
            continue

        mb = size_mb(path)

        if dry_run:
            info(f"[DRY-RUN] Borraría: {path} ({mb:.1f} MB)")
            total_mb_freed += mb
        else:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                ok(f"Borrado: {path} ({mb:.1f} MB liberados)")
                total_mb_freed += mb
                deleted.append(path)
            except Exception as e:
                warn(f"Error al borrar {path}: {e}")

    # Verificar SPEL 3.0 después de la limpieza
    if not dry_run:
        print("\n  ── Verificación post-delete ──")
        all_ok = True
        for asset in ASSETS_CORE:
            path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
            if os.path.exists(path):
                ok(f"Parquet core intacto: {asset}")
            else:
                fail(f"PARQUET CORE PERDIDO: {asset} — CRÍTICO")
                all_ok = False
        if not all_ok:
            fail("CRÍTICO: parquets core perdidos. Verificar backup en Google Drive Trash.")

    # Resumen
    print("\n" + "═"*60)
    print("  RESUMEN")
    print(f"  Espacio liberado: {total_mb_freed:.1f} MB")
    print(f"  Carpetas borradas: {len(deleted)}")
    print(f"  Ya estaban limpias: {len(skipped)}")
    if dry_run:
        print("\n  ℹ️  Modo DRY-RUN: nada fue borrado.")
        print("  Para ejecutar la limpieza real:")
        print("      !python spel_drive_cleanup.py")
    else:
        print("\n  ✅  Limpieza completada.")
        print("  Próximo paso: ejecutar spel_preflight_s24.py para verificar estado.")
    print("═"*60 + "\n")


def main():
    parser = argparse.ArgumentParser(description='SPEL Drive Cleanup S24')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='Muestra qué se borraría sin borrar nada (default: True si no se pasa flag)')
    # Si no se pasa ningún argumento, hacer dry-run por seguridad
    args = parser.parse_args()

    # Si se ejecuta directamente sin --dry-run, confirmar
    if not args.dry_run:
        print("\n  ⚠️  Estás a punto de borrar archivos de Google Drive.")
        print("  Esto NO se puede deshacer fácilmente (revisa la papelera de Drive).")
        confirm = input("\n  Escribe 'BORRAR' para confirmar: ").strip()
        if confirm != 'BORRAR':
            print("  Cancelado. Nada fue borrado.")
            sys.exit(0)

    cleanup(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
