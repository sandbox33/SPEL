"""
SPEL — Pre-flight S24
Ejecutar al inicio de CADA sesión antes de tocar cualquier archivo.
Si cualquier check falla → STOP. No continuar hasta resolver.

Uso en Colab:
    !python spel_preflight_s24.py
    # o dentro de una celda:
    exec(open('spel_preflight_s24.py').read())
"""

import os, json, hashlib, sys
from datetime import datetime
from pathlib import Path

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
DRIVE       = '/content/drive/MyDrive'
ROOT        = f'{DRIVE}/SPEL-v2.0'
META        = f'{ROOT}/meta'
DATA_LAKE   = f'{ROOT}/data_lake'
SCRIPTS     = f'{ROOT}/scripts'
CHECKPOINTS = f'{ROOT}/checkpoints'

SHA_REGISTRY_PATH = f'{META}/SHA_REGISTRY.json'

ASSETS_CORE = ['NVDA', 'BTC', 'XAU', 'NIFTY50']

# Features documentadas en el tensor (18 confirmadas — las 2 faltantes
# deben hallarse en spel_trainer.py durante la auditoría Paso 2)
TENSOR_FEATURES_KNOWN = [
    'entropy_shannon', 'entropy_decay_lambda', 'entropy_psych_vix',
    'fibonacci_lag_1', 'fibonacci_lag_2', 'fibonacci_lag_3',
    'fibonacci_lag_5', 'fibonacci_lag_8', 'fibonacci_lag_13', 'fibonacci_lag_21',
    'goldstein_geo', 'n_events_ohlcv', 'vitality_tesla',
    'mass_panic_index', 'fear_momentum', 'vix_norm',
    'nash_frozen_7d', 'log_return',
]
TENSOR_INPUT_SIZE = 20  # R13 inamovible

COLS_METADATA  = {'volume_type', 'asset_class', 'trading_session'}
COLS_GDELT_EXT = {'goldstein_mean', 'tone_variance', 'zipf_concentration'}
COLS_NO_TENSOR = COLS_METADATA | COLS_GDELT_EXT  # estas 6 NO entran al tensor

SCHEMA_V51_NCOLS = 30

GODEL_ACTIVATION_MIN = 0.30
GODEL_ACTIVATION_MAX = 0.48

# ── UTILIDADES ───────────────────────────────────────────────────────────────

def sha12(path: str) -> str:
    """file-level SHA-256[:12] — método estándar único SPEL."""
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]

def ok(msg):   print(f"  ✅  {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def fail(msg): print(f"  ❌  {msg}")
def head(msg): print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")

# ── CHECKS ───────────────────────────────────────────────────────────────────

results = {'passed': 0, 'warned': 0, 'failed': 0, 'bugs': []}

def check(name, fn):
    try:
        status, detail = fn()
        if status == 'OK':
            ok(f"{name}: {detail}")
            results['passed'] += 1
        elif status == 'WARN':
            warn(f"{name}: {detail}")
            results['warned'] += 1
        else:
            fail(f"{name}: {detail}")
            results['failed'] += 1
            results['bugs'].append({'check': name, 'detail': detail})
    except Exception as e:
        fail(f"{name}: EXCEPCIÓN — {e}")
        results['failed'] += 1
        results['bugs'].append({'check': name, 'detail': str(e)})


# CHECK 1 — SHA_REGISTRY existe y no está vacío
def c_registry_exists():
    if not os.path.exists(SHA_REGISTRY_PATH):
        return 'FAIL', f"No encontrado: {SHA_REGISTRY_PATH}"
    reg = json.load(open(SHA_REGISTRY_PATH))
    if not reg:
        return 'FAIL', "SHA_REGISTRY.json está vacío — BUG-SHA-REGISTRY-VACIO activo"
    missing = [a for a in ASSETS_CORE if a not in reg]
    if missing:
        return 'FAIL', f"Activos faltantes en registry: {missing}"
    return 'OK', f"{len(reg)} activos registrados"


# CHECK 2 — SHA de los 4 parquets core coincide con el registry
def c_sha_parquets():
    try:
        reg = json.load(open(SHA_REGISTRY_PATH))
    except Exception as e:
        return 'FAIL', f"No se pudo leer SHA_REGISTRY: {e}"

    mismatches = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        if not os.path.exists(path):
            mismatches.append(f"{asset}: archivo no encontrado")
            continue
        actual   = sha12(path)
        expected = reg.get(asset, {}).get('sha_v5', 'MISSING')
        if actual != expected:
            mismatches.append(f"{asset}: actual={actual} esperado={expected}")

    if mismatches:
        return 'FAIL', "SHA mismatch: " + " | ".join(mismatches)
    return 'OK', "4/4 SHAs verificados"


# CHECK 3 — Schema v5.1 (30 cols) en todos los parquets
def c_schema():
    try:
        import polars as pl
    except ImportError:
        return 'WARN', "polars no disponible — instalar con: pip install polars"

    errors = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        if not os.path.exists(path):
            errors.append(f"{asset}: archivo no encontrado")
            continue
        df = pl.read_parquet(path)
        ncols = len(df.columns)
        if ncols != SCHEMA_V51_NCOLS:
            errors.append(f"{asset}: {ncols} cols (esperadas {SCHEMA_V51_NCOLS})")
        # Verificar que metadata no entró al tensor accidentalmente
        for col in COLS_NO_TENSOR:
            if col not in df.columns:
                errors.append(f"{asset}: falta col '{col}' (debe estar en v5.1)")
        # Verificar features conocidas del tensor
        for col in TENSOR_FEATURES_KNOWN:
            if col not in df.columns:
                errors.append(f"{asset}: falta feature de tensor '{col}'")

    if errors:
        return 'FAIL', " | ".join(errors)
    return 'OK', f"4/4 parquets con {SCHEMA_V51_NCOLS} cols y features verificadas"


# CHECK 4 — date dtype datetime[ms, UTC] en todos los parquets
def c_date_dtype():
    try:
        import polars as pl
    except ImportError:
        return 'WARN', "polars no disponible"

    errors = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        if not os.path.exists(path):
            continue
        df   = pl.read_parquet(path)
        dtype = str(df['date'].dtype)
        if 'Datetime' not in dtype or 'UTC' not in dtype:
            errors.append(f"{asset}: date dtype={dtype} (debe ser Datetime ms UTC)")

    if errors:
        return 'FAIL', " | ".join(errors)  # EF-02
    return 'OK', "4/4 parquets con date=datetime[ms,UTC]"


# CHECK 5 — Nulls en features del tensor
def c_nulls_tensor():
    try:
        import polars as pl
    except ImportError:
        return 'WARN', "polars no disponible"

    errors = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        if not os.path.exists(path):
            continue
        df = pl.read_parquet(path)
        for col in TENSOR_FEATURES_KNOWN:
            if col not in df.columns:
                continue
            nulls = df[col].null_count()
            if nulls > 0:
                errors.append(f"{asset}.{col}: {nulls} nulls")

    if errors:
        return 'FAIL', "Nulls en features del tensor: " + " | ".join(errors)
    return 'OK', "0 nulls en features conocidas del tensor"


# CHECK 6 — P90 entropy dentro de rangos válidos
def c_p90_range():
    try:
        reg = json.load(open(SHA_REGISTRY_PATH))
    except Exception:
        return 'FAIL', "No se pudo leer SHA_REGISTRY"

    P90_BOUNDS = {'min': 1.10, 'max': 1.50}
    errors = []
    for asset in ASSETS_CORE:
        p90 = reg.get(asset, {}).get('p90_entropy')
        if p90 is None:
            errors.append(f"{asset}: p90_entropy=None")
            continue
        if not (P90_BOUNDS['min'] <= p90 <= P90_BOUNDS['max']):
            errors.append(f"{asset}: p90={p90:.4f} fuera de [{P90_BOUNDS['min']},{P90_BOUNDS['max']}]")

    if errors:
        return 'FAIL', " | ".join(errors)
    return 'OK', f"P90s en rango [{P90_BOUNDS['min']},{P90_BOUNDS['max']}]: " + \
                  " | ".join(f"{a}={json.load(open(SHA_REGISTRY_PATH))[a]['p90_entropy']:.4f}"
                             for a in ASSETS_CORE)


# CHECK 7 — Activación Gödel OR en rango 30-48%
def c_godel_activation():
    try:
        import polars as pl
        reg = json.load(open(SHA_REGISTRY_PATH))
    except Exception as e:
        return 'WARN', f"No se pudo verificar: {e}"

    issues = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        if not os.path.exists(path):
            continue
        df  = pl.read_parquet(path)
        p90 = reg.get(asset, {}).get('p90_entropy', 9999)

        if 'entropy_shannon' not in df.columns or 'vitality_tesla' not in df.columns:
            issues.append(f"{asset}: columnas faltantes")
            continue

        n          = len(df)
        n_entropy  = (df['entropy_shannon'] >= p90).sum()
        n_vitality = (df['vitality_tesla'] == 9).sum()
        # OR = A + B - A∩B
        n_both     = ((df['entropy_shannon'] >= p90) & (df['vitality_tesla'] == 9)).sum()
        n_godel    = n_entropy + n_vitality - n_both
        activation = n_godel / n

        if not (GODEL_ACTIVATION_MIN <= activation <= GODEL_ACTIVATION_MAX):
            issues.append(
                f"{asset}: activación={activation:.1%} "
                f"(fuera de [{GODEL_ACTIVATION_MIN:.0%},{GODEL_ACTIVATION_MAX:.0%}])"
            )

    if issues:
        return 'WARN', "Activaciones fuera de rango sano: " + " | ".join(issues)
    return 'OK', "Todas las activaciones Gödel en rango 30-48%"


# CHECK 8 — Parquets GDELT existen para los 4 activos core
def c_gdelt_exists():
    missing = []
    for asset in ASSETS_CORE:
        path = f'{DATA_LAKE}/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet'
        if not os.path.exists(path):
            missing.append(asset)

    if missing:
        return 'FAIL', f"GDELT faltante para: {missing}"
    return 'OK', "GDELT presente para 4/4 activos"


# CHECK 9 — No hay parquets v1.1 ni versiones antiguas en rutas activas
def c_no_legacy():
    legacy_paths = [
        f'{DRIVE}/SPEL-v1.1',
        f'{DRIVE}/_SPEL_CUARENTENA',
        f'{DRIVE}/SPEL_PROD',
    ]
    found = [p for p in legacy_paths if os.path.exists(p)]

    if found:
        return 'WARN', f"Legacy todavía existe (borrar en Paso 1): {found}"
    return 'OK', "Sin archivos legacy en Drive"


# CHECK 10 — SHA_REGISTRY versión única de hashing (file-level)
def c_hash_method():
    """
    Verifica que no haya scripts usando el método DataFrame-level antiguo.
    BUG-SHA-DOS-METODOS cerrado en S23 — verificar que no regresó.
    """
    scripts_to_check = [
        f'{SCRIPTS}/spel_p90_recalibrate.py',
        f'{SCRIPTS}/spel_harvester_v3.py',
        f'{SCRIPTS}/spel_auditoria_total.py',
    ]
    suspicious = []
    for script in scripts_to_check:
        if not os.path.exists(script):
            continue
        content = open(script).read()
        # El método incorrecto hashea un DataFrame serializado, no el archivo
        # Señales de método incorrecto: hashlib sobre to_pandas(), to_csv(), write_csv()
        bad_patterns = [
            'to_pandas().to_csv',
            'write_csv',
            '.encode()',
            'hashlib.sha256(df',
            'sha256(str(',
        ]
        for pat in bad_patterns:
            if pat in content:
                suspicious.append(f"{os.path.basename(script)}: patrón '{pat}'")

    if suspicious:
        return 'FAIL', "Posible método de hash incorrecto: " + " | ".join(suspicious)
    return 'OK', "Método file-level SHA verificado en scripts auditados"


# CHECK 11 — Tensor de 20 features: BUG-TENSOR-DOC
def c_tensor_doc():
    """
    Alerta sobre la inconsistencia documentada: el log dice 20 pero lista 18.
    No es un error del sistema — es un BUG de documentación que debe resolverse
    leyendo spel_trainer.py en el Paso 2.
    """
    trainer_path = f'{SCRIPTS}/spel_trainer.py'
    if not os.path.exists(trainer_path):
        return 'WARN', (
            "BUG-TENSOR-DOC activo: el log documenta 18 features pero input_size=20. "
            "spel_trainer.py no encontrado en scripts/ — buscar en codigo/core/. "
            "Resolver en Paso 2 (auditoría del trainer)."
        )

    # Si el trainer existe, intentar contar las features que selecciona
    content = open(trainer_path).read()
    known_count = sum(1 for f in TENSOR_FEATURES_KNOWN if f in content)
    if known_count < 15:
        return 'WARN', (
            f"BUG-TENSOR-DOC: trainer usa {known_count}/18 features conocidas. "
            "Revisar selección de columnas en Paso 2."
        )
    return 'WARN', (
        f"BUG-TENSOR-DOC: log documenta 18 features, input_size=20. "
        f"Las 2 features sin documentar deben identificarse en Paso 2. "
        f"Candidatas: high_norm, low_norm, volume_norm, atr_norm."
    )


# CHECK 12 — godel_bound.py es dinámico (sin P90 hardcodeados)
def c_godel_dynamic():
    godel_path = f'{ROOT}/codigo/core/godel_bound.py'
    if not os.path.exists(godel_path):
        return 'WARN', f"No encontrado en {godel_path} — buscar ruta alternativa"

    content = open(godel_path).read()

    # Buscar P90 hardcodeados (números flotantes asociados a P90 en el rango 1.0-1.5)
    import re
    hardcoded = re.findall(
        r'[Pp]90\s*[=:]\s*(1\.[0-9]{2,})',
        content
    )
    if hardcoded:
        return 'WARN', (
            f"Posibles P90 hardcodeados en godel_bound.py: {hardcoded}. "
            "Verificar que son ejemplos/comentarios y no valores activos."
        )
    return 'OK', "godel_bound.py dinámico — sin P90 hardcodeados detectados"


# CHECK 13 — Checkpoints: estado esperado antes de entrenar
def c_checkpoints():
    if not os.path.exists(CHECKPOINTS):
        return 'WARN', f"Directorio checkpoints no encontrado: {CHECKPOINTS}"

    ckpt_files = [f for f in os.listdir(CHECKPOINTS)
                  if f.endswith('.pt') or f.endswith('.pth') or f.endswith('.pkl')]

    if not ckpt_files:
        return 'OK', "Checkpoints vacío — listo para entrenamiento limpio"

    # Si hay checkpoints, verificar que tienen metadata
    old_or_dirty = []
    for f in ckpt_files:
        full = os.path.join(CHECKPOINTS, f)
        try:
            import torch
            ckpt = torch.load(full, map_location='cpu')
            if not isinstance(ckpt, dict):
                old_or_dirty.append(f"{f}: no es dict (sin metadata)")
                continue
            required_keys = {'model_state_dict', 'sha_parquet', 'val_dir', 'fecha'}
            missing_keys  = required_keys - set(ckpt.keys())
            if missing_keys:
                old_or_dirty.append(f"{f}: faltan keys {missing_keys}")
        except ImportError:
            return 'WARN', f"{len(ckpt_files)} checkpoint(s) encontrados — torch no disponible para inspeccionar"
        except Exception as e:
            old_or_dirty.append(f"{f}: error al leer — {e}")

    if old_or_dirty:
        return 'WARN', "Checkpoints sin metadata completa: " + " | ".join(old_or_dirty)
    return 'OK', f"{len(ckpt_files)} checkpoint(s) con metadata completa"


# ── EJECUCIÓN ────────────────────────────────────────────────────────────────

def run_preflight():
    print("\n" + "═"*60)
    print("  SPEL Pre-flight S24")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("═"*60)

    head("1. INTEGRIDAD DE DATOS")
    check("SHA_REGISTRY existe y no está vacío",   c_registry_exists)
    check("SHA parquets vs registry",              c_sha_parquets)
    check("Schema v5.1 (30 cols)",                 c_schema)
    check("date dtype datetime[ms,UTC]",           c_date_dtype)
    check("Nulls en features del tensor",          c_nulls_tensor)
    check("GDELT presente para activos core",      c_gdelt_exists)

    head("2. CONFIGURACIÓN GÖDEL")
    check("P90 entropy en rangos válidos",         c_p90_range)
    check("Activación Gödel OR en 30-48%",         c_godel_activation)
    check("godel_bound.py dinámico",               c_godel_dynamic)

    head("3. ESTADO DEL SISTEMA")
    check("Sin archivos legacy",                   c_no_legacy)
    check("Método hash file-level en scripts",     c_hash_method)
    check("Tensor features (BUG-TENSOR-DOC)",      c_tensor_doc)
    check("Checkpoints estado",                    c_checkpoints)

    # ── RESUMEN ──────────────────────────────────────────────────────────────
    total = results['passed'] + results['warned'] + results['failed']
    print("\n" + "═"*60)
    print(f"  RESUMEN: {total} checks")
    print(f"  ✅ Pasaron:    {results['passed']}")
    print(f"  ⚠️  Warnings:   {results['warned']}")
    print(f"  ❌ Fallaron:   {results['failed']}")
    print("═"*60)

    if results['failed'] > 0:
        print("\n  BUGS ACTIVOS — resolver antes de continuar:")
        for i, bug in enumerate(results['bugs'], 1):
            print(f"\n  [{i}] {bug['check']}")
            print(f"      {bug['detail']}")
        print("\n  ⛔  ABORT: sistema en estado inconsistente.")
        print("      No entrenar. No modificar parquets. Resolver bugs primero.\n")
        return False

    if results['warned'] > 0:
        print("\n  ⚠️  Hay warnings — revisar antes de continuar.")
        print("  Si los warnings son conocidos (ej: BUG-TENSOR-DOC),")
        print("  documentarlos y continuar con el Paso 2.\n")

    if results['failed'] == 0:
        print("\n  ✅  SISTEMA LISTO PARA CONTINUAR\n")
        return True


if __name__ == '__main__':
    passed = run_preflight()
    sys.exit(0 if passed else 1)
