"""
SPEL — Auditoría ADN Completa de Datasets
==========================================
Escanea TODOS los parquets de SPEL-v2.0 y emite veredicto forense por feature:
  - Lookahead / leakage potencial
  - Tipos de datos (date, dtypes)
  - Gaps temporales
  - Distribuciones estadísticas
  - Correlación con lr[t+1] (proxy de leakage)
  - Causalidad rolling vs global en features normalizadas
  - Integridad SHA

Este script es READ-ONLY — nunca modifica datos.
Ejecutar en Colab con Drive montado antes de cualquier entrenamiento.

Salida: /content/drive/MyDrive/SPEL-v2.0/meta/dna_audit_report.json
"""

import json, hashlib, warnings
from pathlib import Path
from datetime import datetime
import polars as pl
import numpy as np
warnings.filterwarnings('ignore')

# ── CONFIGURACIÓN ─────────────────────────────────────────────
RAIZ    = Path('/content/drive/MyDrive/SPEL-v2.0')
ACTIVOS = ['NVDA', 'BTC', 'XAU', 'NIFTY50']
OUT_JSON = RAIZ / 'meta' / 'dna_audit_report.json'

SHA_ESPERADOS = {
    'NVDA':    '3627a749da49',
    'BTC':     'a2c4e6f6e816',
    'XAU':     'a8e10cff2e80',
    'NIFTY50': '5e9624595c03',
}

# Features que DEBEN ser causales (solo info pasada, no futura)
# Clasificación por tipo de riesgo de lookahead
FEATURES_RIESGO = {
    # ALTO riesgo (normalización global puede introducir lookahead)
    'ALTO': ['vix_norm', 'entropy_psych_vix', 'entropy_decay_lambda', 'mass_panic_index'],
    # MEDIO riesgo (pueden tener rolling windows mal configuradas)
    'MEDIO': ['fibonacci_lag_1', 'fibonacci_lag_2', 'fibonacci_lag_3',
              'fibonacci_lag_5', 'fibonacci_lag_8', 'fibonacci_lag_13', 'fibonacci_lag_21',
              'nash_frozen_7d', 'fear_momentum'],
    # BAJO riesgo (features de precio pasado + entropía punto a punto)
    'BAJO': ['entropy_shannon', 'goldstein_geo', 'n_events_ohlcv',
             'vitality_tesla', 'log_return', 'open', 'high', 'low', 'close', 'volume'],
}

RUTA_OHLCV = lambda a: RAIZ / 'data_lake' / a / 'ohlcv' / 'aggregated' / f'{a}_ohlcv_v5.parquet'
RUTA_GDELT = lambda a: RAIZ / 'data_lake' / a / 'gdelt' / 'raw' / f'{a}_gdelt_entropy.parquet'

# ── HELPERS ───────────────────────────────────────────────────
def sha12(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()[:12]


def corr_con_futuro(series_np: np.ndarray, lr_np: np.ndarray, lag: int = 1) -> float:
    """Correlación de Pearson entre feature[t] y lr[t+lag]. Proxy de lookahead."""
    if len(series_np) < lag + 2:
        return float('nan')
    x = series_np[:-lag]
    y = lr_np[lag:]
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 10:
        return float('nan')
    return float(np.corrcoef(x[mask], y[mask])[0, 1])


def test_causalidad_rolling(col_np: np.ndarray, lr_np: np.ndarray,
                             window: int = 63) -> dict:
    """
    Test forense de lookahead inspirado en BUG-LA-01.
    Compara std global vs rolling std para detectar si la feature
    usa estadísticas futuras para normalizar.
    """
    global_std = float(np.nanstd(col_np))
    # rolling std (manual, polars-compatible)
    rolling_stds = []
    for i in range(len(col_np)):
        start = max(0, i - window + 1)
        chunk = col_np[start:i + 1]
        chunk = chunk[~np.isnan(chunk)]
        rolling_stds.append(float(np.std(chunk)) if len(chunk) > 1 else 0.0)
    rolling_std_arr = np.array(rolling_stds)

    diff = np.abs(col_np - col_np)  # reset
    # Diferencia entre la serie normalizada globalmente vs rolling
    # Si son iguales → sin leakage. Si difieren mucho → sospechoso.
    if global_std > 0:
        col_global_norm = col_np / global_std
    else:
        col_global_norm = col_np.copy()

    rolling_norms = col_np / np.where(rolling_std_arr > 0, rolling_std_arr, 1.0)
    diff = np.abs(col_global_norm - rolling_norms)

    corr_global  = corr_con_futuro(col_np, lr_np)
    corr_causal  = corr_con_futuro(rolling_norms, lr_np)

    return {
        'global_std': round(global_std, 6),
        'rolling_std_bar0': round(rolling_std_arr[0], 6),   # debe ser 0.0
        'rolling_std_last': round(rolling_std_arr[-1], 6),
        'pct_filas_diff_gt_001': round(float(np.mean(diff > 0.001)) * 100, 2),
        'diff_media': round(float(np.nanmean(diff)), 4),
        'diff_max': round(float(np.nanmax(diff)), 4),
        'corr_global_con_lr_t1': round(corr_global, 6) if not np.isnan(corr_global) else None,
        'corr_rolling_con_lr_t1': round(corr_causal, 6) if not np.isnan(corr_causal) else None,
        'leakage_detectado': bool(rolling_std_arr[0] != 0.0 or
                                   (not np.isnan(corr_global) and not np.isnan(corr_causal) and
                                    abs(corr_global) > abs(corr_causal) + 0.01)),
    }


def audit_feature(col_np: np.ndarray, lr_np: np.ndarray,
                  nombre: str, riesgo: str) -> dict:
    """Auditoría completa de una feature individual."""
    n = len(col_np)
    n_nan   = int(np.sum(np.isnan(col_np)))
    n_zero  = int(np.sum(col_np == 0))
    pct_nan  = round(100 * n_nan / n, 2)
    pct_zero = round(100 * n_zero / n, 2)
    nonzero  = round(100 * (n - n_zero - n_nan) / n, 2)

    result = {
        'riesgo': riesgo,
        'n_filas': n,
        'n_nan': n_nan,
        'pct_nan': pct_nan,
        'pct_nonzero': nonzero,
        'mean': round(float(np.nanmean(col_np)), 6),
        'std':  round(float(np.nanstd(col_np)), 6),
        'min':  round(float(np.nanmin(col_np)), 6),
        'max':  round(float(np.nanmax(col_np)), 6),
        'corr_lr_t1': round(corr_con_futuro(col_np, lr_np), 6),
    }

    # Test forense profundo para features de ALTO riesgo
    if riesgo == 'ALTO':
        forense = test_causalidad_rolling(col_np, lr_np)
        result['forense_lookahead'] = forense
        result['veredicto'] = '🔴 LEAKAGE' if forense['leakage_detectado'] else '✅ CAUSAL'
    else:
        result['veredicto'] = f'✅ OK ({riesgo})'

    return result


# ── INICIO AUDITORÍA ──────────────────────────────────────────
print("=" * 65)
print("SPEL — Auditoría ADN Completa")
print(f"Fecha: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
print("=" * 65)

reporte_global = {'_meta': {
    'fecha': datetime.utcnow().isoformat(),
    'version': 'SPEL-v2.0',
    'descripcion': 'Auditoría forense de lookahead, dtypes, gaps y distribuciones',
}, 'activos': {}}

# ── POR ACTIVO ────────────────────────────────────────────────
for activo in ACTIVOS:
    print(f"\n{'=' * 65}")
    print(f"  {activo}")
    print(f"{'=' * 65}")

    path_ohlcv = RUTA_OHLCV(activo)
    path_gdelt = RUTA_GDELT(activo)
    reporte_activo = {'ohlcv': {}, 'gdelt': {}, 'features': {}, 'veredicto_final': ''}
    problemas = []

    # ── SHA ───────────────────────────────────────────────────
    print(f"\n  [SHA]")
    if path_ohlcv.exists():
        sha_real = sha12(path_ohlcv)
        sha_ok = sha_real == SHA_ESPERADOS[activo]
        print(f"    OHLCV SHA: {sha_real} {'✅' if sha_ok else f'❌ esperado {SHA_ESPERADOS[activo]}'}")
        if not sha_ok:
            problemas.append('SHA_MISMATCH')
        reporte_activo['ohlcv']['sha'] = sha_real
        reporte_activo['ohlcv']['sha_ok'] = sha_ok
    else:
        print(f"    ❌ OHLCV parquet no encontrado: {path_ohlcv}")
        problemas.append('OHLCV_NOT_FOUND')
        reporte_global['activos'][activo] = {'error': 'OHLCV_NOT_FOUND'}
        continue

    # ── CARGAR OHLCV ──────────────────────────────────────────
    df = pl.read_parquet(str(path_ohlcv)).sort('date')
    n_filas = len(df)

    # ── DATE DTYPE ────────────────────────────────────────────
    print(f"\n  [DATE]")
    date_dtype = str(df['date'].dtype)
    es_datetime = 'Datetime' in date_dtype
    tiene_utc   = 'UTC' in date_dtype
    print(f"    dtype: {date_dtype} {'✅' if (es_datetime and tiene_utc) else '❌ debe ser Datetime[ms,UTC]'}")
    if not (es_datetime and tiene_utc):
        problemas.append(f'DATE_DTYPE_INCORRECTO:{date_dtype}')
    reporte_activo['ohlcv']['date_dtype'] = date_dtype
    reporte_activo['ohlcv']['date_ok'] = es_datetime and tiene_utc

    fecha_inicio = str(df['date'].min())
    fecha_fin    = str(df['date'].max())
    print(f"    rango: {fecha_inicio} → {fecha_fin}")
    print(f"    filas: {n_filas}")
    reporte_activo['ohlcv']['fecha_inicio'] = fecha_inicio
    reporte_activo['ohlcv']['fecha_fin']    = fecha_fin
    reporte_activo['ohlcv']['n_filas']      = n_filas

    # ── GAPS TEMPORALES ───────────────────────────────────────
    print(f"\n  [GAPS]")
    # Calcular diferencias entre fechas consecutivas
    dates_np = df['date'].to_numpy().astype('datetime64[D]').astype(np.int64)
    diffs = np.diff(dates_np)
    if len(diffs) > 0:
        gap_max_dias = int(diffs.max())
        n_gaps_gt7   = int(np.sum(diffs > 7))
        n_gaps_gt30  = int(np.sum(diffs > 30))
        print(f"    Gap máximo: {gap_max_dias} días | gaps>7d: {n_gaps_gt7} | gaps>30d: {n_gaps_gt30}")
        if gap_max_dias > 30:
            problemas.append(f'GAP_GRANDE:{gap_max_dias}d')
            # Encontrar el gap más grande
            idx_max = int(np.argmax(diffs))
            print(f"    ⚠️  Gap principal: {str(df['date'][idx_max])} → {str(df['date'][idx_max+1])}")
        reporte_activo['ohlcv']['gap_max_dias'] = gap_max_dias
        reporte_activo['ohlcv']['n_gaps_gt7']   = n_gaps_gt7
        reporte_activo['ohlcv']['n_gaps_gt30']  = n_gaps_gt30

    # ── COLUMNAS ──────────────────────────────────────────────
    print(f"\n  [COLUMNAS]")
    cols_presentes = set(df.columns)
    cols_canon = {
        'date', 'open', 'high', 'low', 'close', 'volume',
        'entropy_shannon', 'entropy_decay_lambda', 'entropy_psych_vix',
        'fibonacci_lag_1', 'fibonacci_lag_2', 'fibonacci_lag_3',
        'fibonacci_lag_5', 'fibonacci_lag_8', 'fibonacci_lag_13', 'fibonacci_lag_21',
        'goldstein_geo', 'n_events_ohlcv', 'vitality_tesla',
        'mass_panic_index', 'fear_momentum', 'vix_norm', 'nash_frozen_7d', 'log_return'
    }
    faltantes = cols_canon - cols_presentes
    extras    = cols_presentes - cols_canon
    print(f"    Columnas presentes: {len(cols_presentes)} | Canon: 24 | {'✅' if len(cols_presentes) >= 24 else '⚠️'}")
    if faltantes:
        print(f"    ❌ Faltantes: {sorted(faltantes)}")
        problemas.append(f'COLS_FALTANTES:{len(faltantes)}')
    if extras:
        print(f"    ℹ️  Extras: {sorted(extras)}")
    reporte_activo['ohlcv']['cols_faltantes'] = sorted(faltantes)
    reporte_activo['ohlcv']['n_cols'] = len(cols_presentes)

    # ── AUDITORÍA DE FEATURES ─────────────────────────────────
    print(f"\n  [FEATURES — Auditoría Forense]")
    lr_np = df['log_return'].to_numpy().astype(np.float64)

    # Construir mapa riesgo→features presentes
    features_a_auditar = {}
    for riesgo, feats in FEATURES_RIESGO.items():
        for f in feats:
            if f in cols_presentes:
                features_a_auditar[f] = riesgo

    for feat, riesgo in sorted(features_a_auditar.items(),
                                key=lambda x: ['ALTO', 'MEDIO', 'BAJO'].index(x[1])):
        col_np = df[feat].cast(pl.Float64).to_numpy()
        audit = audit_feature(col_np, lr_np, feat, riesgo)
        reporte_activo['features'][feat] = audit

        # Imprimir línea de resumen
        emoji = '🔴' if 'LEAKAGE' in audit['veredicto'] else '✅'
        extras_str = ''
        if riesgo == 'ALTO' and 'forense_lookahead' in audit:
            f = audit['forense_lookahead']
            extras_str = (f" | diff_media={f['diff_media']:.4f}"
                         f" | rolling_std[0]={f['rolling_std_bar0']:.4f}"
                         f" | corr_fut={f.get('corr_global_con_lr_t1', '?')}")
        print(f"    {emoji} {feat:<28} [{riesgo}] mean={audit['mean']:.4f}"
              f" nonzero={audit['pct_nonzero']:.1f}%{extras_str}")

        if 'LEAKAGE' in audit['veredicto']:
            problemas.append(f'LEAKAGE:{feat}')

    # ── GDELT ─────────────────────────────────────────────────
    print(f"\n  [GDELT]")
    if path_gdelt.exists():
        df_g = pl.read_parquet(str(path_gdelt))
        date_g_dtype = str(df_g['date'].dtype)
        print(f"    filas: {len(df_g)} | date dtype: {date_g_dtype}")
        print(f"    cols: {df_g.columns}")
        g_fecha_inicio = str(df_g['date'].min())
        g_fecha_fin    = str(df_g['date'].max())
        print(f"    rango: {g_fecha_inicio} → {g_fecha_fin}")

        # NaN check en entropy_shannon
        ent_nan = df_g['entropy_shannon'].null_count()
        print(f"    entropy_shannon NaN: {ent_nan} ({100*ent_nan/len(df_g):.1f}%)")

        # Distribución vitality
        if 'vitality_tesla' in df_g.columns:
            vit_dist = dict(df_g.group_by('vitality_tesla').agg(pl.len().alias('n'))
                           .sort('vitality_tesla').iter_rows())
            print(f"    vitality dist: {vit_dist}")

        reporte_activo['gdelt'] = {
            'n_filas': len(df_g),
            'cols': df_g.columns,
            'fecha_inicio': g_fecha_inicio,
            'fecha_fin':    g_fecha_fin,
            'date_dtype':   date_g_dtype,
            'ent_nan': ent_nan,
        }
    else:
        print(f"    ⚠️  No encontrado: {path_gdelt}")
        problemas.append('GDELT_NOT_FOUND')
        reporte_activo['gdelt'] = {'status': 'NOT_FOUND'}

    # ── VEREDICTO FINAL DEL ACTIVO ────────────────────────────
    leakages  = [p for p in problemas if 'LEAKAGE' in p]
    criticos  = [p for p in problemas if any(x in p for x in ['SHA_MISMATCH', 'OHLCV_NOT_FOUND', 'DATE_DTYPE'])]

    if leakages:
        veredicto = f'🔴 LEAKAGE DETECTADO: {", ".join(leakages)}'
    elif criticos:
        veredicto = f'⚠️  PROBLEMAS CRÍTICOS: {", ".join(criticos)}'
    elif problemas:
        veredicto = f'⚠️  AVISOS: {", ".join(problemas)}'
    else:
        veredicto = '✅ LIMPIO — listo para entrenamiento'

    reporte_activo['veredicto_final'] = veredicto
    reporte_activo['problemas'] = problemas
    reporte_global['activos'][activo] = reporte_activo

    print(f"\n  VEREDICTO {activo}: {veredicto}")

# ── RESUMEN GLOBAL ────────────────────────────────────────────
print(f"\n{'=' * 65}")
print("RESUMEN GLOBAL")
print(f"{'=' * 65}")
todos_limpios = True
for activo in ACTIVOS:
    v = reporte_global['activos'].get(activo, {}).get('veredicto_final', 'ERROR')
    print(f"  {activo:<10} {v}")
    if '🔴' in v or '⚠️' in v:
        todos_limpios = False

if todos_limpios:
    print("\n  ✅ TODOS LOS DATASETS LIMPIOS — safe to train")
    reporte_global['veredicto_global'] = 'LIMPIO'
else:
    print("\n  ⚠️  HAY PROBLEMAS — revisar antes de entrenar")
    reporte_global['veredicto_global'] = 'REVISAR'

# ── GUARDAR REPORTE ───────────────────────────────────────────
OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
with open(OUT_JSON, 'w') as f:
    json.dump(reporte_global, f, indent=2, ensure_ascii=False, default=str)
print(f"\n  Reporte guardado: {OUT_JSON}")
print(f"\n  Siguiente paso: python spel_p90_recalibrate.py")
print(f"  Luego:          python spel_retrain_v5_clean.py")
