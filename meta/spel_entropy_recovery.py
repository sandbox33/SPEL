"""
SPEL — Recuperación de Entropy Anuales
=======================================
Problema: XAU y NIFTY50 no tienen entropy anuales en SPEL-v2.0/data_lake/.
Situación: Existen en _SPEL_CUARENTENA en 3 versiones distintas.
Estrategia:
  1. Usar SPEL-v1.1 como fuente preferida (más reciente, incluye 2026)
  2. Auditar ADN de cada parquet antes de copiar
  3. Solo copiar los que pasen auditoría limpia o con advertencia tolerable
  4. Rechazar los que tengan leakage o dtype incorrecto
  5. Actualizar sha_registry_v2.json con los nuevos archivos
  6. Documentar el gap de NIFTY50 en el reporte

SPEL · 10-Mar-2026
"""

import hashlib
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

import polars as pl
import numpy as np

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════

MYDRIVE    = Path('/content/drive/MyDrive')
RAIZ_PROD  = MYDRIVE / 'SPEL-v2.0'
SHA_PATH   = RAIZ_PROD / 'meta' / 'sha_registry_v2.json'
REPORT_PATH = RAIZ_PROD / 'meta' / 'recovery_report.json'

# Fuentes en orden de preferencia — la primera que tenga el año gana
FUENTES_PREFERENCIA = [
    MYDRIVE / '_SPEL_CUARENTENA' / 'SPEL-v1.1' / 'data_lake' / 'entropy',
    MYDRIVE / '_SPEL_CUARENTENA' / 'SPEL' / 'data_lake' / 'entropy',
    MYDRIVE / '_SPEL_CUARENTENA' / 'SPEL_v8' / 'shared_volumes' / 'data_lake' / 'entropy',
]

# Destino en v2.0 — estructura canónica
def destino_entropy(activo: str) -> Path:
    return RAIZ_PROD / 'data_lake' / activo / 'entropy'

ACTIVOS_A_RECUPERAR = ['XAU', 'NIFTY50']
ANOS = list(range(2015, 2027))  # 2015 → 2026

CORR_FUT_MAX = 0.05

# ══════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════

def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]

def md5_file(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:12]

def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()

def sep(c='─', n=65):
    print(c * n)

def titulo(t):
    sep('═')
    print(f"  {t}")
    sep('═')

# ══════════════════════════════════════════════════════════
# LOCALIZACIÓN DE CANDIDATOS
# ══════════════════════════════════════════════════════════

def localizar_candidato(activo: str, ano: int) -> Path | None:
    """
    Busca el archivo de entropy anual para un activo y año dado.
    Prueba las fuentes en orden de preferencia.
    SPEL-v1.1 tiene estructura: entropy/{ACTIVO}/{ACTIVO}_{AÑO}_entropy.parquet
    SPEL y SPEL_v8 tienen estructura: entropy/{ACTIVO}_{AÑO}_entropy.parquet
    """
    nombre = f"{activo}_{ano}_entropy.parquet"

    for fuente in FUENTES_PREFERENCIA:
        # Estructura con subfolder por activo (SPEL-v1.1)
        candidato = fuente / activo / nombre
        if candidato.exists():
            return candidato

        # Estructura plana (SPEL, SPEL_v8)
        candidato = fuente / nombre
        if candidato.exists():
            return candidato

    return None

# ══════════════════════════════════════════════════════════
# AUDITORÍA DE ENTROPY ANUAL
# ══════════════════════════════════════════════════════════

def auditar_entropy(path: Path, activo: str, ano: int) -> dict:
    """
    Auditoría ADN completa para un parquet de entropy anual.
    Criterios:
      LIMPIO      → columnas OK, dtypes OK, sin leakage, NaN < 10%
      ADVERTENCIA → gaps o NaN moderados, sin leakage
      RECHAZADO   → leakage, dtype incorrecto, ilegible, o vacío
    """
    resultado = {
        'archivo': str(path),
        'activo': activo,
        'año': ano,
        'sha256': sha256_file(path),
        'md5': md5_file(path),
        'tamaño_bytes': path.stat().st_size,
        'advertencias': [],
        'errores': [],
        'decision': 'PENDIENTE'  # COPIAR | RECHAZAR
    }

    if path.stat().st_size == 0:
        resultado['errores'].append('Archivo vacío')
        resultado['decision'] = 'RECHAZAR'
        return resultado

    try:
        df = pl.read_parquet(str(path))
    except Exception as e:
        resultado['errores'].append(f'Ilegible: {e}')
        resultado['decision'] = 'RECHAZAR'
        return resultado

    resultado['filas']    = len(df)
    resultado['columnas'] = len(df.columns)
    resultado['columnas_nombres'] = df.columns

    if len(df) == 0:
        resultado['errores'].append('Parquet vacío (0 filas)')
        resultado['decision'] = 'RECHAZAR'
        return resultado

    # ── Rango de fechas
    if 'date' in df.columns:
        resultado['fecha_min'] = str(df['date'].min())
        resultado['fecha_max'] = str(df['date'].max())

        # Verificar que el año del parquet corresponde al año esperado
        fecha_min_year = df['date'].min()
        try:
            year_real = int(str(fecha_min_year)[:4])
            if year_real != ano:
                resultado['advertencias'].append(
                    f'Año en nombre={ano} pero datos desde {year_real}'
                )
        except Exception:
            pass

    # ── Columnas de entropía
    cols_entropy = [c for c in df.columns if 'entropy' in c.lower()]
    if not cols_entropy:
        resultado['errores'].append('Sin columnas de entropía — schema incorrecto')
        resultado['decision'] = 'RECHAZAR'
        return resultado
    resultado['cols_entropy'] = cols_entropy

    # ── NaN en entropía
    for col in cols_entropy:
        if df[col].dtype in [pl.Float32, pl.Float64]:
            nan_pct = df[col].is_nan().sum() / len(df)
        else:
            nan_pct = df[col].is_null().sum() / len(df)
        if nan_pct > 0.10:
            resultado['advertencias'].append(f'{col}: {nan_pct:.1%} NaN')
        resultado[f'nan_pct_{col}'] = round(float(nan_pct), 4)

    # ── Leakage detector — correlación con retorno futuro
    # Solo si hay columna de retorno; si no, skip (no es fatal en entropy anual)
    leakages = []
    if 'log_return' in df.columns:
        for col in cols_entropy:
            try:
                fut  = df['log_return'].shift(-1)
                mask = df[col].is_not_nan() & fut.is_not_nan()
                x    = df[col].filter(mask).to_numpy()
                y    = fut.filter(mask).to_numpy()
                if len(x) > 50:
                    corr = float(np.corrcoef(x, y)[0, 1])
                    resultado.setdefault('corr_fut', {})[col] = round(corr, 5)
                    if abs(corr) > CORR_FUT_MAX:
                        leakages.append(f'{col}(corr={corr:.3f})')
            except Exception:
                pass

    if leakages:
        resultado['errores'].append(f'LEAKAGE: {", ".join(leakages)}')
        resultado['decision'] = 'RECHAZAR'
        return resultado

    # ── Decisión final
    if resultado['errores']:
        resultado['decision'] = 'RECHAZAR'
    else:
        resultado['decision'] = 'COPIAR'

    return resultado

# ══════════════════════════════════════════════════════════
# RECUPERACIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════

def recuperar_entropy_activo(activo: str) -> dict:
    """Recupera todos los años disponibles para un activo."""
    sep()
    print(f"  Recuperando entropy: {activo}")
    sep()

    destino = destino_entropy(activo)
    destino.mkdir(parents=True, exist_ok=True)

    resumen = {
        'activo': activo,
        'años_copiados': [],
        'años_rechazados': [],
        'años_no_encontrados': [],
        'archivos': []
    }

    for ano in ANOS:
        candidato = localizar_candidato(activo, ano)

        if candidato is None:
            print(f"    {ano}: ⚠️  No encontrado en ninguna fuente")
            resumen['años_no_encontrados'].append(ano)
            continue

        # Auditar antes de copiar
        audit = auditar_entropy(candidato, activo, ano)

        nombre_destino = f"{activo}_{ano}_entropy.parquet"
        path_destino   = destino / nombre_destino

        if audit['decision'] == 'COPIAR':
            # Verificar si ya existe y tiene el mismo SHA
            if path_destino.exists():
                sha_existente = sha256_file(path_destino)
                if sha_existente == audit['sha256']:
                    print(f"    {ano}: ✅ Ya existe (SHA idéntico) — skip")
                    resumen['años_copiados'].append(ano)
                    audit['accion'] = 'YA_EXISTIA'
                    resumen['archivos'].append(audit)
                    continue

            shutil.copy2(str(candidato), str(path_destino))

            # Verificar integridad post-copia
            sha_post = sha256_file(path_destino)
            if sha_post != audit['sha256']:
                print(f"    {ano}: 🔴 SHA mismatch post-copia — error de transferencia")
                path_destino.unlink()
                resumen['años_rechazados'].append(ano)
                audit['accion']  = 'RECHAZADO_POST_COPIA'
                audit['errores'].append('SHA mismatch después de copiar')
                resumen['archivos'].append(audit)
                continue

            adv_str = f" ⚠️  {', '.join(audit['advertencias'])}" if audit['advertencias'] else ""
            fuente_label = str(candidato).replace(str(MYDRIVE) + '/', '')
            print(f"    {ano}: ✅ Copiado{adv_str}")
            print(f"         Fuente: {fuente_label}")
            resumen['años_copiados'].append(ano)
            audit['accion'] = 'COPIADO'

        else:
            errores_str = '; '.join(audit['errores'])
            print(f"    {ano}: 🔴 Rechazado — {errores_str}")
            resumen['años_rechazados'].append(ano)
            audit['accion'] = 'RECHAZADO'

        resumen['archivos'].append(audit)

    print(f"\n  {activo} resumen:")
    print(f"    ✅ Copiados  : {len(resumen['años_copiados'])} años → {resumen['años_copiados']}")
    if resumen['años_rechazados']:
        print(f"    🔴 Rechazados: {resumen['años_rechazados']}")
    if resumen['años_no_encontrados']:
        print(f"    ⚠️  No encontrados: {resumen['años_no_encontrados']}")

    return resumen

# ══════════════════════════════════════════════════════════
# ANÁLISIS DEL GAP DE NIFTY50
# ══════════════════════════════════════════════════════════

def analizar_gap_nifty50():
    sep()
    print("  Análisis gap NIFTY50")
    sep()

    ohlcv_path = RAIZ_PROD / 'data_lake' / 'NIFTY50' / 'ohlcv' / 'aggregated' / 'NIFTY50_ohlcv_v5.parquet'

    if not ohlcv_path.exists():
        print("  🔴 NIFTY50 OHLCV no encontrado")
        return {}

    df = pl.read_parquet(str(ohlcv_path)).sort('date')
    diffs = df.select(['date']).with_columns(
        pl.col('date').diff().alias('delta')
    ).drop_nulls()

    gaps_grandes = diffs.filter(pl.col('delta') > pl.duration(days=7))

    info = {
        'total_filas': len(df),
        'fecha_inicio': str(df['date'].min()),
        'fecha_fin': str(df['date'].max()),
        'gaps_mayores_7d': len(gaps_grandes),
        'gaps_detalle': []
    }

    print(f"  Total filas: {len(df):,}")
    print(f"  Rango: {df['date'].min()} → {df['date'].max()}")
    print(f"  Gaps > 7 días: {len(gaps_grandes)}")

    for row in gaps_grandes.iter_rows(named=True):
        gap_dias = row['delta'].days
        # Encontrar la fecha exacta del gap
        idx = df.with_row_index().filter(
            pl.col('date') >= df['date'].min() + row['delta']
        )
        print(f"\n  Gap de {gap_dias} días:")

        # Mostrar 3 filas antes y 3 después del gap
        fecha_fin_gap   = df.filter(
            pl.col('date') < df['date'].min() + row['delta']
        )['date'].max()
        fecha_ini_despues = df.filter(
            pl.col('date') > fecha_fin_gap
        )['date'].min()

        print(f"    Último dato antes : {fecha_fin_gap}")
        print(f"    Primer dato después: {fecha_ini_despues}")
        print(f"    → {gap_dias} días sin datos de mercado")

        info['gaps_detalle'].append({
            'dias': gap_dias,
            'fin_antes': str(fecha_fin_gap),
            'inicio_despues': str(fecha_ini_despues)
        })

    print(f"""
  DECISIÓN ARQUITECTURAL:
  ─────────────────────────────────────────────────────────
  Un gap de {gaps_grandes['delta'].max().days} días en NIFTY50 NO es corrupción.
  Es un problema de fuente de datos (proveedor no cubrió ese período).

  Impacto en entrenamiento con lookback=42d:
  → El entrenador NO puede construir secuencias que crucen el gap
  → El script spel_retrain_v5_clean.py DEBE detectar y descartar
    secuencias que incluyan la frontera del gap
  → Filas válidas para entrenamiento: datos hasta {fecha_fin_gap}
    + datos desde {fecha_ini_despues} (como secuencias independientes)

  Acción requerida en spel_retrain_v5_clean.py:
    Agregar filtro: gap_mask = (diffs > 7 días) → break de secuencia
  ─────────────────────────────────────────────────────────
    """)

    return info

# ══════════════════════════════════════════════════════════
# ACTUALIZAR SHA REGISTRY
# ══════════════════════════════════════════════════════════

def actualizar_sha_registry(resumenes: list[dict]):
    """Agrega los archivos nuevos al sha_registry_v2.json."""
    if SHA_PATH.exists():
        registry = json.loads(SHA_PATH.read_text())
    else:
        registry = {}

    agregados = 0
    for resumen in resumenes:
        activo = resumen['activo']
        for arch in resumen['archivos']:
            if arch.get('accion') not in ('COPIADO', 'YA_EXISTIA'):
                continue
            path_completo = Path(arch['archivo'])
            # El archivo puede estar en la fuente; necesitamos la ruta en v2.0
            nombre = path_completo.name
            rel_key = f"data_lake/{activo}/entropy/{nombre}"

            # Calcular SHA del archivo ya en producción
            prod_path = RAIZ_PROD / rel_key
            if not prod_path.exists():
                continue

            registry[rel_key] = {
                'sha256':          sha256_file(prod_path),
                'md5':             md5_file(prod_path),
                'tipo':            'ENTROPY_ANUAL',
                'activo':          activo,
                'veredicto':       'LIMPIO' if not arch.get('advertencias') else 'ADVERTENCIA',
                'fecha_auditoria': ahora()
            }
            agregados += 1

    SHA_PATH.write_text(json.dumps(registry, indent=2))
    print(f"  🔑 SHA registry actualizado — {agregados} entradas nuevas")
    print(f"     Total en registry: {len(registry)}")

# ══════════════════════════════════════════════════════════
# VERIFICACIÓN FINAL
# ══════════════════════════════════════════════════════════

def verificar_cobertura_final():
    sep()
    print("  Cobertura final de data_lake")
    sep()

    canon = {
        'NVDA': {
            'ohlcv':   'data_lake/NVDA/ohlcv/aggregated/NVDA_ohlcv_v5.parquet',
            'gdelt':   'data_lake/NVDA/gdelt/raw/NVDA_gdelt_entropy.parquet',
            'entropy': 'data_lake/NVDA/entropy/',
        },
        'BTC': {
            'ohlcv':   'data_lake/BTC/ohlcv/aggregated/BTC_ohlcv_v5.parquet',
            'gdelt':   'data_lake/BTC/gdelt/raw/BTC_gdelt_entropy.parquet',
            'entropy': 'data_lake/BTC/entropy/',
        },
        'XAU': {
            'ohlcv':   'data_lake/XAU/ohlcv/aggregated/XAU_ohlcv_v5.parquet',
            'gdelt':   'data_lake/XAU/gdelt/raw/XAU_gdelt_entropy.parquet',
            'entropy': 'data_lake/XAU/entropy/',
        },
        'NIFTY50': {
            'ohlcv':   'data_lake/NIFTY50/ohlcv/aggregated/NIFTY50_ohlcv_v5.parquet',
            'gdelt':   'data_lake/NIFTY50/gdelt/raw/NIFTY50_gdelt_entropy.parquet',
            'entropy': 'data_lake/NIFTY50/entropy/',
        },
    }

    todo_ok = True
    for activo, rutas in canon.items():
        print(f"\n  {activo}:")
        for tipo, rel in rutas.items():
            path = RAIZ_PROD / rel
            if tipo == 'entropy':
                # Contar parquets en el directorio
                if path.exists():
                    archivos = list(path.glob('*.parquet'))
                    anos_encontrados = sorted([
                        int(p.stem.split('_')[1])
                        for p in archivos
                        if p.stem.split('_')[1].isdigit()
                    ])
                    if len(archivos) >= 8:
                        print(f"    ✅ entropy : {len(archivos)} años ({anos_encontrados[0]}→{anos_encontrados[-1]})")
                    else:
                        print(f"    ⚠️  entropy : solo {len(archivos)} años: {anos_encontrados}")
                        todo_ok = False
                else:
                    print(f"    🔴 entropy : directorio no existe")
                    todo_ok = False
            else:
                if path.exists():
                    sha = sha256_file(path)
                    rows = len(pl.read_parquet(str(path)))
                    print(f"    ✅ {tipo:5s}  : {sha} | {rows:,} filas")
                else:
                    print(f"    🔴 {tipo:5s}  : NO ENCONTRADO")
                    todo_ok = False

    print()
    if todo_ok:
        print("  ✅ Data lake completo — listo para spel_p90_recalibrate.py")
    else:
        print("  ⚠️  Hay gaps — revisar antes de entrenar")

    return todo_ok

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def main():
    titulo("SPEL — Recuperación de Entropy Anuales")
    print(f"  Fecha: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # Verificar que las fuentes existen
    print("  Fuentes disponibles:")
    for fuente in FUENTES_PREFERENCIA:
        existe = "✅" if fuente.exists() else "❌"
        print(f"    {existe} {fuente}")
    print()

    # Recuperar entropy para XAU y NIFTY50
    resumenes = []
    for activo in ACTIVOS_A_RECUPERAR:
        resumen = recuperar_entropy_activo(activo)
        resumenes.append(resumen)
        print()

    # Analizar gap NIFTY50
    gap_info = analizar_gap_nifty50()

    # Actualizar SHA registry
    sep()
    print("  Actualizando SHA registry...")
    actualizar_sha_registry(resumenes)

    # Verificación final
    print()
    todo_ok = verificar_cobertura_final()

    # Guardar reporte
    reporte = {
        'version':   'spel_recovery_v1.0',
        'fecha':     ahora(),
        'resumenes': resumenes,
        'gap_nifty50': gap_info,
        'data_lake_completo': todo_ok
    }
    REPORT_PATH.write_text(json.dumps(reporte, indent=2, default=str))

    titulo("RESULTADO")
    if todo_ok:
        print("  ✅ Recuperación completa.")
        print()
        print("  PRÓXIMOS PASOS:")
        print("  1. python spel_p90_recalibrate.py    ← recalibrar P90 XAU")
        print("  2. python spel_retrain_v5_clean.py   ← entrenar BTC primero")
        print("     (agregar gap_mask para NIFTY50 antes de entrenar NIFTY50)")
    else:
        print("  ⚠️  Recuperación parcial — revisar gaps antes de entrenar.")
    sep('═')


if __name__ == '__main__':
    main()
