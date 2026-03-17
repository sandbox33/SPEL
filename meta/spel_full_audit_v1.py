"""
SPEL — Auditoría Completa de Datasets v1.0
===========================================
Escanea TODO en SPEL-v2.0:
  - Parquets OHLCV y GDELT
  - CSVs crudos
  - ZIPs de backup
  - JSONs de estado

Para cada archivo emite:
  - SHA fresco (reemplaza registro viejo)
  - Calidad forense (leakage, NaNs, dtype, gaps)
  - Veredicto: LIMPIO / ADVERTENCIA / CUARENTENA

Salidas:
  /content/drive/MyDrive/SPEL-v2.0/meta/full_audit_report.json
  /content/drive/MyDrive/SPEL-v2.0/meta/sha_registry_v2.json   ← SHA frescos
  /content/drive/MyDrive/SPEL-v2.0/CUARENTENA/                 ← archivos malos

Autor: SPEL · 09-Mar-2026
"""

import os
import hashlib
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

import polars as pl
import numpy as np

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
RAIZ          = Path('/content/drive/MyDrive/SPEL-v2.0')
CUARENTENA    = RAIZ / 'CUARENTENA'
REPORT_PATH   = RAIZ / 'meta' / 'full_audit_report.json'
SHA_PATH      = RAIZ / 'meta' / 'sha_registry_v2.json'

ACTIVOS       = ['NVDA', 'BTC', 'XAU', 'NIFTY50']
CANON_COLS    = 24

# Features de alto riesgo de leakage — auditoría forense profunda
FEATURES_CRITICAS = ['vix_norm', 'entropy_psych_vix', 'entropy_decay_lambda',
                     'mass_panic_index', 'fear_momentum']

# Umbrales de calidad
CORR_FUT_MAX      = 0.05   # correlación con futuro: >5% sospechoso
NAN_MAX_PCT       = 0.10   # NaNs permitidos: <10%
GAP_MAX_DIAS      = 30     # gap máximo tolerable en días

# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def sha_archivo(path: Path) -> str:
    """SHA-256 primeros 12 caracteres."""
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return h

def md5_archivo(path: Path) -> str:
    """MD5 primeros 12 caracteres (compatibilidad con registro viejo)."""
    h = hashlib.md5(path.read_bytes()).hexdigest()[:12]
    return h

def sep(char='─', n=65):
    print(char * n)

def titulo(texto):
    sep('═')
    print(f"  {texto}")
    sep('═')

# ─────────────────────────────────────────────
# AUDITORÍA DE PARQUET OHLCV
# ─────────────────────────────────────────────

def auditar_ohlcv(path: Path, activo: str) -> dict:
    resultado = {
        'archivo': str(path),
        'tipo': 'OHLCV',
        'activo': activo,
        'sha256': sha_archivo(path),
        'md5': md5_archivo(path),
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

    try:
        df = pl.read_parquet(str(path)).sort('date')
    except Exception as e:
        resultado['errores'].append(f'No se puede leer el parquet: {e}')
        resultado['veredicto'] = 'CUARENTENA'
        return resultado

    # ── Metadata básica
    resultado['filas']   = len(df)
    resultado['columnas'] = len(df.columns)
    resultado['fecha_min'] = str(df['date'].min())
    resultado['fecha_max'] = str(df['date'].max())

    # ── Columnas
    if len(df.columns) != CANON_COLS:
        resultado['advertencias'].append(
            f'Columnas: {len(df.columns)} (esperado {CANON_COLS})'
        )

    # ── Date dtype
    date_dtype = str(df['date'].dtype)
    resultado['date_dtype'] = date_dtype
    if 'Datetime' not in date_dtype or 'UTC' not in date_dtype:
        resultado['errores'].append(
            f'date dtype incorrecto: {date_dtype} — debe ser Datetime(ms, UTC)'
        )

    # ── Gaps temporales
    if len(df) > 1:
        diffs = df['date'].diff().drop_nulls()
        gap_max = diffs.max()
        gap_max_dias = gap_max.days if hasattr(gap_max, 'days') else int(gap_max.total_seconds() / 86400)
        gaps_grandes = diffs.filter(diffs > pl.duration(days=GAP_MAX_DIAS))
        resultado['gap_max_dias'] = gap_max_dias
        resultado['gaps_mayores_30d'] = len(gaps_grandes)

        if gap_max_dias > GAP_MAX_DIAS:
            resultado['advertencias'].append(
                f'Gap de {gap_max_dias} días detectado — posible dato faltante'
            )

    # ── Auditoría forense de features
    leakages = []
    for feat in FEATURES_CRITICAS:
        if feat not in df.columns:
            continue

        serie = df[feat]
        n_total = len(serie)
        n_nan   = serie.is_nan().sum() if serie.dtype in [pl.Float32, pl.Float64] else serie.is_null().sum()
        nan_pct = n_nan / n_total

        if nan_pct > NAN_MAX_PCT:
            resultado['advertencias'].append(f'{feat}: {nan_pct:.1%} NaNs')

        # Correlación con futuro (lookahead detector)
        if feat in df.columns and 'log_return' in df.columns:
            try:
                fut_return = df['log_return'].shift(-1)
                mask = serie.is_not_nan() & fut_return.is_not_nan()
                x = serie.filter(mask).to_numpy()
                y = fut_return.filter(mask).to_numpy()
                if len(x) > 100:
                    corr = float(np.corrcoef(x, y)[0, 1])
                    resultado.setdefault('corr_fut', {})[feat] = round(corr, 5)
                    if abs(corr) > CORR_FUT_MAX:
                        leakages.append(f'{feat} (corr_fut={corr:.4f})')
            except Exception:
                pass

    if leakages:
        resultado['errores'].append(f'LEAKAGE posible: {", ".join(leakages)}')

    # ── NaNs globales
    total_celdas = len(df) * len(df.columns)
    total_nulls  = sum(df[c].is_null().sum() for c in df.columns)
    resultado['nan_pct_global'] = round(total_nulls / total_celdas, 4)

    # ── Veredicto final
    if resultado['errores']:
        if any('LEAKAGE' in e or 'dtype incorrecto' in e for e in resultado['errores']):
            resultado['veredicto'] = 'CUARENTENA'
        else:
            resultado['veredicto'] = 'ADVERTENCIA'
    elif resultado['advertencias']:
        resultado['veredicto'] = 'ADVERTENCIA'

    return resultado


# ─────────────────────────────────────────────
# AUDITORÍA DE PARQUET GDELT
# ─────────────────────────────────────────────

GDELT_COLS_ESPERADAS = {
    'date', 'asset', 'entropy_shannon', 'zipf_concentration',
    'goldstein_mean', 'tone_variance', 'n_events',
    'nash_frozen_7d', 'vitality_tesla'
}

def auditar_gdelt(path: Path, activo: str) -> dict:
    resultado = {
        'archivo': str(path),
        'tipo': 'GDELT',
        'activo': activo,
        'sha256': sha_archivo(path),
        'md5': md5_archivo(path),
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

    try:
        df = pl.read_parquet(str(path))
    except Exception as e:
        resultado['errores'].append(f'No se puede leer: {e}')
        resultado['veredicto'] = 'CUARENTENA'
        return resultado

    resultado['filas']    = len(df)
    resultado['columnas'] = len(df.columns)

    # ── Columnas esperadas
    cols_presentes  = set(df.columns)
    cols_faltantes  = GDELT_COLS_ESPERADAS - cols_presentes
    cols_extra      = cols_presentes - GDELT_COLS_ESPERADAS
    resultado['cols_faltantes'] = list(cols_faltantes)
    resultado['cols_extra']     = list(cols_extra)

    if cols_faltantes:
        resultado['errores'].append(f'Columnas faltantes: {cols_faltantes}')

    # ── Fechas
    if 'date' in df.columns:
        try:
            df_sorted = df.sort('date')
            resultado['fecha_min'] = str(df_sorted['date'].min())
            resultado['fecha_max'] = str(df_sorted['date'].max())
        except Exception:
            pass

    # ── Vitality distribution
    if 'vitality_tesla' in df.columns:
        try:
            dist = df['vitality_tesla'].value_counts().sort('vitality_tesla')
            resultado['vitality_dist'] = {
                str(row['vitality_tesla']): row['count']
                for row in dist.iter_rows(named=True)
            }
        except Exception:
            pass

    # ── Entropía NaNs
    if 'entropy_shannon' in df.columns:
        n_nan = df['entropy_shannon'].is_null().sum()
        resultado['entropy_nan'] = int(n_nan)
        if n_nan > 0:
            resultado['advertencias'].append(f'entropy_shannon: {n_nan} NaNs')

    # ── Activo correcto en la columna 'asset'
    if 'asset' in df.columns:
        activos_en_archivo = df['asset'].unique().to_list()
        resultado['activos_en_archivo'] = activos_en_archivo
        if len(activos_en_archivo) > 1:
            resultado['advertencias'].append(
                f'Múltiples activos en un solo archivo: {activos_en_archivo}'
            )
        elif activos_en_archivo and activos_en_archivo[0] != activo:
            resultado['advertencias'].append(
                f'asset={activos_en_archivo[0]} pero esperado {activo}'
            )

    if resultado['errores']:
        resultado['veredicto'] = 'CUARENTENA' if cols_faltantes else 'ADVERTENCIA'
    elif resultado['advertencias']:
        resultado['veredicto'] = 'ADVERTENCIA'

    return resultado


# ─────────────────────────────────────────────
# AUDITORÍA DE CSV
# ─────────────────────────────────────────────

def auditar_csv(path: Path) -> dict:
    resultado = {
        'archivo': str(path),
        'tipo': 'CSV',
        'sha256': sha_archivo(path),
        'md5': md5_archivo(path),
        'tamaño_bytes': path.stat().st_size,
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

    try:
        df = pl.read_csv(str(path), n_rows=5, infer_schema_length=100)
        resultado['columnas']       = len(df.columns)
        resultado['columnas_nombres'] = df.columns
    except Exception as e:
        resultado['advertencias'].append(f'No parseable como CSV estándar: {e}')
        resultado['veredicto'] = 'ADVERTENCIA'

    if path.stat().st_size == 0:
        resultado['errores'].append('Archivo vacío')
        resultado['veredicto'] = 'CUARENTENA'

    return resultado


# ─────────────────────────────────────────────
# AUDITORÍA DE ZIP
# ─────────────────────────────────────────────

def auditar_zip(path: Path) -> dict:
    import zipfile
    resultado = {
        'archivo': str(path),
        'tipo': 'ZIP',
        'sha256': sha_archivo(path),
        'md5': md5_archivo(path),
        'tamaño_bytes': path.stat().st_size,
        'advertencias': [],
        'errores': [],
        'veredicto': 'LIMPIO'
    }

    try:
        with zipfile.ZipFile(str(path), 'r') as z:
            bad = z.testzip()
            resultado['archivos_internos'] = len(z.namelist())
            resultado['nombres'] = z.namelist()[:10]  # primeros 10
            if bad:
                resultado['errores'].append(f'Archivo corrupto dentro del ZIP: {bad}')
                resultado['veredicto'] = 'CUARENTENA'
    except zipfile.BadZipFile as e:
        resultado['errores'].append(f'ZIP corrupto: {e}')
        resultado['veredicto'] = 'CUARENTENA'
    except Exception as e:
        resultado['errores'].append(f'Error al leer ZIP: {e}')
        resultado['veredicto'] = 'ADVERTENCIA'

    if path.stat().st_size == 0:
        resultado['errores'].append('Archivo vacío')
        resultado['veredicto'] = 'CUARENTENA'

    return resultado


# ─────────────────────────────────────────────
# DESCUBRIMIENTO DE ARCHIVOS
# ─────────────────────────────────────────────

def descubrir_archivos(raiz: Path) -> dict:
    """Mapea todos los archivos de datos en SPEL-v2.0."""
    mapa = {
        'parquets': [],
        'csvs':     [],
        'zips':     [],
        'otros':    []
    }

    # Excluir CUARENTENA de la auditoría
    excluir = {str(raiz / 'CUARENTENA')}

    for path in sorted(raiz.rglob('*')):
        if not path.is_file():
            continue
        if any(str(path).startswith(e) for e in excluir):
            continue
        if path.suffix == '.parquet':
            mapa['parquets'].append(path)
        elif path.suffix == '.csv':
            mapa['csvs'].append(path)
        elif path.suffix == '.zip':
            mapa['zips'].append(path)
        elif path.suffix not in {'.py', '.md', '.json', '.yml', '.yaml',
                                   '.txt', '.log', '.ipynb', '.pt', '.pth'}:
            mapa['otros'].append(path)

    return mapa


# ─────────────────────────────────────────────
# DETECCIÓN DE TIPO DE PARQUET
# ─────────────────────────────────────────────

def clasificar_parquet(path: Path) -> tuple[str, str]:
    """Devuelve (tipo, activo) para un parquet dado su path."""
    parts = path.parts
    activo = 'DESCONOCIDO'

    for a in ACTIVOS:
        if a in parts:
            activo = a
            break

    if 'gdelt' in str(path).lower():
        tipo = 'GDELT'
    elif 'ohlcv' in str(path).lower():
        tipo = 'OHLCV'
    elif 'intraday' in str(path).lower():
        tipo = 'INTRADAY'
    elif 'sector' in str(path).lower():
        tipo = 'SECTORES'
    else:
        tipo = 'OTRO'

    return tipo, activo


# ─────────────────────────────────────────────
# CUARENTENA
# ─────────────────────────────────────────────

def mover_a_cuarentena(path: Path, motivo: str):
    """Mueve un archivo a CUARENTENA preservando la estructura de carpetas."""
    CUARENTENA.mkdir(parents=True, exist_ok=True)

    # Ruta relativa desde RAIZ
    try:
        rel = path.relative_to(RAIZ)
    except ValueError:
        rel = Path(path.name)

    destino = CUARENTENA / rel
    destino.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(str(path), str(destino))

    # Dejar nota de por qué fue movido
    nota = destino.with_suffix(destino.suffix + '.MOTIVO.txt')
    nota.write_text(f"Movido a cuarentena: {datetime.now(timezone.utc)}\nMotivo: {motivo}\n")

    print(f"  🔒 CUARENTENA: {rel}")
    print(f"     Motivo: {motivo}")


# ─────────────────────────────────────────────
# EJECUCIÓN PRINCIPAL
# ─────────────────────────────────────────────

def main():
    titulo("SPEL — Auditoría Completa de Datasets v1.0")
    print(f"  Fecha: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Raíz:  {RAIZ}")
    print()

    if not RAIZ.exists():
        print("❌ RAIZ no existe. Verifica que Drive está montado.")
        return

    # ── Paso 1: Descubrimiento
    sep()
    print("  [1/4] Descubriendo archivos...")
    sep()
    mapa = descubrir_archivos(RAIZ)
    print(f"    Parquets encontrados : {len(mapa['parquets'])}")
    print(f"    CSVs encontrados     : {len(mapa['csvs'])}")
    print(f"    ZIPs encontrados     : {len(mapa['zips'])}")
    print(f"    Otros                : {len(mapa['otros'])}")
    print()

    resultados  = []
    sha_registry = {}
    cuarentena_count = 0

    # ── Paso 2: Auditar parquets
    sep()
    print("  [2/4] Auditando parquets...")
    sep()

    for path in mapa['parquets']:
        tipo, activo = clasificar_parquet(path)
        rel = path.relative_to(RAIZ)
        print(f"\n  📄 {rel}")
        print(f"     Tipo: {tipo} | Activo: {activo}")

        if tipo == 'OHLCV':
            res = auditar_ohlcv(path, activo)
        elif tipo == 'GDELT':
            res = auditar_gdelt(path, activo)
        else:
            # Parquet genérico — audit básico
            res = {
                'archivo': str(path),
                'tipo': tipo,
                'activo': activo,
                'sha256': sha_archivo(path),
                'md5': md5_archivo(path),
                'advertencias': [],
                'errores': [],
                'veredicto': 'LIMPIO'
            }
            try:
                df = pl.read_parquet(str(path))
                res['filas']    = len(df)
                res['columnas'] = len(df.columns)
            except Exception as e:
                res['errores'].append(str(e))
                res['veredicto'] = 'CUARENTENA'

        # ── Imprimir resultado
        icono = {'LIMPIO': '✅', 'ADVERTENCIA': '⚠️ ', 'CUARENTENA': '🔴'}.get(res['veredicto'], '?')
        print(f"     SHA256: {res['sha256']} | MD5: {res['md5']}")
        if res.get('filas'):
            print(f"     Filas: {res['filas']} | Cols: {res.get('columnas', '?')}")
        if res.get('fecha_min'):
            print(f"     Rango: {res['fecha_min']} → {res['fecha_max']}")
        for adv in res['advertencias']:
            print(f"     ⚠️  {adv}")
        for err in res['errores']:
            print(f"     ❌ {err}")
        print(f"     {icono} VEREDICTO: {res['veredicto']}")

        # Registro SHA
        sha_registry[str(rel)] = {
            'sha256': res['sha256'],
            'md5': res['md5'],
            'fecha_auditoria': datetime.now(timezone.utc).isoformat()
        }

        # Mover a cuarentena si aplica
        if res['veredicto'] == 'CUARENTENA':
            motivo = '; '.join(res['errores']) if res['errores'] else 'Veredicto CUARENTENA'
            mover_a_cuarentena(path, motivo)
            cuarentena_count += 1

        resultados.append(res)

    # ── Paso 3: Auditar CSVs
    if mapa['csvs']:
        sep()
        print(f"\n  [3/4] Auditando {len(mapa['csvs'])} CSVs...")
        sep()
        for path in mapa['csvs']:
            rel = path.relative_to(RAIZ)
            res = auditar_csv(path)
            icono = {'LIMPIO': '✅', 'ADVERTENCIA': '⚠️ ', 'CUARENTENA': '🔴'}.get(res['veredicto'], '?')
            print(f"  {icono} {rel} ({res['tamaño_bytes']:,} bytes)")
            for err in res['errores']:
                print(f"     ❌ {err}")
            if res['veredicto'] == 'CUARENTENA':
                mover_a_cuarentena(path, '; '.join(res['errores']))
                cuarentena_count += 1
            resultados.append(res)
    else:
        print("\n  [3/4] CSVs: ninguno encontrado")

    # ── Paso 4: Auditar ZIPs
    if mapa['zips']:
        sep()
        print(f"\n  [4/4] Auditando {len(mapa['zips'])} ZIPs...")
        sep()
        for path in mapa['zips']:
            rel = path.relative_to(RAIZ)
            res = auditar_zip(path)
            icono = {'LIMPIO': '✅', 'ADVERTENCIA': '⚠️ ', 'CUARENTENA': '🔴'}.get(res['veredicto'], '?')
            print(f"  {icono} {rel} ({res['tamaño_bytes']:,} bytes)")
            if res.get('archivos_internos'):
                print(f"     Archivos internos: {res['archivos_internos']}")
            for err in res['errores']:
                print(f"     ❌ {err}")
            if res['veredicto'] == 'CUARENTENA':
                mover_a_cuarentena(path, '; '.join(res['errores']))
                cuarentena_count += 1
            resultados.append(res)
    else:
        print("\n  [4/4] ZIPs: ninguno encontrado")

    # ── Resumen global
    titulo("RESUMEN GLOBAL")
    limpios    = sum(1 for r in resultados if r['veredicto'] == 'LIMPIO')
    advertencias = sum(1 for r in resultados if r['veredicto'] == 'ADVERTENCIA')
    cuarentena = sum(1 for r in resultados if r['veredicto'] == 'CUARENTENA')

    print(f"  Total archivos auditados : {len(resultados)}")
    print(f"  ✅ LIMPIOS               : {limpios}")
    print(f"  ⚠️  CON ADVERTENCIAS      : {advertencias}")
    print(f"  🔴 ENVIADOS A CUARENTENA : {cuarentena}")
    print()

    # Listar advertencias para revisión manual
    con_adv = [r for r in resultados if r['veredicto'] == 'ADVERTENCIA']
    if con_adv:
        print("  Archivos con advertencias (revisar manualmente):")
        for r in con_adv:
            rel = Path(r['archivo']).relative_to(RAIZ) if RAIZ in Path(r['archivo']).parents else r['archivo']
            print(f"    ⚠️  {rel}")
            for adv in r.get('advertencias', []):
                print(f"        · {adv}")

    # ── Guardar reportes
    sep()
    print("  Guardando reportes...")

    report = {
        'version': 'spel_full_audit_v1.0',
        'fecha': datetime.now(timezone.utc).isoformat(),
        'raiz': str(RAIZ),
        'resumen': {
            'total': len(resultados),
            'limpios': limpios,
            'advertencias': advertencias,
            'cuarentena': cuarentena
        },
        'archivos': resultados
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"  📋 Reporte completo  → {REPORT_PATH}")

    SHA_PATH.write_text(json.dumps(sha_registry, indent=2))
    print(f"  🔑 SHA registry v2   → {SHA_PATH}")

    if cuarentena_count:
        print(f"  🔒 Cuarentena        → {CUARENTENA}")

    sep('═')
    if cuarentena > 0:
        print(f"  ⚠️  {cuarentena} archivos movidos a cuarentena.")
        print("     Revisa CUARENTENA/ antes de continuar.")
    else:
        print("  ✅ Dataset limpio — sin archivos en cuarentena.")
    print("  Próximo paso: python spel_p90_recalibrate.py")
    sep('═')


if __name__ == '__main__':
    main()
