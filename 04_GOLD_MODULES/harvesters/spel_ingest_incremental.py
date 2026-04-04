"""
SPEL — Ingest Incremental S24
Llena el gap 2026-01-01 → hoy sin modificar gdelt_foundation.py (R6).

Hace dos cosas por activo:
  1. OHLCV: descarga velas diarias nuevas desde yfinance y hace append
  2. GDELT: descarga eventos del periodo y calcula features de entropía

Uso: exec(open('spel_ingest_incremental.py').read())
     ingest_all()
"""

import sys, json, hashlib, requests, zipfile, io, time
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path

import polars as pl
import yfinance as yf

ROOT      = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
CORE      = ROOT / 'codigo/core'
META_PATH = ROOT / 'meta/SPEL_META.json'
SHA_PATH  = ROOT / 'meta/SHA_REGISTRY.json'

sys.path.insert(0, str(CORE))

# ── CONFIGURACIÓN POR ACTIVO ──────────────────────────────────
ASSET_CONFIG = {
    'BTC': {
        'ticker':   'BTC-USD',
        'keywords': ['bitcoin', 'crypto', 'BTC', 'cryptocurrency',
                     'blockchain', 'digital asset', 'coinbase'],
    },
    'XAU': {
        'ticker':   'GC=F',
        'keywords': ['gold', 'XAU', 'precious metals', 'safe haven',
                     'federal reserve', 'inflation', 'geopolitical'],
    },
    'NIFTY50': {
        'ticker':   '^NSEI',
        'keywords': ['india', 'nifty', 'sensex', 'BSE', 'NSE',
                     'RBI', 'rupee', 'indian economy'],
    },
    'NVDA': {
        'ticker':   'NVDA',
        'keywords': ['nvidia', 'NVDA', 'GPU', 'artificial intelligence',
                     'semiconductors', 'data center', 'jensen huang'],
    },
}

TENSOR_COLS = [
    'high','low','log_return',
    'entropy_shannon','entropy_decay_lambda','entropy_psych_vix',
    'fibonacci_lag_1','fibonacci_lag_2','fibonacci_lag_3',
    'fibonacci_lag_5','fibonacci_lag_8','fibonacci_lag_13','fibonacci_lag_21',
    'goldstein_geo','n_events_ohlcv','vitality_tesla',
    'mass_panic_index','fear_momentum','vix_norm','nash_frozen_7d',
]

def sha12(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:12]

def update_sha_registry(asset: str, path: Path):
    reg = json.load(open(SHA_PATH))
    reg[asset]['sha_v5']  = sha12(str(path))
    reg[asset]['updated'] = datetime.now(timezone.utc).isoformat()
    json.dump(reg, open(SHA_PATH,'w'), indent=2)
    return reg[asset]['sha_v5']

# ══════════════════════════════════════════════════════════════
# MÓDULO 1 — OHLCV INCREMENTAL
# ══════════════════════════════════════════════════════════════

def fetch_ohlcv_new(ticker: str, start: datetime, end: datetime) -> pl.DataFrame:
    """Descarga OHLCV diario desde yfinance para el rango [start, end]."""
    start_str = start.strftime('%Y-%m-%d')
    end_str   = end.strftime('%Y-%m-%d')

    print(f"    yfinance: {ticker} {start_str} → {end_str}")
    raw = yf.download(ticker, start=start_str, end=end_str,
                      auto_adjust=True, progress=False)

    if raw.empty:
        print(f"    ⚠️  yfinance retornó vacío para {ticker}")
        return None

    # Aplanar columnas MultiIndex si existen
    if hasattr(raw.columns, 'levels'):
        raw.columns = raw.columns.get_level_values(0)

    df = pl.DataFrame({
        'date':   pl.Series(raw.index.tz_localize('UTC') if raw.index.tz is None
                            else raw.index.tz_convert('UTC')).cast(pl.Datetime('ms','UTC')),
        'open':   raw['Open'].values.astype(float),
        'high':   raw['High'].values.astype(float),
        'low':    raw['Low'].values.astype(float),
        'close':  raw['Close'].values.astype(float),
        'volume': raw['Volume'].values.astype(float),
    }).filter(
        pl.col('close').is_not_null() & (pl.col('close') > 0)
    )

    print(f"    Filas descargadas: {len(df)}")
    return df

# ══════════════════════════════════════════════════════════════
# MÓDULO 2 — GDELT INCREMENTAL
# ══════════════════════════════════════════════════════════════

def fetch_gdelt_day(date: datetime, keywords: list) -> dict:
    """
    Descarga eventos GDELT de un día y calcula features de entropía.
    No modifica gdelt_foundation.py (R6) — pipeline independiente.
    """
    date_str = date.strftime('%Y%m%d')
    url      = f"http://data.gdeltproject.org/gdeltv2/{date_str}000000.export.CSV.zip"

    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                content = f.read().decode('latin-1')

        # Parsear CSV GDELT (15 columnas mínimo)
        rows = []
        for line in content.strip().split('\n'):
            cols = line.split('\t')
            if len(cols) >= 15:
                rows.append(cols)

        if not rows:
            return None

        # Filtrar por keywords (columna Actor1Name + Actor2Name)
        keyword_lower = [k.lower() for k in keywords]
        filtered = [
            row for row in rows
            if any(kw in (row[6] + row[17]).lower() for kw in keyword_lower)
        ] if len(rows[0]) > 17 else rows

        # Si no hay filas filtradas, usar todas (señal débil pero presente)
        events = filtered if len(filtered) >= 5 else rows

        # Goldstein Scale (col 30 si existe)
        goldstein_vals = []
        for row in events:
            try:
                if len(row) > 30 and row[30]:
                    goldstein_vals.append(float(row[30]))
            except (ValueError, IndexError):
                pass

        goldstein_mean = float(np.mean(goldstein_vals)) if goldstein_vals else 1.845

        # AvgTone (col 34 si existe)
        tone_vals = []
        for row in events:
            try:
                if len(row) > 34 and row[34]:
                    tone_vals.append(float(row[34]))
            except (ValueError, IndexError):
                pass

        tone_variance = float(np.var(tone_vals)) if len(tone_vals) > 1 else 117.0

        n_events = len(events)

        # Entropy Shannon sobre distribución de tone
        if tone_vals:
            hist, _ = np.histogram(tone_vals, bins=20, density=True)
            hist    = hist[hist > 0]
            entropy = float(-np.sum(hist * np.log(hist + 1e-10)))
        else:
            entropy = 1.0

        # Zipf concentration
        if n_events > 0:
            event_types = {}
            for row in events:
                try:
                    et = row[26] if len(row) > 26 else 'UNK'
                    event_types[et] = event_types.get(et, 0) + 1
                except IndexError:
                    pass
            if event_types:
                counts = np.array(sorted(event_types.values(), reverse=True), dtype=float)
                zipf = float(counts[0] / counts.sum()) if counts.sum() > 0 else 0.0002
            else:
                zipf = 0.0002
        else:
            zipf = 0.0002

        return {
            'n_events':       n_events,
            'entropy_raw':    entropy,
            'goldstein_mean': goldstein_mean,
            'tone_variance':  tone_variance,
            'zipf':           zipf,
        }

    except Exception as e:
        print(f"    ⚠️  GDELT {date_str}: {e}")
        return None


def compute_entropy_features(gdelt_window: list, p90: float) -> dict:
    """
    Calcula todas las features de entropía para un día dado un historial de N días.
    Replica la lógica de gdelt_foundation.py sin modificarla.
    """
    if not gdelt_window:
        return None

    current = gdelt_window[-1]
    entropies = [d['entropy_raw'] for d in gdelt_window if d]

    entropy_shannon = current['entropy_raw']

    # entropy_decay_lambda: decay exponencial de la entropía en la ventana
    if len(entropies) >= 7:
        recent = np.array(entropies[-7:])
        if recent.std() > 1e-6:
            entropy_decay_lambda = float(np.polyfit(np.arange(7), recent, 1)[0] + 1.1)
        else:
            entropy_decay_lambda = 1.093
    else:
        entropy_decay_lambda = 1.093

    entropy_decay_lambda = float(np.clip(entropy_decay_lambda, 0.9, 1.3))

    # entropy_psych_vix: volatilidad de la entropía en ventana 7d
    entropy_psych_vix = float(np.std(entropies[-7:]) * 10) if len(entropies) >= 7 else 0.1

    # nash_frozen_7d: qué tan estable está la entropía (compresión)
    if len(entropies) >= 7:
        nash_frozen_7d = float(1.0 - (np.std(entropies[-7:]) / (np.mean(entropies[-7:]) + 1e-9)))
    else:
        nash_frozen_7d = 0.5
    nash_frozen_7d = float(np.clip(nash_frozen_7d, 0.0, 1.0))

    # vitality_tesla: tertil de n_events
    n_events_window = [d['n_events'] for d in gdelt_window if d]
    if len(n_events_window) >= 3:
        p33 = np.percentile(n_events_window, 33)
        p66 = np.percentile(n_events_window, 66)
        n   = current['n_events']
        vitality_tesla = 3 if n <= p33 else (6 if n <= p66 else 9)
    else:
        vitality_tesla = 6

    # mass_panic_index: entropy_shannon normalizada sobre ventana
    if len(entropies) >= 7:
        mass_panic_index = float((entropy_shannon - np.mean(entropies[-7:])) /
                                  (np.std(entropies[-7:]) + 1e-9))
    else:
        mass_panic_index = 0.0
    mass_panic_index = float(np.clip(mass_panic_index, -3.0, 3.0))

    # fear_momentum: derivada de la entropía
    if len(entropies) >= 2:
        fear_momentum = float(entropies[-1] - entropies[-2])
    else:
        fear_momentum = 0.0

    # vix_norm: tone_variance normalizada
    tone_vars = [d['tone_variance'] for d in gdelt_window if d]
    if len(tone_vars) >= 7:
        vix_norm = float(current['tone_variance'] / (np.mean(tone_vars[-7:]) + 1e-9))
    else:
        vix_norm = 1.0
    vix_norm = float(np.clip(vix_norm, 0.0, 5.0))

    return {
        'entropy_shannon':     entropy_shannon,
        'entropy_decay_lambda': entropy_decay_lambda,
        'entropy_psych_vix':   entropy_psych_vix,
        'goldstein_geo':       current['goldstein_mean'],
        'n_events_ohlcv':      float(current['n_events']),
        'vitality_tesla':      float(vitality_tesla),
        'mass_panic_index':    mass_panic_index,
        'fear_momentum':       fear_momentum,
        'vix_norm':            vix_norm,
        'nash_frozen_7d':      nash_frozen_7d,
        'goldstein_mean':      current['goldstein_mean'],
        'tone_variance':       current['tone_variance'],
        'zipf_concentration':  current['zipf'],
    }


# ══════════════════════════════════════════════════════════════
# MÓDULO 3 — FIBONACCI LAGS
# ══════════════════════════════════════════════════════════════

def add_fibonacci_lags(df: pl.DataFrame) -> pl.DataFrame:
    """Añade fibonacci_lag_N sobre log_return. Requiere historia suficiente."""
    for lag in [1, 2, 3, 5, 8, 13, 21]:
        df = df.with_columns(
            pl.col('log_return').shift(lag).alias(f'fibonacci_lag_{lag}')
        )
    return df


# ══════════════════════════════════════════════════════════════
# ORQUESTADOR POR ACTIVO
# ══════════════════════════════════════════════════════════════

def ingest_asset(asset: str) -> bool:
    print(f"\n{'═'*55}")
    print(f"  Ingest incremental: {asset}")
    print(f"{'═'*55}")

    cfg      = ASSET_CONFIG[asset]
    pq_path  = ROOT / f'data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
    reg      = json.load(open(SHA_PATH))

    # Verificar SHA antes de modificar (R3)
    sha_before = sha12(str(pq_path))
    if sha_before != reg[asset]['sha_v5']:
        print(f"  ❌ SHA mismatch antes de ingest — ABORT")
        return False

    # Leer parquet existente
    df_existing = pl.read_parquet(str(pq_path)).sort('date')
    last_date   = df_existing['date'].max()
    today       = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_date  = last_date.replace(tzinfo=timezone.utc) + timedelta(days=1)

    if start_date >= today:
        print(f"  ✅ Ya actualizado — sin gap")
        return True

    gap_days = (today - start_date).days
    print(f"  Gap: {start_date.date()} → {today.date()} ({gap_days} días)")

    # ── OHLCV nuevo ──────────────────────────────────────────
    print(f"\n  [1/3] OHLCV")
    df_new_ohlcv = fetch_ohlcv_new(cfg['ticker'], start_date, today)
    if df_new_ohlcv is None or len(df_new_ohlcv) == 0:
        print(f"  ⚠️  Sin datos OHLCV nuevos — puede ser fin de semana o mercado cerrado")
        return False

    # ── GDELT nuevo ──────────────────────────────────────────
    print(f"\n  [2/3] GDELT")
    new_dates    = df_new_ohlcv['date'].to_list()
    gdelt_cache  = []  # ventana histórica para calcular features

    # Cargar últimos 30 días de GDELT histórico para ventana
    df_hist_gdelt = df_existing.tail(30)
    hist_window   = []
    for row in df_hist_gdelt.to_dicts():
        hist_window.append({
            'entropy_raw':    row.get('entropy_shannon', 1.0),
            'n_events':       row.get('n_events_ohlcv', 100),
            'goldstein_mean': row.get('goldstein_mean', 1.845),
            'tone_variance':  row.get('tone_variance', 117.0),
            'zipf':           row.get('zipf_concentration', 0.0002),
        })

    gdelt_results = []
    for date in new_dates:
        date_py = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
        day_data = fetch_gdelt_day(date_py, cfg['keywords'])

        if day_data is None:
            # Fallback: interpolar del día anterior
            day_data = hist_window[-1].copy() if hist_window else {
                'entropy_raw': 1.0, 'n_events': 50,
                'goldstein_mean': 1.845, 'tone_variance': 117.0, 'zipf': 0.0002
            }
            print(f"    {date_py.date()}: usando fallback (GDELT sin datos)")

        hist_window.append(day_data)
        p90 = reg[asset]['p90_entropy']
        features = compute_entropy_features(hist_window[-30:], p90)
        gdelt_results.append(features)
        time.sleep(0.3)  # Rate limiting GDELT

    # ── Construir nuevas filas ────────────────────────────────
    print(f"\n  [3/3] Construir y append")

    vol_type   = df_existing['volume_type'][0]
    asset_cls  = df_existing['asset_class'][0]
    trade_sess = df_existing['trading_session'][0]

    new_rows = []
    for i, row_ohlcv in enumerate(df_new_ohlcv.to_dicts()):
        if i >= len(gdelt_results) or gdelt_results[i] is None:
            continue

        g     = gdelt_results[i]
        close = row_ohlcv['close']
        prev  = df_existing['close'][-1] if i == 0 else new_rows[-1]['close']

        log_ret = float(np.log(close / prev)) if prev > 0 else 0.0

        new_row = {
            'date':             row_ohlcv['date'],
            'open':             row_ohlcv['open'],
            'high':             row_ohlcv['high'],
            'low':              row_ohlcv['low'],
            'close':            close,
            'volume':           row_ohlcv['volume'],
            'volume_type':      vol_type,
            'asset_class':      asset_cls,
            'trading_session':  trade_sess,
            'log_return':       log_ret,
            **g,
        }
        # Fibonacci lags — usar historia existente
        all_returns = df_existing['log_return'].to_list() + \
                      [r['log_return'] for r in new_rows] + [log_ret]
        for lag in [1,2,3,5,8,13,21]:
            idx = len(all_returns) - 1 - lag
            new_row[f'fibonacci_lag_{lag}'] = all_returns[idx] if idx >= 0 else 0.0

        new_rows.append(new_row)

    if not new_rows:
        print(f"  ⚠️  Sin filas nuevas para append")
        return False

    # Construir DataFrame nuevo con schema idéntico al existente
    df_new = pl.DataFrame(new_rows).select(df_existing.columns)

    # FIX RAIZ: castear TODOS los dtypes al schema canónico del parquet existente
    # Resuelve Float32/Float64 y Datetime ms/μs en una sola pasada
    # Dinámico — no necesita mantenimiento si yfinance cambia dtypes
    cast_exprs = [
        pl.col(col).cast(df_existing[col].dtype)
        for col in df_existing.columns
        if col in df_new.columns
    ]
    df_new = df_new.with_columns(cast_exprs)

    # Append y guardar
    df_updated = pl.concat([df_existing, df_new]).sort('date').unique('date')
    df_updated.write_parquet(str(pq_path))

    # Actualizar SHA
    new_sha = update_sha_registry(asset, pq_path)

    n_added = len(df_updated) - len(df_existing)
    print(f"  ✅ {asset}: +{n_added} filas | nueva SHA: {new_sha}")
    print(f"     Rango: {df_updated['date'].min()} → {df_updated['date'].max()}")

    return True


# ══════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════

def ingest_all():
    print(f"\n{'═'*55}")
    print(f"  SPEL Ingest Incremental S24")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*55}")

    results = {}
    for asset in ['BTC', 'XAU', 'NIFTY50', 'NVDA']:
        ok = ingest_asset(asset)
        results[asset] = ok

    print(f"\n\n{'═'*55}")
    print(f"  RESUMEN INGEST")
    print(f"{'═'*55}")
    for asset, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {asset}")

    # Verificar preflight post-ingest
    print(f"\n  Verificando SHA post-ingest...")
    reg = json.load(open(SHA_PATH))
    pq_root = ROOT / 'data_lake'
    all_ok  = True
    for asset in ['BTC','XAU','NIFTY50','NVDA']:
        pq = pq_root / f'{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
        sha = sha12(str(pq))
        exp = reg[asset]['sha_v5']
        ok  = sha == exp
        all_ok = all_ok and ok
        print(f"  {'✅' if ok else '❌'} {asset}: {sha}")

    if all_ok:
        print(f"\n  ✅ SHA_REGISTRY sincronizado — sistema listo para LIVE")
    else:
        print(f"\n  ❌ SHA mismatch post-ingest — revisar antes de inferir")

    return all_ok


if __name__ == '__main__':
    ingest_all()
