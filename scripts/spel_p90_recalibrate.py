"""
SPEL — Recalibración de Umbrales P90 para Gödel Bound
======================================================
Calcula los percentiles reales de entropy_shannon y vitality_tesla
SOLO con datos <= 2023-12-31 (anti-leakage estricto).

Fuente: GDELT entropy parquets en SPEL-v2.0
Output: godel_thresholds_v2.json  +  informe por pantalla

Regla R3: verificar SHA antes de cualquier cálculo.
Ejecutar en Colab con Drive montado.
"""

import json, hashlib
from pathlib import Path
from datetime import datetime
import polars as pl
import numpy as np

# ── CONFIGURACIÓN ─────────────────────────────────────────────
RAIZ     = Path('/content/drive/MyDrive/SPEL-v2.0')
ACTIVOS  = ['NVDA', 'BTC', 'XAU', 'NIFTY50']
CUTOFF   = '2023-12-31'          # techo anti-leakage (NUNCA cambiar)
OUT_JSON = RAIZ / 'meta' / 'godel_thresholds_v2.json'

SHA_ESPERADOS = {
    'NVDA':    'f496c377c7ae',
    'BTC':     '899052347d73',
    'XAU':     'd3acbf6342bc',
    'NIFTY50': '981989b7024d',
}

RUTA_GDELT = lambda a: RAIZ / 'data_lake' / a / 'gdelt' / 'raw' / f'{a}_gdelt_entropy.parquet'
RUTA_OHLCV = lambda a: RAIZ / 'data_lake' / a / 'ohlcv' / 'aggregated' / f'{a}_ohlcv_v5.parquet'

# ── HELPERS ───────────────────────────────────────────────────
def sha12(path: Path) -> str:
    """SHA-256 primeros 12 caracteres del archivo."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()[:12]


def verificar_sha(activo: str) -> bool:
    p = RUTA_OHLCV(activo)
    if not p.exists():
        print(f"  ❌ {activo}: parquet OHLCV no encontrado en {p}")
        return False
    sha = sha12(p)
    esperado = SHA_ESPERADOS[activo]
    ok = sha == esperado
    print(f"  {'✅' if ok else '❌'} {activo} SHA: {sha} {'== OK' if ok else f'!= {esperado} (MISMATCH)'}")
    return ok


def percentil(series: pl.Series, p: float) -> float:
    """Percentil p (0-100) sobre una Serie Polars, excluyendo nulos."""
    arr = series.drop_nulls().to_numpy()
    if len(arr) == 0:
        return float('nan')
    return float(np.percentile(arr, p))


# ── VERIFICACIÓN SHA ──────────────────────────────────────────
print("=" * 60)
print("SPEL — Recalibración P90 Gödel")
print(f"Cutoff anti-leakage: {CUTOFF}")
print("=" * 60)
print("\n[1/3] Verificando SHA de parquets OHLCV...")
sha_ok = {a: verificar_sha(a) for a in ACTIVOS}
if not all(sha_ok.values()):
    print("\n🔴 SHA mismatch detectado. Abortar y verificar integridad.")
    raise SystemExit(1)
print("  SHA 4/4 OK ✅\n")

# ── CÁLCULO DE P90 ────────────────────────────────────────────
print("[2/3] Calculando percentiles con datos <= 2023-12-31...\n")
resultados = {}

for activo in ACTIVOS:
    print(f"  ── {activo} ──────────────────────────────────")
    path_gdelt = RUTA_GDELT(activo)
    path_ohlcv = RUTA_OHLCV(activo)

    # ── GDELT (fuente principal de entropy_shannon y vitality_tesla) ──
    if path_gdelt.exists():
        df_g = pl.read_parquet(str(path_gdelt))

        # Asegurar datetime
        if df_g['date'].dtype == pl.Utf8:
            df_g = df_g.with_columns(pl.col('date').str.strptime(pl.Date, '%Y-%m-%d'))

        # Filtro anti-leakage estricto
        df_g = df_g.filter(pl.col('date') <= pl.lit(CUTOFF).str.strptime(pl.Date, '%Y-%m-%d'))

        n_filas_total  = len(pl.read_parquet(str(path_gdelt)))
        n_filas_train  = len(df_g)
        pct_usado      = 100 * n_filas_train / n_filas_total

        print(f"    GDELT total: {n_filas_total} filas | usadas (≤{CUTOFF}): {n_filas_train} ({pct_usado:.1f}%)")

        ent_series = df_g['entropy_shannon']
        vit_series = df_g['vitality_tesla']

        # Percentiles de entropía
        p50_ent = percentil(ent_series, 50)
        p75_ent = percentil(ent_series, 75)
        p90_ent = percentil(ent_series, 90)
        p95_ent = percentil(ent_series, 95)
        p99_ent = percentil(ent_series, 99)
        mean_ent = float(ent_series.drop_nulls().mean())
        std_ent  = float(ent_series.drop_nulls().std())

        # Distribución vitality (escala 3/6/9)
        vit_counts = df_g.group_by('vitality_tesla').agg(pl.len().alias('n')).sort('vitality_tesla')
        vit_dist = {str(int(row['vitality_tesla'])): int(row['n']) for row in vit_counts.iter_rows(named=True)}
        pct_v9 = 100 * vit_dist.get('9', 0) / n_filas_train

        # Gödel activations con P90 actual vs nuevo
        p90_viejo = {'NVDA': 1.1898, 'BTC': 1.35, 'XAU': 0.95, 'NIFTY50': 1.18}[activo]
        act_viejo = df_g.filter(
            (pl.col('entropy_shannon') >= p90_viejo) | (pl.col('vitality_tesla') == 9)
        ).height / n_filas_train * 100
        act_nuevo = df_g.filter(
            (pl.col('entropy_shannon') >= p90_ent) | (pl.col('vitality_tesla') == 9)
        ).height / n_filas_train * 100

        print(f"    entropy_shannon: mean={mean_ent:.4f} std={std_ent:.4f}")
        print(f"      P50={p50_ent:.4f}  P75={p75_ent:.4f}  P90={p90_ent:.4f}  P95={p95_ent:.4f}  P99={p99_ent:.4f}")
        print(f"    vitality dist: {vit_dist} | vitality==9: {pct_v9:.1f}%")
        print(f"    Gödel activations:")
        print(f"      P90 anterior ({p90_viejo:.4f}): {act_viejo:.1f}% {'⚠️ DEMASIADO ALTO' if act_viejo > 20 else '✅'}")
        print(f"      P90 nuevo    ({p90_ent:.4f}): {act_nuevo:.1f}% {'✅' if 1 <= act_nuevo <= 15 else '⚠️'}")

        # Recomendación de umbral
        # Objetivo: Gödel activa entre 5-15% del tiempo
        # Si vitality==9 ya cubre > 10%, el umbral de entropía es secundario
        umbral_recomendado = p90_ent
        nota_umbral = "P90 real calculado"
        if pct_v9 >= 10:
            nota_umbral += f" (vitality==9 ya cubre {pct_v9:.1f}% — entropía es filtro secundario)"

        resultados[activo] = {
            'entropy_p50': round(p50_ent, 6),
            'entropy_p75': round(p75_ent, 6),
            'entropy_p90': round(p90_ent, 6),   # ← UMBRAL GÖDEL RECOMENDADO
            'entropy_p95': round(p95_ent, 6),
            'entropy_p99': round(p99_ent, 6),
            'entropy_mean': round(mean_ent, 6),
            'entropy_std':  round(std_ent, 6),
            'vitality_dist': vit_dist,
            'pct_vitality_9': round(pct_v9, 2),
            'p90_anterior': p90_viejo,
            'godel_activations_anterior_pct': round(act_viejo, 2),
            'godel_activations_nuevo_pct':    round(act_nuevo, 2),
            'umbral_recomendado': round(umbral_recomendado, 6),
            'nota': nota_umbral,
            'filas_usadas': n_filas_train,
            'cutoff': CUTOFF,
        }
        print(f"    → UMBRAL GÖDEL RECOMENDADO: {umbral_recomendado:.4f}")

    else:
        print(f"    ⚠️ GDELT no encontrado: {path_gdelt}")
        print(f"    → Usando OHLCV como fallback (entropy_shannon en canon_v4)")

        df_o = pl.read_parquet(str(path_ohlcv))
        if df_o['date'].dtype != pl.Datetime:
            df_o = df_o.with_columns(pl.col('date').cast(pl.Datetime('ms', 'UTC')))
        df_o = df_o.filter(pl.col('date') < pl.lit(f'{CUTOFF} 23:59:59').str.strptime(pl.Datetime, '%Y-%m-%d %H:%M:%S'))

        ent_s = df_o['entropy_shannon']
        p90_ent = percentil(ent_s, 90)
        resultados[activo] = {
            'entropy_p90': round(p90_ent, 6),
            'source': 'OHLCV_fallback',
            'nota': 'GDELT no disponible — usar con precaución',
            'cutoff': CUTOFF,
        }
        print(f"    → P90 fallback (OHLCV): {p90_ent:.4f}")

    print()

# ── GUARDAR JSON ──────────────────────────────────────────────
print("[3/3] Guardando resultados...\n")
OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
output = {
    '_meta': {
        'generado': datetime.utcnow().isoformat(),
        'cutoff_anti_leakage': CUTOFF,
        'descripcion': 'Percentiles de entropy_shannon y vitality_tesla calculados con datos <= 2023-12-31. Usar entropy_p90 como umbral en godel_bound.py.',
        'condicion_godel': 'entropy_shannon[t-1] >= entropy_p90  OR  vitality_tesla[t-1] == 9',
        'regla_R8': 'Siempre OR — nunca AND',
        'objetivo_activaciones': '5-15% del tiempo total',
    },
    'umbrales': resultados
}
with open(OUT_JSON, 'w') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"  ✅ Guardado en: {OUT_JSON}\n")

# ── RESUMEN FINAL ─────────────────────────────────────────────
print("=" * 60)
print("RESUMEN — Comparación P90 anterior vs nuevo")
print("=" * 60)
print(f"  {'Activo':<10} {'P90 anterior':>14} {'P90 nuevo':>12} {'Gödel viejo%':>14} {'Gödel nuevo%':>13} {'Estado'}")
print("  " + "-" * 72)
for activo, r in resultados.items():
    p_viejo = r.get('p90_anterior', '?')
    p_nuevo = r.get('entropy_p90', '?')
    g_viejo = r.get('godel_activations_anterior_pct', '?')
    g_nuevo = r.get('godel_activations_nuevo_pct', '?')
    estado  = '✅' if isinstance(g_nuevo, float) and 1 <= g_nuevo <= 20 else '⚠️ revisar'
    print(f"  {activo:<10} {str(p_viejo):>14} {str(p_nuevo):>12} {str(g_viejo):>13}% {str(g_nuevo):>12}% {estado}")

print()
print("SIGUIENTE PASO:")
print("  1. Verificar que Gödel activaciones nuevo% esté en rango 5-15%")
print("  2. Copiar los entropy_p90 al archivo godel_bound.py:")
print()
print("  # En godel_bound.py — sección P90_THRESHOLDS:")
for activo, r in resultados.items():
    p = r.get('entropy_p90', 'N/A')
    print(f"  '{activo}': {p},")
print()
print("  3. Verificar con: python -c \"from godel_bound import *; print(test_covid())\"")
print(f"\n✅ Recalibración completa. Archivo: {OUT_JSON.name}")
