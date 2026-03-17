"""
SPEL — Auditor Maestro de MyDrive v2.0
=======================================
Escanea TODO Google Drive buscando datos SPEL en cualquier versión.

Lógica de clasificación:
  PRODUCCIÓN   → /SPEL-v2.0/              Solo fuente de verdad
  DEPRECATED   → /SPEL-v1.1/, /SPEL-v1.0/ etc.  Mover a cuarentena
  YA CUARENTENA→ /_SPEL_CUARENTENA/       Mapear, no tocar
  HUÉRFANO     → parquets SPEL fuera de carpetas conocidas → auditar

Salidas:
  SPEL-v2.0/meta/master_audit_report.json   ← mapa completo MyDrive
  SPEL-v2.0/meta/sha_registry_v2.json       ← SHAs frescos (solo v2.0)
  SPEL-v2.0/CUARENTENA/                     ← archivos malos de v2.0
  _SPEL_CUARENTENA/                         ← deprecated ya existente (solo mapear)

SPEL · 09-Mar-2026
"""

import os
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import polars as pl
import numpy as np

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════

MYDRIVE   = Path('/content/drive/MyDrive')
RAIZ_PROD = MYDRIVE / 'SPEL-v2.0'
CUARENTENA_V2   = RAIZ_PROD / 'CUARENTENA'
CUARENTENA_LEGACY = MYDRIVE / '_SPEL_CUARENTENA'

REPORT_PATH  = RAIZ_PROD / 'meta' / 'master_audit_report.json'
SHA_PATH     = RAIZ_PROD / 'meta' / 'sha_registry_v2.json'

ACTIVOS      = ['NVDA', 'BTC', 'XAU', 'NIFTY50']
CANON_COLS   = 24
CORR_FUT_MAX = 0.05
NAN_MAX_PCT  = 0.10
GAP_MAX_DIAS = 30

# Palabras clave para detectar archivos SPEL en cualquier carpeta
SPEL_KEYWORDS = {'spel', 'nvda', 'btc', 'xau', 'nifty', 'gdelt', 'ohlcv',
                 'entropy', 'godel', 'vitality', 'canonical', 'parquet'}

# Extensiones de datos a auditar
DATA_EXTS = {'.parquet', '.csv', '.zip'}

# Carpetas a IGNORAR completamente (no son datos)
IGNORAR_DIRS = {
    '.git', '__pycache__', 'node_modules', '.ipynb_checkpoints',
    'Trash', '.Trash'
}

# ══════════════════════════════════════════════════════════
# CLASIFICACIÓN DE VERSIONES
# ══════════════════════════════════════════════════════════

def clasificar_version(path: Path) -> str:
    """
    Devuelve la versión/categoría de un archivo según su ubicación.
    PRODUCCION | DEPRECATED | CUARENTENA_LEGACY | HUERFANO
    """
    path_str = str(path)

    if str(RAIZ_PROD) in path_str and 'CUARENTENA' not in path_str:
        return 'PRODUCCION'
    if 'CUARENTENA' in path_str and str(RAIZ_PROD) in path_str:
        return 'CUARENTENA_V2'
    if str(CUARENTENA_LEGACY) in path_str:
        return 'CUARENTENA_LEGACY'
    # Versiones viejas reconocibles
    for token in ['SPEL-v1', 'SPEL_v1', 'SPEL-v0', 'SPEL_PROD',
                  'spel_root_backup', 'spel_old', 'SPEL-backup']:
        if token.lower() in path_str.lower():
            return 'DEPRECATED'
    return 'HUERFANO'


def es_archivo_spel(path: Path) -> bool:
    """True si el archivo probablemente pertenece al ecosistema SPEL."""
    nombre = path.name.lower()
    return any(kw in nombre for kw in SPEL_KEYWORDS)


# ══════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]

def sep(c='─', n=68):
    print(c * n)

def titulo(t):
    sep('═')
    print(f"  {t}")
    sep('═')

def fmt_bytes(b):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} GB"


# ══════════════════════════════════════════════════════════
# DESCUBRIMIENTO
# ══════════════════════════════════════════════════════════

def descubrir_mydrive(raiz: Path) -> list[Path]:
    """
    Camina MyDrive y devuelve todos los archivos de datos.
    Filtra carpetas irrelevantes para no tardar horas.
    """
    archivos = []

    for dirpath, dirnames, filenames in os.walk(str(raiz)):
        # Podar directorios a ignorar en el walk
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORAR_DIRS and not d.startswith('.')
        ]

        for fname in filenames:
            path = Path(dirpath) / fname
            if path.suffix.lower() in DATA_EXTS:
                archivos.append(path)

    return sorted(archivos)


# ══════════════════════════════════════════════════════════
# AUDITORÍA OHLCV
# ══════════════════════════════════════════════════════════

def auditar_ohlcv(path: Path, activo: str) -> dict:
    r = _base_result(path, 'OHLCV', activo)

    try:
        df = pl.read_parquet(str(path)).sort('date')
    except Exception as e:
        r['errores'].append(f'Ilegible: {e}')
        r['veredicto'] = 'CUARENTENA'
        return r

    r['filas']    = len(df)
    r['columnas'] = len(df.columns)
    r['fecha_min'] = str(df['date'].min())
    r['fecha_max'] = str(df['date'].max())

    # Dtype fecha
    date_dtype = str(df['date'].dtype)
    r['date_dtype'] = date_dtype
    if 'Datetime' not in date_dtype or 'UTC' not in date_dtype:
        r['errores'].append(f'date dtype: {date_dtype} — debe ser Datetime(ms,UTC)')

    # Columnas canónicas
    if len(df.columns) != CANON_COLS:
        r['advertencias'].append(f'{len(df.columns)} cols (esperado {CANON_COLS})')

    # Gaps
    if len(df) > 1:
        diffs = df['date'].diff().drop_nulls()
        try:
            gap_max = max(d.days for d in diffs.to_list() if d is not None)
        except Exception:
            gap_max = 0
        r['gap_max_dias'] = gap_max
        if gap_max > GAP_MAX_DIAS:
            r['advertencias'].append(f'Gap de {gap_max} días')

    # Leakage detector
    leakages = []
    for feat in ['vix_norm', 'entropy_psych_vix', 'entropy_decay_lambda',
                 'mass_panic_index', 'fear_momentum']:
        if feat not in df.columns or 'log_return' not in df.columns:
            continue
        try:
            fut  = df['log_return'].shift(-1)
            mask = df[feat].is_not_nan() & fut.is_not_nan()
            x    = df[feat].filter(mask).to_numpy()
            y    = fut.filter(mask).to_numpy()
            if len(x) > 100:
                corr = float(np.corrcoef(x, y)[0, 1])
                r.setdefault('corr_fut', {})[feat] = round(corr, 5)
                if abs(corr) > CORR_FUT_MAX:
                    leakages.append(f'{feat}(corr={corr:.3f})')
        except Exception:
            pass

    if leakages:
        r['errores'].append(f'LEAKAGE: {", ".join(leakages)}')

    _finalizar_veredicto(r)
    return r


# ══════════════════════════════════════════════════════════
# AUDITORÍA GDELT
# ══════════════════════════════════════════════════════════

GDELT_COLS = {'date', 'asset', 'entropy_shannon', 'zipf_concentration',
              'goldstein_mean', 'tone_variance', 'n_events',
              'nash_frozen_7d', 'vitality_tesla'}

def auditar_gdelt(path: Path, activo: str) -> dict:
    r = _base_result(path, 'GDELT', activo)

    try:
        df = pl.read_parquet(str(path))
    except Exception as e:
        r['errores'].append(f'Ilegible: {e}')
        r['veredicto'] = 'CUARENTENA'
        return r

    r['filas']    = len(df)
    r['columnas'] = len(df.columns)

    faltantes = GDELT_COLS - set(df.columns)
    if faltantes:
        r['errores'].append(f'Columnas faltantes: {sorted(faltantes)}')

    if 'entropy_shannon' in df.columns:
        n_nan = int(df['entropy_shannon'].is_null().sum())
        r['entropy_nan'] = n_nan

    if 'vitality_tesla' in df.columns:
        try:
            dist = df['vitality_tesla'].value_counts().sort('vitality_tesla')
            r['vitality_dist'] = {
                str(row['vitality_tesla']): row['count']
                for row in dist.iter_rows(named=True)
            }
        except Exception:
            pass

    if 'date' in df.columns:
        r['fecha_min'] = str(df['date'].min())
        r['fecha_max'] = str(df['date'].max())

    if 'asset' in df.columns:
        assets_en_archivo = df['asset'].unique().to_list()
        r['activos_en_archivo'] = assets_en_archivo
        if len(assets_en_archivo) > 1:
            r['advertencias'].append(f'Múltiples activos: {assets_en_archivo}')

    _finalizar_veredicto(r)
    return r


# ══════════════════════════════════════════════════════════
# AUDITORÍA GENÉRICA PARQUET
# ══════════════════════════════════════════════════════════

def auditar_parquet_generico(path: Path) -> dict:
    r = _base_result(path, 'PARQUET_OTRO', 'N/A')
    try:
        df = pl.read_parquet(str(path))
        r['filas']    = len(df)
        r['columnas'] = len(df.columns)
        r['columnas_nombres'] = df.columns
    except Exception as e:
        r['errores'].append(f'Ilegible: {e}')
        r['veredicto'] = 'CUARENTENA'
    _finalizar_veredicto(r)
    return r


# ══════════════════════════════════════════════════════════
# AUDITORÍA CSV
# ══════════════════════════════════════════════════════════

def auditar_csv(path: Path) -> dict:
    r = _base_result(path, 'CSV', 'N/A')
    r['tamaño_bytes'] = path.stat().st_size

    if r['tamaño_bytes'] == 0:
        r['errores'].append('Archivo vacío')
        r['veredicto'] = 'CUARENTENA'
        return r

    try:
        df = pl.read_csv(str(path), n_rows=5, infer_schema_length=200,
                         ignore_errors=True)
        r['columnas']         = len(df.columns)
        r['columnas_nombres'] = df.columns
    except Exception as e:
        r['advertencias'].append(f'No parseable limpiamente: {e}')

    _finalizar_veredicto(r)
    return r


# ══════════════════════════════════════════════════════════
# AUDITORÍA ZIP
# ══════════════════════════════════════════════════════════

def auditar_zip(path: Path) -> dict:
    r = _base_result(path, 'ZIP', 'N/A')
    r['tamaño_bytes'] = path.stat().st_size

    if r['tamaño_bytes'] == 0:
        r['errores'].append('Archivo vacío')
        r['veredicto'] = 'CUARENTENA'
        return r

    try:
        with zipfile.ZipFile(str(path), 'r') as z:
            bad = z.testzip()
            nombres = z.namelist()
            r['archivos_internos'] = len(nombres)
            r['muestra_nombres']   = nombres[:10]
            if bad:
                r['errores'].append(f'Archivo corrupto: {bad}')
                r['veredicto'] = 'CUARENTENA'
    except zipfile.BadZipFile as e:
        r['errores'].append(f'ZIP corrupto: {e}')
        r['veredicto'] = 'CUARENTENA'
    except Exception as e:
        r['advertencias'].append(f'Error al leer: {e}')

    _finalizar_veredicto(r)
    return r


# ══════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════

def _base_result(path: Path, tipo: str, activo: str) -> dict:
    return {
        'archivo': str(path),
        'tipo': tipo,
        'activo': activo,
        'version_categoria': clasificar_version(path),
        'sha256': sha256(path),
        'md5':    md5(path),
        'tamaño_bytes': path.stat().st_size,
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

def _finalizar_veredicto(r: dict):
    if r['veredicto'] == 'CUARENTENA':
        return
    if r['errores']:
        r['veredicto'] = 'CUARENTENA' if any(
            kw in e for e in r['errores']
            for kw in ('LEAKAGE', 'Ilegible', 'dtype', 'faltantes', 'corrupto', 'vacío')
        ) else 'ADVERTENCIA'
    elif r['advertencias']:
        r['veredicto'] = 'ADVERTENCIA'


# ══════════════════════════════════════════════════════════
# CLASIFICAR PARQUET POR CONTENIDO
# ══════════════════════════════════════════════════════════

def clasificar_parquet(path: Path) -> tuple[str, str]:
    s = str(path).lower()
    activo = next((a for a in ACTIVOS if a.lower() in s), 'DESCONOCIDO')

    if 'gdelt' in s:
        return 'GDELT', activo
    if 'ohlcv' in s or 'canonical' in s or '_v4' in s:
        return 'OHLCV', activo
    if 'intraday' in s or '_1m' in s or '_15m' in s:
        return 'INTRADAY', activo
    if 'sector' in s:
        return 'SECTORES', activo
    return 'PARQUET_OTRO', activo


# ══════════════════════════════════════════════════════════
# CUARENTENA
# ══════════════════════════════════════════════════════════

def mover_a_cuarentena(path: Path, destino_base: Path, motivo: str):
    destino_base.mkdir(parents=True, exist_ok=True)
    try:
        rel = path.relative_to(MYDRIVE)
    except ValueError:
        rel = Path(path.name)

    destino = destino_base / rel
    destino.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.move(str(path), str(destino))
        nota = destino.with_suffix(destino.suffix + '.MOTIVO.txt')
        nota.write_text(
            f"Movido: {datetime.now(timezone.utc).isoformat()}\n"
            f"Origen: {path}\n"
            f"Motivo: {motivo}\n"
        )
        return True
    except Exception as e:
        print(f"     ⚠️  No se pudo mover: {e}")
        return False


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def main():
    titulo("SPEL — Auditor Maestro MyDrive v2.0")
    print(f"  Fecha  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  MyDrive: {MYDRIVE}")
    print(f"  Prod   : {RAIZ_PROD}")
    print()

    if not MYDRIVE.exists():
        print("❌ MyDrive no montado. Ejecutar: drive.mount('/content/drive')")
        return

    # ── 1. Descubrimiento total ──────────────────────────────
    sep()
    print("  [1/5] Descubriendo archivos de datos en MyDrive...")
    print("        (puede tardar 1-2 min si hay muchos archivos)")
    sep()

    todos_archivos = descubrir_mydrive(MYDRIVE)

    # Separar por extensión
    parquets = [p for p in todos_archivos if p.suffix == '.parquet']
    csvs     = [p for p in todos_archivos if p.suffix == '.csv']
    zips     = [p for p in todos_archivos if p.suffix == '.zip']

    print(f"    Parquets : {len(parquets)}")
    print(f"    CSVs     : {len(csvs)}")
    print(f"    ZIPs     : {len(zips)}")
    print()

    # ── 2. Clasificar por versión ────────────────────────────
    sep()
    print("  [2/5] Clasificando por versión SPEL...")
    sep()

    por_version = defaultdict(list)
    for p in todos_archivos:
        por_version[clasificar_version(p)].append(p)

    for version, archivos in sorted(por_version.items()):
        icono = {
            'PRODUCCION':        '✅',
            'CUARENTENA_V2':     '🔒',
            'CUARENTENA_LEGACY': '🔒',
            'DEPRECATED':        '🔴',
            'HUERFANO':          '⚠️ '
        }.get(version, '?')
        print(f"    {icono} {version:20s} : {len(archivos)} archivos")

    print()

    # ── 3. Mover DEPRECATED a cuarentena legacy ─────────────
    sep()
    print("  [3/5] Procesando archivos DEPRECATED...")
    sep()

    deprecated = por_version.get('DEPRECATED', [])
    if not deprecated:
        print("    ✅ Sin archivos deprecated encontrados.")
    else:
        print(f"    🔴 {len(deprecated)} archivos deprecated → moviendo a {CUARENTENA_LEGACY}")
        movidos = 0
        for path in deprecated:
            rel = str(path).replace(str(MYDRIVE) + '/', '')
            print(f"    → {rel}")
            ok = mover_a_cuarentena(path, CUARENTENA_LEGACY, 'Versión deprecated (no v2.0)')
            if ok:
                movidos += 1
        print(f"\n    {movidos}/{len(deprecated)} archivos movidos a {CUARENTENA_LEGACY}")

    print()

    # ── 4. Auditar PRODUCCION ────────────────────────────────
    sep()
    print("  [4/5] Auditando archivos de PRODUCCION (SPEL-v2.0)...")
    sep()

    produccion = por_version.get('PRODUCCION', [])
    resultados_prod = []
    sha_registry = {}

    for path in produccion:
        ext  = path.suffix.lower()
        tipo, activo = clasificar_parquet(path) if ext == '.parquet' else ('CSV' if ext == '.csv' else 'ZIP', 'N/A')

        # Auditar según tipo
        if ext == '.parquet':
            if tipo == 'OHLCV':
                res = auditar_ohlcv(path, activo)
            elif tipo == 'GDELT':
                res = auditar_gdelt(path, activo)
            else:
                res = auditar_parquet_generico(path)
        elif ext == '.csv':
            res = auditar_csv(path)
        elif ext == '.zip':
            res = auditar_zip(path)
        else:
            continue

        # Imprimir resultado
        icono = {'LIMPIO': '✅', 'ADVERTENCIA': '⚠️ ', 'CUARENTENA': '🔴'}.get(res['veredicto'], '?')
        try:
            rel = path.relative_to(RAIZ_PROD)
        except ValueError:
            rel = path.name

        print(f"\n  {icono} {rel}")
        print(f"     Tipo: {tipo} | Activo: {activo}")
        print(f"     SHA256: {res['sha256']} | Tamaño: {fmt_bytes(res['tamaño_bytes'])}")

        if res.get('filas'):
            print(f"     Filas: {res['filas']:,} | Cols: {res.get('columnas', '?')}")
        if res.get('fecha_min'):
            print(f"     Rango: {res['fecha_min']} → {res['fecha_max']}")
        for adv in res['advertencias']:
            print(f"     ⚠️  {adv}")
        for err in res['errores']:
            print(f"     ❌ {err}")

        # Mover a cuarentena si aplica
        if res['veredicto'] == 'CUARENTENA':
            motivo = '; '.join(res['errores']) or 'Veredicto CUARENTENA'
            mover_a_cuarentena(path, CUARENTENA_V2, motivo)

        # SHA registry solo para producción limpia
        if res['veredicto'] != 'CUARENTENA':
            try:
                rel_key = str(path.relative_to(RAIZ_PROD))
            except ValueError:
                rel_key = path.name
            sha_registry[rel_key] = {
                'sha256':           res['sha256'],
                'md5':              res['md5'],
                'tipo':             tipo,
                'activo':           activo,
                'fecha_auditoria':  datetime.now(timezone.utc).isoformat()
            }

        resultados_prod.append(res)

    # ── 5. Mapear HUÉRFANOS ──────────────────────────────────
    sep()
    print("\n  [5/5] Archivos SPEL huérfanos (fuera de carpetas conocidas)...")
    sep()

    huerfanos = [p for p in por_version.get('HUERFANO', []) if es_archivo_spel(p)]
    if not huerfanos:
        print("    ✅ Sin archivos SPEL huérfanos.")
    else:
        print(f"    ⚠️  {len(huerfanos)} archivos SPEL encontrados fuera de carpetas conocidas:")
        for p in huerfanos:
            rel = str(p).replace(str(MYDRIVE) + '/', '')
            print(f"    📍 {rel} ({fmt_bytes(p.stat().st_size)})")
        print()
        print("    → Estos archivos NO se mueven automáticamente.")
        print("      Revisa manualmente y decide si pertenecen a v2.0 o van a cuarentena.")

    # ── Resumen ──────────────────────────────────────────────
    titulo("RESUMEN GLOBAL")

    limpios      = sum(1 for r in resultados_prod if r['veredicto'] == 'LIMPIO')
    advertencias = sum(1 for r in resultados_prod if r['veredicto'] == 'ADVERTENCIA')
    cuarentena   = sum(1 for r in resultados_prod if r['veredicto'] == 'CUARENTENA')

    print(f"  MyDrive total archivos de datos : {len(todos_archivos)}")
    print(f"  DEPRECATED movidos a cuarentena : {len(deprecated)}")
    print(f"  PRODUCCION auditados            : {len(resultados_prod)}")
    print(f"    ✅ LIMPIOS                    : {limpios}")
    print(f"    ⚠️  CON ADVERTENCIAS           : {advertencias}")
    print(f"    🔴 ENVIADOS A CUARENTENA       : {cuarentena}")
    print(f"  HUÉRFANOS SPEL                  : {len(huerfanos)}")
    print()

    # Advertencias que requieren acción manual
    con_adv = [r for r in resultados_prod if r['veredicto'] == 'ADVERTENCIA']
    if con_adv:
        print("  Advertencias que requieren revisión manual:")
        for r in con_adv:
            print(f"    ⚠️  {Path(r['archivo']).name}")
            for a in r.get('advertencias', []):
                print(f"        · {a}")
        print()

    # ── Guardar reportes ─────────────────────────────────────
    sep()
    print("  Guardando reportes...")
    RAIZ_PROD.mkdir(parents=True, exist_ok=True)
    (RAIZ_PROD / 'meta').mkdir(parents=True, exist_ok=True)

    report = {
        'version':     'spel_master_audit_v2.0',
        'fecha':       datetime.now(timezone.utc).isoformat(),
        'mydrive':     str(MYDRIVE),
        'resumen': {
            'total_archivos_datos': len(todos_archivos),
            'deprecated_movidos':   len(deprecated),
            'produccion_auditados': len(resultados_prod),
            'limpios':              limpios,
            'advertencias':         advertencias,
            'cuarentena':           cuarentena,
            'huerfanos_spel':       len(huerfanos)
        },
        'por_version': {v: [str(p) for p in ps] for v, ps in por_version.items()},
        'huerfanos':   [str(p) for p in huerfanos],
        'produccion':  resultados_prod
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    SHA_PATH.write_text(json.dumps(sha_registry, indent=2))

    print(f"  📋 Reporte maestro → {REPORT_PATH}")
    print(f"  🔑 SHA registry    → {SHA_PATH}")

    sep('═')
    estado_final = "✅ Dataset limpio." if cuarentena == 0 else f"⚠️  {cuarentena} archivos en cuarentena."
    print(f"  {estado_final}")
    print("  Próximo paso: python spel_p90_recalibrate.py")
    sep('═')


if __name__ == '__main__':
    main()
