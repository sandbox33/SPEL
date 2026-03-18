"""
SPEL — Score Engine v1.0 (S25)
Conecta el pipeline completo: parquet → MathEngine → Backbone → Router → ScoreResult

Uso en Colab (después de spel_session_start.py):
    exec(open('/content/drive/MyDrive/SPEL-v2.0/scripts/spel_score_engine.py').read())
    result = score('NVDA')
    result = score('BTC')
    score_all()
"""

import sys, json, hashlib, inspect, re
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import polars as pl
import torch

# ── PATCH ATR14 (Polars >=1.21 fix) ─────────────────────────
# zip_with() requiere Series no Expr — patch aplicado en carga
def _atr14_fixed(df_price):
    import numpy as _np
    required = {'high', 'low', 'close'}
    missing  = required - set(df_price.columns)
    if missing:
        raise ValueError(f'df_price falta columnas: {missing}')
    high, low, close = df_price['high'], df_price['low'], df_price['close']
    prev_close = close.shift(1)
    hl_arr  = (high - low).to_numpy()
    hpc_arr = _np.abs((high - prev_close).fill_null(0)).to_numpy()
    lpc_arr = _np.abs((low  - prev_close).fill_null(0)).to_numpy()
    tr_arr    = _np.maximum(_np.maximum(hl_arr, hpc_arr), lpc_arr)
    tr_arr[0] = _np.nan
    ATR_PERIOD = 14
    alpha = 1.0 / ATR_PERIOD
    atr   = _np.full_like(tr_arr, _np.nan)
    seed_idx   = ATR_PERIOD - 1
    valid_seed = tr_arr[1:ATR_PERIOD + 1]
    if len(valid_seed) < ATR_PERIOD or _np.any(_np.isnan(valid_seed)):
        return pl.Series('atr14', atr)
    atr[seed_idx] = float(_np.nanmean(valid_seed))
    for i in range(seed_idx + 1, len(tr_arr)):
        atr[i] = atr[i-1] if _np.isnan(tr_arr[i])                  else alpha * tr_arr[i] + (1.0 - alpha) * atr[i-1]
    return pl.Series('atr14', atr)

try:
    import spel_backbone_engine as _bb
    _bb._compute_atr14_vectorized = _atr14_fixed
except Exception:
    pass
# ─────────────────────────────────────────────────────────────


ROOT = Path('/content/drive/MyDrive/SPEL-v2.0')
sys.path.insert(0, str(ROOT / 'codigo/core'))

from spel_math_engine     import SPELMathEngine
from spel_backbone_engine import SPELBackbone, BackboneSignal
from spel_trading_router  import SPELTradingRouter, DecisionTrading

def sha12(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:12]

# ── CONFIGURACIÓN ─────────────────────────────────────────────
ASSETS    = ['BTC', 'XAU', 'NIFTY50', 'NVDA']
LOOKBACKS = {'BTC': 21, 'XAU': 63, 'NIFTY50': 42, 'NVDA': 63}

# Score de Oro weights por volume_type (R16/R17)
SCORE_WEIGHTS = {
    'NATIVE_FUTURES':  {'godel': 0.40, 'te': 0.30, 'vol': 0.30},
    'SPOT_CRYPTO':     {'godel': 0.40, 'te': 0.30, 'vol': 0.30},
    'SYNTHETIC_INDEX': {'godel': 0.55, 'te': 0.45, 'vol': 0.00},
    'TICK_PROXY':      {'godel': 0.50, 'te': 0.35, 'vol': 0.15},
}

# ── RESULTADO ─────────────────────────────────────────────────
@dataclass
class ScoreResult:
    asset:          str
    score_oro:      int           # 0-100 Score de Oro
    godel_active:   bool
    direction:      str           # 'LONG' | 'SHORT' | 'NEUTRAL'
    natural_score:  float         # BackboneSignal.natural_score
    kelly_fraction: float         # nunca > 0.25
    viable:         bool
    modo:           str           # ModoTrading
    razon:          list
    hurst:          float
    te_gov:         float
    te_bus:         float
    entropy:        float
    p90:            float
    sha_parquet:    str
    timestamp_utc:  str
    backbone:       Optional[BackboneSignal] = None
    decision:       Optional[DecisionTrading] = None

    def summary(self):
        sep = '═' * 55
        icon = '🟢' if self.viable else ('🟡' if self.score_oro >= 60 else '⛔')
        print(f"\n{sep}")
        print(f"  {self.asset}  |  Score de Oro: {self.score_oro}/100  {icon}")
        print(f"  Modo: {self.modo}  |  Dirección: {self.direction}")
        print(f"  Gödel: {'✅ ACTIVO' if self.godel_active else '○ inactivo'}"
              f"  |  entropy={self.entropy:.4f} (P90={self.p90:.4f})")
        print(f"  Hurst: {self.hurst:.3f}  |  TE_gov: {self.te_gov:.4f}"
              f"  |  TE_bus: {self.te_bus:.4f}")
        print(f"  Natural score: {self.natural_score:.4f}"
              f"  |  Kelly: {self.kelly_fraction:.4f}")
        print(f"  Viable: {'✅ SÍ' if self.viable else '❌ NO'}")
        for r in self.razon:
            print(f"  > {r}")
        print(sep)

# ── HELPERS ───────────────────────────────────────────────────

def _build_alerts_df_from_parquet(df: pl.DataFrame,
                                   asset: str) -> pl.DataFrame:
    """
    Construye alerts_df con las 14 columnas canónicas que SPELBackbone.evaluate()
    espera, usando los datos del parquet v5.1.

    Cuando MathEngine.run() no puede ejecutarse (sin LazyFrames GDELT separados),
    derivamos las columnas requeridas directamente del parquet.
    """
    df = df.filter(pl.col('close').is_not_null() & ~pl.col('close').is_nan() & pl.col('high').is_not_null() & pl.col('low').is_not_null())
    last = df.tail(1).to_dicts()[0]

    # Hurst: usar entropy_decay_lambda como proxy (H>0.5 = tendencia)
    # entropy_decay_lambda ~ 1.09 en calma, sube en tendencia
    decay = float(last.get('entropy_decay_lambda', 1.093))
    hurst = float(np.clip(0.5 + (decay - 1.093) * 2.0, 0.1, 0.9))

    # Transfer Entropy: usar nash_frozen y fear_momentum como proxy
    nash    = float(last.get('nash_frozen_7d', 0.5))
    fear    = float(last.get('fear_momentum', 0.0))
    te_gov  = float(np.clip(abs(fear) * 0.3 + nash * 0.1, 0.0, 2.0))
    te_bus  = float(np.clip(abs(fear) * 0.2, 0.0, 2.0))

    entropy_val  = float(last.get('entropy_shannon', 1.0))
    vitality     = int(last.get('vitality_tesla', 3))
    mass_panic   = float(last.get('mass_panic_index', 0.0))
    log_ret      = float(last.get('log_return', 0.0))

    reg    = json.load(open(ROOT / 'meta/SHA_REGISTRY.json'))
    p90    = reg[asset]['p90_entropy']
    godel  = (entropy_val >= p90) or (vitality == 9)

    # spillover: hay spillover si TE > umbral mínimo
    spillover = te_gov > 0.05 or te_bus > 0.05

    # anomaly_score: basado en desviación de la entropía respecto al P90
    anomaly_score = float(np.clip((entropy_val - p90) / (p90 * 0.1 + 1e-9), 0, 3))

    # market_regime string
    if godel and vitality == 9:
        regime = 'HIGH_ENTROPY_CRISIS'
    elif godel:
        regime = 'HIGH_ENTROPY'
    elif nash > 0.75:
        regime = 'COMPRESSION'
    else:
        regime = 'NORMAL'

    # Construir el DataFrame de una sola fila que Backbone espera
    alerts_df = pl.DataFrame({
        'date':               [last.get('date', datetime.now(timezone.utc))],
        'hurst':              [hurst],
        'hurst_dfa':          [hurst],
        'te_gov':             [te_gov],
        'te_bus':             [te_bus],
        'spillover_detected': [spillover],
        'anomaly_type':       ['ENTROPY_SPIKE' if godel else 'NORMAL'],
        'anomaly_score':      [anomaly_score],
        'godel_signal':       [godel],
        'market_regime':      [regime],
        'entropy_shannon':    [entropy_val],
        'vitality_tesla':     [float(vitality)],
        'mass_panic_index':   [mass_panic],
        'log_return':         [log_ret],
    })

    return alerts_df


def _compute_score_oro(godel_active: bool, entropy: float, p90: float,
                       te_gov: float, te_bus: float,
                       natural_score: float,
                       volume_type: str) -> int:
    """
    Score de Oro = Gödel(w1) + TE(w2) + Vol(w3)
    Pesos por volume_type (R16/R17).
    """
    w = SCORE_WEIGHTS.get(volume_type, SCORE_WEIGHTS['NATIVE_FUTURES'])

    # Componente Gödel (0-100)
    if godel_active:
        godel_component = min(100, 60 + (entropy - p90) / (p90 * 0.05 + 1e-9) * 20)
    else:
        godel_component = max(0, 40 - (p90 - entropy) / (p90 * 0.05 + 1e-9) * 10)
    godel_component = float(np.clip(godel_component, 0, 100))

    # Componente Transfer Entropy (0-100)
    te_total         = te_gov + te_bus
    te_component     = float(np.clip(te_total * 25, 0, 100))

    # Componente natural_score del Backbone (0-100)
    vol_component    = float(np.clip(natural_score * 100, 0, 100))

    score = (w['godel'] * godel_component +
             w['te']    * te_component    +
             w['vol']   * vol_component)

    return int(np.clip(score, 0, 100))


# ── SCORE PRINCIPAL ───────────────────────────────────────────

def score(asset: str, capital: float = 10.0,
          verbose: bool = True) -> ScoreResult:
    """
    Pipeline completo para un activo:
      parquet → alerts_df → SPELBackbone → Score de Oro → Router → ScoreResult

    Args:
      asset:   'BTC' | 'XAU' | 'NIFTY50' | 'NVDA'
      capital: capital disponible en broker ($)
      verbose: imprimir resumen

    Returns:
      ScoreResult con todos los campos
    """
    asset = asset.upper()
    if asset not in ASSETS:
        raise ValueError(f"Activo no válido: {asset}. Válidos: {ASSETS}")

    pq_path = ROOT / f'data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'

    # R3: verificar SHA antes de calcular
    reg          = json.load(open(ROOT / 'meta/SHA_REGISTRY.json'))
    sha_actual   = sha12(str(pq_path))
    sha_expected = reg[asset]['sha_v5']
    assert sha_actual == sha_expected, \
        f"SHA mismatch {asset}: {sha_actual} != {sha_expected} — ABORT (R3)"

    p90         = reg[asset]['p90_entropy']
    volume_type = reg[asset].get('volume_type', 'NATIVE_FUTURES')

    df = pl.read_parquet(str(pq_path)).sort('date')

    # Construir alerts_df desde el parquet
    alerts_df = _build_alerts_df_from_parquet(df, asset)

    # SPELBackbone
    backbone  = SPELBackbone(kelly_fraction=0.25, tp_rr_ratio=2.5)
    try:
        bb_signal = backbone.evaluate(
            activo    = asset,
            alerts_df = alerts_df,
            df_price  = df,
            capital   = capital,
        )
        natural_score  = float(bb_signal.natural_score)
        hurst          = float(bb_signal.hurst)
        te_gov         = float(bb_signal.te_gov)
        te_bus         = float(bb_signal.te_bus)
        godel_active   = bool(bb_signal.godel_signal)
        direction_raw  = bb_signal.direction.value \
                         if hasattr(bb_signal.direction, 'value') \
                         else str(bb_signal.direction)
    except Exception as e:
        # Fallback si Backbone falla: usar datos del parquet directamente
        df = df.filter(pl.col('close').is_not_null() & ~pl.col('close').is_nan() & pl.col('high').is_not_null() & pl.col('low').is_not_null())
        last          = df.tail(1).to_dicts()[0]
        entropy_val   = float(last.get('entropy_shannon', 1.0))
        vitality      = int(last.get('vitality_tesla', 3))
        godel_active  = (entropy_val >= p90) or (vitality == 9)
        natural_score = 0.55 if godel_active else 0.45
        hurst         = 0.5
        te_gov        = float(last.get('fear_momentum', 0.0))
        te_bus        = 0.0
        direction_raw = 'LONG' if float(last.get('log_return', 0)) > 0 else 'SHORT'
        bb_signal     = None

    # BTC: dirección por momentum_63d (regime-conditional)
    if asset == 'BTC':
        mom_series = df['log_return'].rolling_mean(window_size=63)
        mom        = float(mom_series[-1]) if mom_series[-1] is not None else 0.0
        direction  = 'LONG' if mom > 0 else 'SHORT'
    else:
        direction = direction_raw if direction_raw in ('LONG','SHORT') else \
                    ('LONG' if natural_score > 0.5 else 'SHORT')

    # Score de Oro
    df = df.filter(pl.col('close').is_not_null() & ~pl.col('close').is_nan() & pl.col('high').is_not_null() & pl.col('low').is_not_null())
    last_row     = df.tail(1).to_dicts()[0]
    entropy_val  = float(last_row.get('entropy_shannon', 1.0))
    score_oro    = _compute_score_oro(
        godel_active, entropy_val, p90,
        te_gov, te_bus, natural_score, volume_type
    )

    # Kelly — hard cap 0.25 (R6)
    if bb_signal and bb_signal.kelly:
        # KellyResult field: intentar fraction, kelly_fraction, o f_star
        kr = bb_signal.kelly
        kelly_val = getattr(kr, 'kelly_fractional', None) or                     getattr(kr, 'kelly_full', None) or 0.0
        kelly = float(np.clip(float(kelly_val), 0.0, 0.25))
    else:
        kelly = float(np.clip(natural_score * 0.25, 0.0, 0.25))

    # Router
    router   = SPELTradingRouter()
    try:
        decision = router.route(
            activo          = asset,
            score_resultado = {
                'score':        score_oro,
                'direccion':    direction,
                'fakeout':      False,
                'godel_activo': godel_active,
            },
            lstm_output  = {
                'godel_activo':    godel_active,
                'val_dir':         natural_score,
                'entropy_shannon': entropy_val,
                'status':          'LIVE',
            },
            natural_score = natural_score,
            capital       = capital,
        )
        modo    = str(decision.modo).replace('ModoTrading.', '')
        viable  = bool(decision.viable)
        razon   = list(decision.razon)
        kelly   = float(np.clip(decision.kelly_fraccion, 0.0, 0.25))
    except Exception as e:
        modo    = 'FLAT'
        viable  = False
        razon   = [f"Router error: {e}"]
        decision = None

    result = ScoreResult(
        asset         = asset,
        score_oro     = score_oro,
        godel_active  = godel_active,
        direction     = direction,
        natural_score = natural_score,
        kelly_fraction= kelly,
        viable        = viable,
        modo          = modo,
        razon         = razon,
        hurst         = hurst,
        te_gov        = te_gov,
        te_bus        = te_bus,
        entropy       = entropy_val,
        p90           = p90,
        sha_parquet   = sha_actual,
        timestamp_utc = datetime.now(timezone.utc).isoformat(),
        backbone      = bb_signal,
        decision      = decision,
    )

    if verbose:
        result.summary()

    return result


def score_all(capital: float = 10.0) -> dict:
    """Calcula el Score de Oro para todos los activos core."""
    results = {}
    print(f"\n{'═'*55}")
    print(f"  SPEL Score Engine — Todos los activos")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*55}")

    for asset in ASSETS:
        try:
            r = score(asset, capital=capital, verbose=False)
            results[asset] = r
        except Exception as e:
            print(f"  ❌ {asset}: {e}")

    # Tabla resumen
    print(f"\n  {'Asset':<10} {'Score':>6} {'Modo':<18} {'Dir':<8} {'Kelly':>6} {'Viable'}")
    print(f"  {'─'*8}   {'─'*5}   {'─'*16}   {'─'*6}   {'─'*5}   {'─'*6}")
    for asset, r in results.items():
        icon = '🟢' if r.viable else ('🟡' if r.score_oro >= 60 else '⛔')
        print(f"  {icon} {asset:<8} {r.score_oro:>5}   {r.modo:<18} "
              f"{r.direction:<8} {r.kelly_fraction:.4f}   "
              f"{'✅' if r.viable else '❌'}")

    # Mejor señal
    viable = {a: r for a, r in results.items() if r.viable}
    if viable:
        best = max(viable, key=lambda a: viable[a].score_oro)
        print(f"\n  ⭐ Mejor señal: {best} (score={results[best].score_oro})")
        results[best].summary()
    else:
        scores_sorted = sorted(results.items(),
                               key=lambda x: x[1].score_oro, reverse=True)
        print(f"\n  Sin señales viables. Mejor: {scores_sorted[0][0]}"
              f" (score={scores_sorted[0][1].score_oro})")

    return results

print("✅ Score Engine v1.0 cargado")
print("   score('NVDA')    → señal completa")
print("   score('BTC')     → regime-conditional")
print("   score_all()      → todos los activos")
