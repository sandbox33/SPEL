"""
SPEL — Auditor Maestro MyDrive v2.1
=====================================
Correcciones respecto a v2.0:
  - ZIPs: se desempaca y audita el ADN de cada parquet interno
  - Deprecated: NO se mueve automáticamente — se audita primero
    → limpio + histórico   → ARCHIVAR (mover a legacy_archive/)
    → corrupto/leakage     → CUARENTENA
  - SHA registry solo contiene archivos verificados como limpios
  - Reporte incluye inventario completo de ZIPs con contenido

Salidas:
  SPEL-v2.0/meta/master_audit_report.json
  SPEL-v2.0/meta/sha_registry_v2.json
  SPEL-v2.0/CUARENTENA/         ← archivos malos de producción
  _SPEL_CUARENTENA/deprecated/  ← versiones viejas con problemas
  _SPEL_LEGACY_ARCHIVE/         ← versiones viejas limpias (conservar)

SPEL · 10-Mar-2026
"""

import os
import hashlib
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import polars as pl
import numpy as np

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════

MYDRIVE         = Path('/content/drive/MyDrive')
RAIZ_PROD       = MYDRIVE / 'SPEL-v2.0'
CUARENTENA_V2   = RAIZ_PROD / 'CUARENTENA'
CUARENTENA_LEG  = MYDRIVE / '_SPEL_CUARENTENA' / 'deprecated_bad'
LEGACY_ARCHIVE  = MYDRIVE / '_SPEL_LEGACY_ARCHIVE'

REPORT_PATH = RAIZ_PROD / 'meta' / 'master_audit_report.json'
SHA_PATH    = RAIZ_PROD / 'meta' / 'sha_registry_v2.json'

ACTIVOS      = ['NVDA', 'BTC', 'XAU', 'NIFTY50']
CANON_COLS   = 24
CORR_FUT_MAX = 0.05
NAN_MAX_PCT  = 0.10
GAP_MAX_DIAS = 30

GDELT_COLS = {
    'date', 'asset', 'entropy_shannon', 'zipf_concentration',
    'goldstein_mean', 'tone_variance', 'n_events',
    'nash_frozen_7d', 'vitality_tesla'
}

IGNORAR_DIRS = {
    '.git', '__pycache__', 'node_modules', '.ipynb_checkpoints',
    'Trash', '.Trash'
}

SPEL_KEYWORDS = {
    'spel', 'nvda', 'btc', 'xau', 'nifty', 'gdelt', 'ohlcv',
    'entropy', 'godel', 'vitality', 'canonical'
}

DEPRECATED_TOKENS = [
    'SPEL-v1', 'SPEL_v1', 'SPEL-v0', 'SPEL_PROD',
    'spel_root_backup', 'spel_old', 'SPEL-backup',
    '_SPEL_CUARENTENA'
]

# ══════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]

def fmt_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}GB"

def sep(c='─', n=68): print(c * n)
def titulo(t):
    sep('═')
    print(f"  {t}")
    sep('═')

def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()

def clasificar_version(path: Path) -> str:
    s = str(path)
    if str(RAIZ_PROD) in s and 'CUARENTENA' not in s:
        return 'PRODUCCION'
    if 'CUARENTENA' in s and str(RAIZ_PROD) in s:
        return 'CUARENTENA_V2'
    if str(MYDRIVE / '_SPEL_CUARENTENA') in s:
        return 'CUARENTENA_LEGACY'
    for tok in DEPRECATED_TOKENS:
        if tok.lower() in s.lower():
            return 'DEPRECATED'
    return 'HUERFANO'

def es_spel(path: Path) -> bool:
    return any(kw in path.name.lower() for kw in SPEL_KEYWORDS)

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
    if 'entropy' in s:
        return 'ENTROPY_ANUAL', activo
    return 'PARQUET_OTRO', activo

def _base(path: Path, tipo: str, activo: str) -> dict:
    return {
        'archivo': str(path),
        'tipo': tipo,
        'activo': activo,
        'version_categoria': clasificar_version(path),
        'sha256': sha256_file(path),
        'md5':    md5_file(path),
        'tamaño_bytes': path.stat().st_size,
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

def _veredicto(r: dict):
    """Asigna veredicto final basado en errores y advertencias."""
    if r['veredicto'] == 'CUARENTENA':
        return
    if r['errores']:
        fatales = ('LEAKAGE', 'Ilegible', 'dtype incorrecto',
                   'faltantes', 'corrupto', 'vacío')
        r['veredicto'] = 'CUARENTENA' if any(
            kw in e for e in r['errores'] for kw in fatales
        ) else 'ADVERTENCIA'
    elif r['advertencias']:
        r['veredicto'] = 'ADVERTENCIA'

# ══════════════════════════════════════════════════════════
# AUDITORÍA OHLCV
# ══════════════════════════════════════════════════════════

def auditar_ohlcv(path: Path, activo: str) -> dict:
    r = _base(path, 'OHLCV', activo)

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

    # dtype fecha
    dd = str(df['date'].dtype)
    r['date_dtype'] = dd
    if 'Datetime' not in dd or 'UTC' not in dd:
        r['errores'].append(f'dtype incorrecto: {dd} — debe ser Datetime(ms,UTC)')

    # columnas
    if len(df.columns) != CANON_COLS:
        r['advertencias'].append(f'{len(df.columns)} cols (esperado {CANON_COLS})')

    # gaps
    if len(df) > 1:
        diffs = df['date'].diff().drop_nulls()
        try:
            gap_max = max(
                (d.days for d in diffs.to_list() if d is not None), default=0
            )
            r['gap_max_dias'] = gap_max
            if gap_max > GAP_MAX_DIAS:
                r['advertencias'].append(f'Gap de {gap_max} días')
        except Exception:
            pass

    # leakage
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

    _veredicto(r)
    return r

# ══════════════════════════════════════════════════════════
# AUDITORÍA GDELT
# ══════════════════════════════════════════════════════════

def auditar_gdelt(path: Path, activo: str) -> dict:
    r = _base(path, 'GDELT', activo)
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

    if 'date' in df.columns:
        r['fecha_min'] = str(df['date'].min())
        r['fecha_max'] = str(df['date'].max())

    if 'entropy_shannon' in df.columns:
        r['entropy_nan'] = int(df['entropy_shannon'].is_null().sum())

    if 'vitality_tesla' in df.columns:
        try:
            dist = df['vitality_tesla'].value_counts().sort('vitality_tesla')
            r['vitality_dist'] = {
                str(row['vitality_tesla']): row['count']
                for row in dist.iter_rows(named=True)
            }
        except Exception:
            pass

    if 'asset' in df.columns:
        assets = df['asset'].unique().to_list()
        r['activos_en_archivo'] = assets
        if len(assets) > 1:
            r['advertencias'].append(f'Múltiples activos: {assets}')

    _veredicto(r)
    return r

# ══════════════════════════════════════════════════════════
# AUDITORÍA ENTROPY ANUAL
# ══════════════════════════════════════════════════════════

def auditar_entropy_anual(path: Path, activo: str) -> dict:
    """Parquets de entropía por año — schema puede diferir del GDELT consolidado."""
    r = _base(path, 'ENTROPY_ANUAL', activo)
    try:
        df = pl.read_parquet(str(path))
        r['filas']    = len(df)
        r['columnas'] = len(df.columns)
        r['columnas_nombres'] = df.columns

        if 'date' in df.columns:
            r['fecha_min'] = str(df['date'].min())
            r['fecha_max'] = str(df['date'].max())

        # Verificar si hay columnas de entropía esperadas
        tiene_entropy = any('entropy' in c.lower() for c in df.columns)
        if not tiene_entropy:
            r['advertencias'].append('No hay columnas de entropía — verificar schema')

        # NaN check
        total = len(df) * len(df.columns)
        nulls = sum(df[c].is_null().sum() for c in df.columns)
        r['nan_pct_global'] = round(nulls / total, 4) if total > 0 else 0
        if r['nan_pct_global'] > NAN_MAX_PCT:
            r['advertencias'].append(f'NaN global: {r["nan_pct_global"]:.1%}')

    except Exception as e:
        r['errores'].append(f'Ilegible: {e}')
        r['veredicto'] = 'CUARENTENA'

    _veredicto(r)
    return r

# ══════════════════════════════════════════════════════════
# AUDITORÍA GENÉRICA PARQUET
# ══════════════════════════════════════════════════════════

def auditar_parquet_generico(path: Path, tipo: str, activo: str) -> dict:
    r = _base(path, tipo, activo)
    try:
        df = pl.read_parquet(str(path))
        r['filas']    = len(df)
        r['columnas'] = len(df.columns)
        r['columnas_nombres'] = df.columns
    except Exception as e:
        r['errores'].append(f'Ilegible: {e}')
        r['veredicto'] = 'CUARENTENA'
    _veredicto(r)
    return r

# ══════════════════════════════════════════════════════════
# AUDITORÍA ZIP — con DNA completo del contenido
# ══════════════════════════════════════════════════════════

def auditar_zip(path: Path) -> dict:
    """
    Auditoría completa de ZIP:
    1. Verificar integridad del contenedor
    2. Inventariar contenido
    3. Desempacar y auditar ADN de cada parquet interno
    4. Veredicto basado en contenido real, no solo en el contenedor
    """
    r = _base(path, 'ZIP', 'N/A')
    r['tamaño_bytes'] = path.stat().st_size
    r['contenido'] = []

    if r['tamaño_bytes'] == 0:
        r['errores'].append('Archivo vacío')
        r['veredicto'] = 'CUARENTENA'
        return r

    # ── Paso 1: Integridad del contenedor
    try:
        with zipfile.ZipFile(str(path), 'r') as z:
            bad = z.testzip()
            if bad:
                r['errores'].append(f'Archivo interno corrupto: {bad}')
                r['veredicto'] = 'CUARENTENA'
                return r

            nombres = z.namelist()
            r['archivos_internos_total'] = len(nombres)
            r['inventario_completo']     = nombres

            parquets_internos = [n for n in nombres if n.endswith('.parquet')]
            csvs_internos     = [n for n in nombres if n.endswith('.csv')]
            r['parquets_internos'] = len(parquets_internos)
            r['csvs_internos']     = len(csvs_internos)

            # ── Paso 2: Desempacar y auditar parquets internos
            if parquets_internos:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp = Path(tmpdir)
                    z.extractall(tmp)

                    leakages_zip  = []
                    dtype_errors  = []
                    ilegibles     = []
                    advertencias_zip = []
                    inventario_detail = []

                    for nombre in parquets_internos:
                        ppath = tmp / nombre
                        if not ppath.exists():
                            ilegibles.append(nombre)
                            continue

                        tipo_p, activo_p = clasificar_parquet(ppath)

                        # Auditoría según tipo
                        if tipo_p == 'OHLCV':
                            res_p = auditar_ohlcv(ppath, activo_p)
                        elif tipo_p == 'GDELT':
                            res_p = auditar_gdelt(ppath, activo_p)
                        elif tipo_p == 'ENTROPY_ANUAL':
                            res_p = auditar_entropy_anual(ppath, activo_p)
                        else:
                            res_p = auditar_parquet_generico(ppath, tipo_p, activo_p)

                        # Colectar hallazgos
                        detail = {
                            'nombre':    nombre,
                            'tipo':      tipo_p,
                            'activo':    activo_p,
                            'veredicto': res_p['veredicto'],
                            'filas':     res_p.get('filas'),
                            'columnas':  res_p.get('columnas'),
                            'fecha_min': res_p.get('fecha_min'),
                            'fecha_max': res_p.get('fecha_max'),
                            'errores':   res_p.get('errores', []),
                            'advertencias': res_p.get('advertencias', [])
                        }
                        inventario_detail.append(detail)

                        for err in res_p.get('errores', []):
                            if 'LEAKAGE' in err:
                                leakages_zip.append(f'{nombre}: {err}')
                            elif 'dtype' in err:
                                dtype_errors.append(f'{nombre}: {err}')

                        for adv in res_p.get('advertencias', []):
                            advertencias_zip.append(f'{nombre}: {adv}')

                    r['contenido'] = inventario_detail

                    if ilegibles:
                        r['errores'].append(f'Parquets ilegibles: {ilegibles}')
                    if leakages_zip:
                        r['errores'].append(f'LEAKAGE en ZIP: {leakages_zip}')
                    if dtype_errors:
                        r['errores'].append(f'dtype incorrecto en ZIP: {dtype_errors}')
                    if advertencias_zip:
                        r['advertencias'].extend(advertencias_zip[:5])  # Primeras 5
                        if len(advertencias_zip) > 5:
                            r['advertencias'].append(
                                f'... y {len(advertencias_zip)-5} advertencias más'
                            )

            # CSVs internos — inventario básico
            if csvs_internos:
                r['advertencias'].append(
                    f'{len(csvs_internos)} CSVs internos — revisar manualmente'
                )

    except zipfile.BadZipFile as e:
        r['errores'].append(f'ZIP corrupto: {e}')
        r['veredicto'] = 'CUARENTENA'
        return r
    except Exception as e:
        r['advertencias'].append(f'Error durante extracción: {e}')

    _veredicto(r)
    return r

# ══════════════════════════════════════════════════════════
# GESTIÓN DE ARCHIVOS — mover con log
# ══════════════════════════════════════════════════════════

def mover_archivo(path: Path, destino_base: Path, motivo: str) -> bool:
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
            f"Movido   : {ahora()}\n"
            f"Origen   : {path}\n"
            f"Destino  : {destino}\n"
            f"Motivo   : {motivo}\n"
        )
        return True
    except Exception as e:
        print(f"     ⚠️  No se pudo mover {path.name}: {e}")
        return False

def imprimir_resultado(res: dict, base_path: Path):
    icono = {'LIMPIO': '✅', 'ADVERTENCIA': '⚠️ ', 'CUARENTENA': '🔴'}.get(
        res['veredicto'], '?'
    )
    try:
        rel = Path(res['archivo']).relative_to(base_path)
    except ValueError:
        rel = Path(res['archivo']).name

    print(f"\n  {icono} {rel}")
    print(f"     Tipo: {res['tipo']} | Activo: {res['activo']}")
    print(f"     SHA256: {res['sha256']} | {fmt_bytes(res['tamaño_bytes'])}")

    if res.get('filas'):
        print(f"     Filas: {res['filas']:,} | Cols: {res.get('columnas','?')}")
    if res.get('fecha_min'):
        print(f"     Rango: {res['fecha_min']} → {res['fecha_max']}")

    # Para ZIPs mostrar resumen del contenido auditado
    if res['tipo'] == 'ZIP' and res.get('contenido'):
        limpios_zip = sum(1 for c in res['contenido'] if c['veredicto'] == 'LIMPIO')
        malos_zip   = sum(1 for c in res['contenido'] if c['veredicto'] == 'CUARENTENA')
        print(f"     Contenido: {res['archivos_internos_total']} archivos "
              f"({res.get('parquets_internos',0)} parquets) | "
              f"✅ {limpios_zip} limpios | 🔴 {malos_zip} con problemas")

    for adv in res['advertencias'][:3]:
        print(f"     ⚠️  {adv}")
    for err in res['errores']:
        print(f"     ❌ {err}")
    print(f"     → VEREDICTO: {res['veredicto']}")

# ══════════════════════════════════════════════════════════
# DESCUBRIMIENTO
# ══════════════════════════════════════════════════════════

def descubrir_mydrive() -> list[Path]:
    archivos = []
    for dirpath, dirnames, filenames in os.walk(str(MYDRIVE)):
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORAR_DIRS and not d.startswith('.')
        ]
        for fname in filenames:
            if Path(fname).suffix.lower() in {'.parquet', '.csv', '.zip'}:
                archivos.append(Path(dirpath) / fname)
    return sorted(archivos)

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def main():
    titulo("SPEL — Auditor Maestro MyDrive v2.1")
    print(f"  Fecha  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  MyDrive: {MYDRIVE}")
    print()

    if not MYDRIVE.exists():
        print("❌ Drive no montado.")
        return

    # ─ 1. Descubrimiento ────────────────────────────────────
    sep()
    print("  [1/5] Descubriendo todos los archivos de datos...")
    sep()
    todos = descubrir_mydrive()

    por_version = defaultdict(list)
    for p in todos:
        por_version[clasificar_version(p)].append(p)

    for v, archivos in sorted(por_version.items()):
        icono = {
            'PRODUCCION': '✅', 'CUARENTENA_V2': '🔒',
            'CUARENTENA_LEGACY': '🔒', 'DEPRECATED': '🔴', 'HUERFANO': '⚠️ '
        }.get(v, '?')
        print(f"    {icono} {v:22s}: {len(archivos):4d} archivos")
    print()

    resultados_todos = []
    sha_registry     = {}
    movidos_cuarentena = 0
    movidos_archive    = 0

    # ─ 2. Auditar DEPRECATED (con DNA, no mover a ciegas) ────
    sep()
    print("  [2/5] Auditando archivos DEPRECATED (DNA completo)...")
    sep()

    deprecated = por_version.get('DEPRECATED', [])
    if not deprecated:
        print("    ✅ Sin archivos deprecated.")
    else:
        print(f"    Encontrados: {len(deprecated)} — auditando antes de decidir...\n")
        for path in deprecated:
            ext   = path.suffix.lower()
            tipo, activo = clasificar_parquet(path) if ext == '.parquet' else ('ZIP' if ext == '.zip' else 'CSV', 'N/A')

            if ext == '.parquet':
                if tipo == 'OHLCV':
                    res = auditar_ohlcv(path, activo)
                elif tipo == 'GDELT':
                    res = auditar_gdelt(path, activo)
                elif tipo == 'ENTROPY_ANUAL':
                    res = auditar_entropy_anual(path, activo)
                else:
                    res = auditar_parquet_generico(path, tipo, activo)
            elif ext == '.zip':
                res = auditar_zip(path)
            else:
                res = _base(path, 'CSV', 'N/A')
                _veredicto(res)

            imprimir_resultado(res, MYDRIVE)

            if res['veredicto'] == 'CUARENTENA':
                motivo = '; '.join(res['errores']) or 'Leakage/corrupto'
                if mover_archivo(path, CUARENTENA_LEG, motivo):
                    movidos_cuarentena += 1
                    print(f"     🔒 → {CUARENTENA_LEG}")
            else:
                # Limpio o advertencia → archivo histórico, conservar
                motivo = 'Versión deprecated — datos limpios, conservar como histórico'
                if mover_archivo(path, LEGACY_ARCHIVE, motivo):
                    movidos_archive += 1
                    print(f"     📦 → LEGACY ARCHIVE (datos limpios)")

            resultados_todos.append(res)

    # ─ 3. Auditar PRODUCCION ─────────────────────────────────
    sep()
    print("\n  [3/5] Auditando PRODUCCION (SPEL-v2.0)...")
    sep()

    produccion = por_version.get('PRODUCCION', [])
    resultados_prod = []

    for path in produccion:
        ext  = path.suffix.lower()
        tipo, activo = clasificar_parquet(path) if ext == '.parquet' else (
            'ZIP' if ext == '.zip' else 'CSV', 'N/A'
        )

        if ext == '.parquet':
            if tipo == 'OHLCV':
                res = auditar_ohlcv(path, activo)
            elif tipo == 'GDELT':
                res = auditar_gdelt(path, activo)
            elif tipo == 'ENTROPY_ANUAL':
                res = auditar_entropy_anual(path, activo)
            else:
                res = auditar_parquet_generico(path, tipo, activo)
        elif ext == '.zip':
            res = auditar_zip(path)
        else:
            res = _base(path, 'CSV', 'N/A')
            _veredicto(res)

        imprimir_resultado(res, RAIZ_PROD)

        if res['veredicto'] == 'CUARENTENA':
            motivo = '; '.join(res['errores']) or 'Veredicto CUARENTENA'
            if mover_archivo(path, CUARENTENA_V2, motivo):
                movidos_cuarentena += 1
                print(f"     🔒 → CUARENTENA_V2")
        else:
            try:
                rel_key = str(path.relative_to(RAIZ_PROD))
            except ValueError:
                rel_key = path.name
            sha_registry[rel_key] = {
                'sha256':          res['sha256'],
                'md5':             res['md5'],
                'tipo':            tipo,
                'activo':          activo,
                'veredicto':       res['veredicto'],
                'fecha_auditoria': ahora()
            }

        resultados_prod.append(res)
        resultados_todos.append(res)

    # ─ 4. Huérfanos SPEL ─────────────────────────────────────
    sep()
    print("\n  [4/5] Archivos SPEL huérfanos...")
    sep()
    huerfanos = [p for p in por_version.get('HUERFANO', []) if es_spel(p)]
    if not huerfanos:
        print("    ✅ Sin huérfanos SPEL.")
    else:
        for p in huerfanos:
            rel = str(p).replace(str(MYDRIVE)+'/', '')
            print(f"    📍 {rel} ({fmt_bytes(p.stat().st_size)})")
        print(f"\n    {len(huerfanos)} huérfanos — NO movidos automáticamente.")
        print("    Revisa manualmente: pueden ser módulos Python u otros recursos.")

    # ─ 5. Verificación de integridad canónica ────────────────
    sep()
    print("\n  [5/5] Verificación canónica SPEL-v2.0...")
    sep()

    canon_esperado = {
        'NVDA': 'data_lake/NVDA/ohlcv/aggregated/NVDA_ohlcv_v5.parquet',
        'BTC':  'data_lake/BTC/ohlcv/aggregated/BTC_ohlcv_v5.parquet',
        'XAU':  'data_lake/XAU/ohlcv/aggregated/XAU_ohlcv_v5.parquet',
        'NIFTY50': 'data_lake/NIFTY50/ohlcv/aggregated/NIFTY50_ohlcv_v5.parquet',
    }

    print("  OHLCV canónicos:")
    for activo, rel in canon_esperado.items():
        path = RAIZ_PROD / rel
        if path.exists():
            sha = sha256_file(path)
            sha_reg = sha_registry.get(rel, {}).get('sha256', 'NO EN REGISTRY')
            match = '✅' if sha == sha_reg else '⚠️ '
            print(f"    {match} {activo}: {sha} | en registry: {sha_reg}")
        else:
            print(f"    🔴 {activo}: ARCHIVO NO ENCONTRADO — puede estar en cuarentena")

    # ─ Resumen ───────────────────────────────────────────────
    titulo("RESUMEN GLOBAL")

    limpios      = sum(1 for r in resultados_prod if r['veredicto'] == 'LIMPIO')
    advertencias = sum(1 for r in resultados_prod if r['veredicto'] == 'ADVERTENCIA')
    cuarentena   = sum(1 for r in resultados_prod if r['veredicto'] == 'CUARENTENA')

    print(f"  MyDrive — archivos de datos encontrados : {len(todos)}")
    print(f"  DEPRECATED auditados                    : {len(deprecated)}")
    print(f"    📦 Limpios → LEGACY_ARCHIVE            : {movidos_archive}")
    print(f"    🔒 Malos   → CUARENTENA                : {movidos_cuarentena}")
    print(f"  PRODUCCION auditados                    : {len(resultados_prod)}")
    print(f"    ✅ LIMPIOS                             : {limpios}")
    print(f"    ⚠️  CON ADVERTENCIAS                   : {advertencias}")
    print(f"    🔴 ENVIADOS A CUARENTENA               : {cuarentena}")
    print(f"  HUÉRFANOS SPEL                          : {len(huerfanos)}")
    print()

    # Guardar
    report = {
        'version':    'spel_master_audit_v2.1',
        'fecha':      ahora(),
        'resumen': {
            'total': len(todos),
            'deprecated': len(deprecated),
            'legacy_archive': movidos_archive,
            'produccion': len(resultados_prod),
            'limpios': limpios,
            'advertencias': advertencias,
            'cuarentena': cuarentena,
            'huerfanos': len(huerfanos)
        },
        'sha_registry': sha_registry,
        'produccion':   resultados_prod,
        'huerfanos':    [str(p) for p in huerfanos]
    }

    (RAIZ_PROD / 'meta').mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    SHA_PATH.write_text(json.dumps(sha_registry, indent=2))

    sep()
    print(f"  📋 Reporte → {REPORT_PATH}")
    print(f"  🔑 SHA registry → {SHA_PATH}")
    sep('═')
    if cuarentena == 0 and limpios > 0:
        print("  ✅ Producción limpia. Siguiente: spel_p90_recalibrate.py")
    else:
        print("  ⚠️  Revisar cuarentena antes de continuar.")
    sep('═')


if __name__ == '__main__':
    main()
