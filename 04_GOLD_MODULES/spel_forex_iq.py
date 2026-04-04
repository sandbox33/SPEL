"""
SPEL — Forex Confluence Dashboard para IQ Option
Score de confluencia 0-100 para operar forex en 15M y 30M.

4 capas de confirmación:
  CAPA 1 (40pts): GDELT macro — ¿hay régimen y en qué dirección?
  CAPA 2 (25pts): Estructura diaria — ¿tendencia clara?
  CAPA 3 (20pts): VWAP sesión — ¿precio en zona favorable?
  CAPA 4 (15pts): Sesión activa — ¿estamos en horario de liquidez?

Reglas de operación:
  ≥ 75: SEÑAL FUERTE   — entrar con tamaño normal
  60-74: SEÑAL MEDIA   — entrar con tamaño reducido (50%)
  < 60:  NO OPERAR     — esperar mejor confluencia

Uso en Colab:
  exec(open('spel_forex_iq.py').read())
  run_forex_dashboard()         # ver todos los pares
  get_forex_signal('EURUSD')    # señal de un par específico
"""

import sys, json, numpy as np, warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')
import yfinance as yf
import polars as pl

ROOT = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')

# ── CONFIGURACIÓN DE PARES ────────────────────────────────────
FOREX_PAIRS = {
    'EURUSD': {
        'ticker':   'EURUSD=X',
        'name':     'EUR/USD',
        'gdelt_keywords': ['euro', 'ECB', 'european central bank', 'eurozone',
                           'federal reserve', 'USD', 'inflation'],
        'sesion_optima': 'LONDON_NY',   # 13:00-17:00 UTC / 08:00-12:00 ECT
        'pip_size': 0.0001,
    },
    'GBPUSD': {
        'ticker':   'GBPUSD=X',
        'name':     'GBP/USD',
        'gdelt_keywords': ['uk', 'britain', 'pound', 'bank of england', 'BOE',
                           'sterling', 'brexit'],
        'sesion_optima': 'LONDON',      # 08:00-13:00 UTC / 03:00-08:00 ECT
        'pip_size': 0.0001,
    },
    'USDJPY': {
        'ticker':   'USDJPY=X',
        'name':     'USD/JPY',
        'gdelt_keywords': ['japan', 'yen', 'bank of japan', 'BOJ', 'nikkei',
                           'japanese economy', 'USDJPY'],
        'sesion_optima': 'ASIA_LONDON', # 00:00-09:00 UTC / 19:00-04:00 ECT
        'pip_size': 0.01,
    },
    'USDCHF': {
        'ticker':   'USDCHF=X',
        'name':     'USD/CHF',
        'gdelt_keywords': ['switzerland', 'swiss franc', 'SNB', 'safe haven',
                           'geopolitical risk', 'crisis'],
        'sesion_optima': 'LONDON_NY',
        'pip_size': 0.0001,
    },
    'AUDUSD': {
        'ticker':   'AUDUSD=X',
        'name':     'AUD/USD',
        'gdelt_keywords': ['australia', 'RBA', 'reserve bank australia',
                           'commodities', 'china trade', 'iron ore'],
        'sesion_optima': 'ASIA',        # 00:00-06:00 UTC / 19:00-01:00 ECT
        'pip_size': 0.0001,
    },
}

# Sesiones en UTC
SESSIONS = {
    'ASIA':        (0,  8),    # 00:00-08:00 UTC
    'LONDON':      (8,  13),   # 08:00-13:00 UTC
    'LONDON_NY':   (13, 17),   # 13:00-17:00 UTC ← MAYOR LIQUIDEZ
    'NY':          (17, 21),   # 17:00-21:00 UTC
    'ASIA_LONDON': (0,  9),    # overlap Asia-Londres
}

# ══════════════════════════════════════════════════════════════
# CAPA 1 — GDELT MACRO (40 pts)
# ══════════════════════════════════════════════════════════════

def get_gdelt_macro(pair: str) -> dict:
    """
    Lee el contexto GDELT del parquet más relevante para el par.
    EURUSD/GBPUSD → contexto macro global (SPX proxy)
    USDJPY → usa XAU como proxy de safe-haven
    USDCHF → usa XAU (safe haven correlation)
    AUDUSD → usa macro global
    """
    # Mapeo par → activo SPEL con mayor correlación GDELT
    gdelt_proxy = {
        'EURUSD': 'NVDA',    # macro USA/global
        'GBPUSD': 'XAU',     # safe haven / geopolítica
        'USDJPY': 'XAU',     # safe haven
        'USDCHF': 'XAU',     # safe haven
        'AUDUSD': 'NVDA',    # macro global / risk-on
    }

    proxy   = gdelt_proxy.get(pair, 'XAU')
    pq_path = ROOT / f'data_lake/{proxy}/ohlcv/aggregated/{proxy}_ohlcv_v5.parquet'

    try:
        df  = pl.read_parquet(str(pq_path)).sort('date').tail(30)
        reg = json.load(open(ROOT/'meta/SHA_REGISTRY.json'))
        p90 = reg[proxy]['p90_entropy']

        last        = df.row(-1, named=True)
        entropy     = float(last['entropy_shannon'])
        vitality    = int(last['vitality_tesla'])
        nash_frozen = float(last['nash_frozen_7d'])
        fear_mom    = float(last['fear_momentum'])
        goldstein   = float(last.get('goldstein_mean', 1.845))
        tone_var    = float(last.get('tone_variance', 117.0))

        godel_active = (entropy >= p90) or (vitality == 9)

        # Dirección macro: goldstein > 1.845 = eventos positivos = risk-on
        # Para EURUSD/GBPUSD/AUDUSD: risk-on = sube el par (USD baja)
        # Para USDJPY/USDCHF: risk-on = par sube (USD sube vs safe havens)
        risk_on_pairs  = ['EURUSD', 'GBPUSD', 'AUDUSD']
        risk_off_pairs = ['USDJPY', 'USDCHF']

        if godel_active:
            # Gödel activo: alta incertidumbre
            # Dirección según fear_momentum (positivo = más miedo = risk-off)
            if fear_mom > 0.05:
                macro_bias = 'SHORT' if pair in risk_on_pairs else 'LONG'
                macro_strength = min(abs(fear_mom) * 50, 40)
            elif fear_mom < -0.05:
                macro_bias = 'LONG' if pair in risk_on_pairs else 'SHORT'
                macro_strength = min(abs(fear_mom) * 50, 40)
            else:
                macro_bias     = 'NEUTRAL'
                macro_strength = 15
        else:
            # Gödel inactivo: régimen normal
            if goldstein > 1.90:  # eventos muy positivos
                macro_bias = 'LONG' if pair in risk_on_pairs else 'SHORT'
                macro_strength = 20
            elif goldstein < 1.70:  # eventos negativos
                macro_bias = 'SHORT' if pair in risk_on_pairs else 'LONG'
                macro_strength = 20
            else:
                macro_bias     = 'NEUTRAL'
                macro_strength = 10

        # Nash frozen alto = compresión → próximo movimiento fuerte
        if nash_frozen > 0.75:
            compression = True
            macro_strength = min(macro_strength + 10, 40)
        else:
            compression = False

        return {
            'score':        int(macro_strength),
            'bias':         macro_bias,
            'godel_active': godel_active,
            'entropy':      round(entropy, 4),
            'p90':          round(p90, 4),
            'vitality':     vitality,
            'compression':  compression,
            'nash_frozen':  round(nash_frozen, 3),
            'proxy':        proxy,
        }

    except Exception as e:
        return {
            'score': 10, 'bias': 'NEUTRAL', 'godel_active': False,
            'entropy': 0, 'p90': 0, 'vitality': 3,
            'compression': False, 'nash_frozen': 0, 'proxy': 'N/A',
            'error': str(e)
        }


# ══════════════════════════════════════════════════════════════
# CAPA 2 — ESTRUCTURA DIARIA (25 pts)
# ══════════════════════════════════════════════════════════════

def get_structure_daily(pair: str) -> dict:
    """
    Analiza la estructura diaria del par forex.
    Tendencia: EMA20 vs EMA50 + Higher Highs/Lows o Lower Highs/Lows
    """
    cfg    = FOREX_PAIRS[pair]
    ticker = cfg['ticker']

    try:
        raw = yf.download(ticker, period='60d', interval='1d',
                          auto_adjust=True, progress=False)

        if raw.empty or len(raw) < 20:
            return {'score': 5, 'bias': 'NEUTRAL', 'trend': 'UNCLEAR',
                    'ema20': 0, 'ema50': 0, 'last_close': 0}

        if hasattr(raw.columns, 'levels'):
            raw.columns = raw.columns.get_level_values(0)

        closes = raw['Close'].values.astype(float)
        highs  = raw['High'].values.astype(float)
        lows   = raw['Low'].values.astype(float)

        # EMAs
        def ema(data, n):
            k = 2 / (n + 1)
            result = [data[0]]
            for x in data[1:]:
                result.append(x * k + result[-1] * (1 - k))
            return np.array(result)

        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)

        last_close = closes[-1]
        ema20_last = ema20[-1]
        ema50_last = ema50[-1]

        # Estructura: últimos 10 días
        recent_highs = highs[-10:]
        recent_lows  = lows[-10:]

        # Higher Highs: máximo reciente > máximo anterior
        hh = recent_highs[-1] > recent_highs[-5]
        hl = recent_lows[-1]  > recent_lows[-5]
        lh = recent_highs[-1] < recent_highs[-5]
        ll = recent_lows[-1]  < recent_lows[-5]

        # Determinar tendencia
        if ema20_last > ema50_last and last_close > ema20_last:
            if hh and hl:
                trend  = 'STRONG_BULL'
                bias   = 'LONG'
                score  = 25
            else:
                trend  = 'BULL'
                bias   = 'LONG'
                score  = 18
        elif ema20_last < ema50_last and last_close < ema20_last:
            if lh and ll:
                trend  = 'STRONG_BEAR'
                bias   = 'SHORT'
                score  = 25
            else:
                trend  = 'BEAR'
                bias   = 'SHORT'
                score  = 18
        else:
            trend  = 'RANGING'
            bias   = 'NEUTRAL'
            score  = 8

        # ATR para tamaño de stops
        atr = np.mean(np.abs(highs[-14:] - lows[-14:]))

        return {
            'score':       score,
            'bias':        bias,
            'trend':       trend,
            'ema20':       round(ema20_last, 5),
            'ema50':       round(ema50_last, 5),
            'last_close':  round(last_close, 5),
            'atr_daily':   round(atr, 5),
        }

    except Exception as e:
        return {'score': 5, 'bias': 'NEUTRAL', 'trend': 'ERROR',
                'ema20': 0, 'ema50': 0, 'last_close': 0,
                'atr_daily': 0, 'error': str(e)}


# ══════════════════════════════════════════════════════════════
# CAPA 3 — VWAP SESIÓN 15/30M (20 pts)
# ══════════════════════════════════════════════════════════════

def get_vwap_signal(pair: str, tf_minutes: int = 15) -> dict:
    """
    Descarga velas 15M o 30M de hoy y calcula VWAP de sesión.
    Evalúa si el precio está en zona favorable de entrada.
    """
    cfg      = FOREX_PAIRS[pair]
    interval = '15m' if tf_minutes == 15 else '30m'

    try:
        raw = yf.download(cfg['ticker'], period='1d',
                          interval=interval, auto_adjust=True, progress=False)

        if raw.empty or len(raw) < 5:
            return {'score': 5, 'bias': 'NEUTRAL', 'vwap': 0,
                    'last': 0, 'position': 'UNKNOWN', 'atr_15m': 0}

        if hasattr(raw.columns, 'levels'):
            raw.columns = raw.columns.get_level_values(0)

        closes  = raw['Close'].values.astype(float)
        highs   = raw['High'].values.astype(float)
        lows    = raw['Low'].values.astype(float)
        volumes = raw['Volume'].values.astype(float)

        # VWAP = sum(typical_price * volume) / sum(volume)
        typical = (highs + lows + closes) / 3
        vol     = np.where(volumes == 0, 1, volumes)  # evitar div/0
        vwap    = np.cumsum(typical * vol) / np.cumsum(vol)
        vwap_now = vwap[-1]
        last     = closes[-1]

        # Bandas VWAP (1 desviación estándar)
        variance = np.cumsum(((typical - vwap) ** 2) * vol) / np.cumsum(vol)
        std      = np.sqrt(np.maximum(variance, 0))
        upper1   = vwap[-1] + std[-1]
        lower1   = vwap[-1] - std[-1]

        # ATR 15M
        atr_15m = np.mean(np.abs(highs[-10:] - lows[-10:]))

        # Posición relativa al VWAP
        pct_from_vwap = (last - vwap_now) / (vwap_now + 1e-10)

        if last > upper1:
            position = 'ABOVE_1SD'   # sobreextendido arriba — no comprar
            bias     = 'SHORT'
            score    = 15
        elif last < lower1:
            position = 'BELOW_1SD'   # sobreextendido abajo — no vender
            bias     = 'LONG'
            score    = 15
        elif last > vwap_now and abs(pct_from_vwap) < 0.001:
            position = 'NEAR_VWAP_ABOVE'   # cerca del VWAP por arriba
            bias     = 'LONG'
            score    = 20
        elif last < vwap_now and abs(pct_from_vwap) < 0.001:
            position = 'NEAR_VWAP_BELOW'   # cerca del VWAP por abajo
            bias     = 'SHORT'
            score    = 20
        elif last > vwap_now:
            position = 'ABOVE_VWAP'
            bias     = 'LONG'
            score    = 12
        else:
            position = 'BELOW_VWAP'
            bias     = 'SHORT'
            score    = 12

        return {
            'score':    score,
            'bias':     bias,
            'vwap':     round(vwap_now, 5),
            'last':     round(last, 5),
            'upper1':   round(upper1, 5),
            'lower1':   round(lower1, 5),
            'position': position,
            'atr_15m':  round(atr_15m, 5),
            'tf':       f'{tf_minutes}M',
        }

    except Exception as e:
        return {'score': 5, 'bias': 'NEUTRAL', 'vwap': 0,
                'last': 0, 'position': 'ERROR', 'atr_15m': 0,
                'error': str(e)}


# ══════════════════════════════════════════════════════════════
# CAPA 4 — SESIÓN ACTIVA (15 pts)
# ══════════════════════════════════════════════════════════════

def get_session_score(pair: str) -> dict:
    """
    Verifica si estamos en la sesión óptima para ese par.
    Horario Ecuador UTC-5.
    """
    now_utc    = datetime.now(timezone.utc)
    hour_utc   = now_utc.hour
    hour_ect   = (hour_utc - 5) % 24  # Ecuador UTC-5

    optima     = FOREX_PAIRS[pair]['sesion_optima']
    start, end = SESSIONS[optima]

    # Verificar si estamos en sesión óptima o en overlap
    in_optimal = start <= hour_utc < end

    # Overlap Londres-NY (13-17 UTC) es el mejor momento para casi todos
    in_overlap = 13 <= hour_utc < 17

    if in_overlap:
        score   = 15
        session = 'OVERLAP_LONDON_NY ⭐'
    elif in_optimal:
        score   = 10
        session = f'SESIÓN ÓPTIMA ({optima})'
    elif 8 <= hour_utc < 21:
        score   = 5
        session = 'SESIÓN ACTIVA (no óptima)'
    else:
        score   = 0
        session = 'SESIÓN CERRADA — no operar'

    return {
        'score':     score,
        'session':   session,
        'hour_utc':  f'{hour_utc:02d}:00 UTC',
        'hour_ect':  f'{hour_ect:02d}:00 ECT',
        'in_optimal': in_optimal or in_overlap,
    }


# ══════════════════════════════════════════════════════════════
# SCORE FINAL Y SEÑAL
# ══════════════════════════════════════════════════════════════

def get_forex_signal(pair: str, tf_minutes: int = 15,
                     verbose: bool = True) -> dict:
    """
    Calcula el score de confluencia completo para un par forex.
    Retorna señal accionable con entrada, stop y target sugeridos.
    """
    pair = pair.upper().replace('/', '')

    if pair not in FOREX_PAIRS:
        print(f"❌ Par no configurado: {pair}")
        print(f"   Disponibles: {list(FOREX_PAIRS.keys())}")
        return {}

    cfg = FOREX_PAIRS[pair]

    # Las 4 capas
    macro    = get_gdelt_macro(pair)
    struct   = get_structure_daily(pair)
    vwap     = get_vwap_signal(pair, tf_minutes)
    session  = get_session_score(pair)

    # Score total
    total_score = macro['score'] + struct['score'] + vwap['score'] + session['score']

    # Determinar dirección por votación ponderada
    biases = {
        'LONG':  0,
        'SHORT': 0,
    }
    weights = {
        'macro':  macro['score'],
        'struct': struct['score'],
        'vwap':   vwap['score'],
    }
    for layer, w in weights.items():
        b = {'macro': macro, 'struct': struct, 'vwap': vwap}[layer]['bias']
        if b in biases:
            biases[b] += w

    if biases['LONG'] > biases['SHORT']:
        direction = 'LONG  ↑'
        dir_key   = 'LONG'
    elif biases['SHORT'] > biases['LONG']:
        direction = 'SHORT ↓'
        dir_key   = 'SHORT'
    else:
        direction = 'NEUTRAL —'
        dir_key   = 'NEUTRAL'

    # Conflicto de capas — reducir confianza
    all_biases = [macro['bias'], struct['bias'], vwap['bias']]
    n_aligned  = max(all_biases.count('LONG'), all_biases.count('SHORT'))
    alignment  = n_aligned / len(all_biases)

    if alignment < 0.67:  # menos de 2/3 capas alineadas
        total_score = int(total_score * 0.7)
        conflicto   = True
    else:
        conflicto   = False

    # Clasificación de señal
    if total_score >= 75 and dir_key != 'NEUTRAL' and session['in_optimal']:
        signal_type = '🟢 SEÑAL FUERTE — ENTRAR'
        operar      = True
    elif total_score >= 60 and dir_key != 'NEUTRAL':
        signal_type = '🟡 SEÑAL MEDIA — tamaño 50%'
        operar      = True
    elif total_score >= 45 and dir_key != 'NEUTRAL' and not conflicto:
        signal_type = '🟠 SEÑAL DÉBIL — esperar confirmación'
        operar      = False
    else:
        signal_type = '⛔ NO OPERAR'
        operar      = False

    # Stop y target basados en ATR
    last_price = vwap.get('last', 0) or struct.get('last_close', 0)
    atr        = vwap.get('atr_15m', 0) or struct.get('atr_daily', 0) * 0.3
    pip        = cfg['pip_size']

    if last_price > 0 and atr > 0:
        stop_pips   = round(atr / pip * 1.5, 1)
        target_pips = round(stop_pips * 2.0, 1)   # R:R 2:1 mínimo
        if dir_key == 'LONG':
            stop_price   = round(last_price - atr * 1.5, 5)
            target_price = round(last_price + atr * 2.0, 5)
        else:
            stop_price   = round(last_price + atr * 1.5, 5)
            target_price = round(last_price - atr * 2.0, 5)
    else:
        stop_pips = target_pips = stop_price = target_price = 0

    result = {
        'pair':          cfg['name'],
        'tf':            f'{tf_minutes}M',
        'score':         total_score,
        'direction':     direction,
        'dir_key':       dir_key,
        'signal':        signal_type,
        'operar':        operar,
        'conflicto':     conflicto,
        'layers': {
            'macro':   macro,
            'struct':  struct,
            'vwap':    vwap,
            'session': session,
        },
        'entry':         round(last_price, 5),
        'stop':          stop_price,
        'target':        target_price,
        'stop_pips':     stop_pips,
        'target_pips':   target_pips,
        'rr_ratio':      2.0,
        'timestamp_ect': datetime.now(timezone.utc - timedelta(hours=5))
                         .strftime('%Y-%m-%d %H:%M ECT')
                         if False else
                         (datetime.now(timezone.utc).replace(tzinfo=None) -
                          timedelta(hours=5)).strftime('%Y-%m-%d %H:%M ECT'),
    }

    if verbose:
        _print_signal(result)

    return result


def _print_signal(r: dict):
    """Imprime la señal de forma clara y accionable."""
    sep = '═' * 55
    print(f"\n{sep}")
    print(f"  {r['pair']}  |  {r['tf']}  |  {r['timestamp_ect']}")
    print(sep)
    print(f"  SCORE: {r['score']}/100   {r['signal']}")
    print(f"  DIRECCIÓN: {r['direction']}")
    if r['conflicto']:
        print(f"  ⚠️  Capas en conflicto — confianza reducida")
    print()
    print(f"  ── Capas ──────────────────────────────────────")

    l = r['layers']
    print(f"  GDELT Macro  [{l['macro']['score']:2d}/40]  "
          f"bias={l['macro']['bias']:<8} "
          f"godel={'✅' if l['macro']['godel_active'] else '○'} "
          f"entropy={l['macro']['entropy']:.4f}(P90={l['macro']['p90']:.4f})")
    print(f"  Estructura   [{l['struct']['score']:2d}/25]  "
          f"bias={l['struct']['bias']:<8} "
          f"trend={l['struct']['trend']}")
    print(f"  VWAP {l['vwap']['tf']}    [{l['vwap']['score']:2d}/20]  "
          f"bias={l['vwap']['bias']:<8} "
          f"pos={l['vwap']['position']}")
    print(f"  Sesión       [{l['session']['score']:2d}/15]  "
          f"{l['session']['session']}")

    if r['operar'] and r['entry'] > 0:
        print(f"\n  ── Gestión ────────────────────────────────────")
        print(f"  Entrada:  {r['entry']}")
        print(f"  Stop:     {r['stop']}  ({r['stop_pips']:.1f} pips)")
        print(f"  Target:   {r['target']}  ({r['target_pips']:.1f} pips)")
        print(f"  R:R:      1:{r['rr_ratio']}")
    print(sep)


# ══════════════════════════════════════════════════════════════
# DASHBOARD COMPLETO
# ══════════════════════════════════════════════════════════════

def run_forex_dashboard(tf_minutes: int = 15):
    """
    Muestra señales para todos los pares configurados.
    Prioriza por score descendente.
    """
    print(f"\n{'═'*55}")
    print(f"  SPEL FOREX DASHBOARD — IQ Option")
    print(f"  Timeframe: {tf_minutes}M")
    print(f"  {(datetime.now(timezone.utc)-timedelta(hours=5)).strftime('%Y-%m-%d %H:%M ECT')}")
    print(f"{'═'*55}")

    signals = {}
    for pair in FOREX_PAIRS:
        signals[pair] = get_forex_signal(pair, tf_minutes, verbose=False)

    # Ordenar por score
    sorted_pairs = sorted(signals.items(),
                          key=lambda x: x[1].get('score', 0), reverse=True)

    # Resumen rápido
    print(f"\n  {'Par':<10} {'Score':>6} {'Dir':>8} {'Señal'}")
    print(f"  {'─'*8}   {'─'*5}   {'─'*6}   {'─'*25}")
    for pair, sig in sorted_pairs:
        icon = '🟢' if sig['score'] >= 75 else ('🟡' if sig['score'] >= 60 else '⛔')
        print(f"  {sig['pair']:<10} {sig['score']:>5}   "
              f"{sig['dir_key']:>7}   {icon} {sig['signal'][:30]}")

    # Mostrar detalles de los que vale la pena operar
    print(f"\n{'═'*55}")
    print(f"  DETALLES — SEÑALES OPERABLES")
    print(f"{'═'*55}")

    showed = False
    for pair, sig in sorted_pairs:
        if sig['score'] >= 60:
            _print_signal(sig)
            showed = True

    if not showed:
        print(f"\n  ⛔ Sin señales operables ahora.")
        print(f"  Mejor par: {sorted_pairs[0][1]['pair']} "
              f"(score={sorted_pairs[0][1]['score']})")
        print(f"  Esperar mejor confluencia o próxima sesión.")

    return {p: s for p, s in signals.items()}


# ══════════════════════════════════════════════════════════════
# SESIONES ÓPTIMAS PARA ECUADOR
# ══════════════════════════════════════════════════════════════

def show_schedule():
    """Muestra el horario óptimo de cada par en hora Ecuador."""
    print(f"\n{'═'*55}")
    print(f"  HORARIOS ÓPTIMOS — HORA ECUADOR (UTC-5)")
    print(f"{'═'*55}")
    schedule = {
        'EUR/USD': '08:00-12:00 ECT ⭐ (London-NY overlap)',
        'GBP/USD': '03:00-08:00 ECT (apertura Londres)',
        'USD/JPY': '19:00-04:00 ECT (sesión Asia)',
        'USD/CHF': '08:00-12:00 ECT ⭐ (London-NY overlap)',
        'AUD/USD': '19:00-01:00 ECT (sesión Asia-Pacífico)',
    }
    for par, horario in schedule.items():
        print(f"  {par:<10}  {horario}")
    print(f"\n  ⭐ MEJOR MOMENTO: 08:00-12:00 ECT")
    print(f"     EUR/USD y USD/CHF en overlap Londres-NY")
    print(f"     Mayor liquidez = spreads más bajos = señales más fiables")
    print(f"{'═'*55}\n")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

print("✅ SPEL Forex IQ Dashboard cargado")
print("   Comandos:")
print("   run_forex_dashboard(15)     → todos los pares en 15M")
print("   run_forex_dashboard(30)     → todos los pares en 30M")
print("   get_forex_signal('EURUSD')  → señal específica")
print("   show_schedule()             → horarios Ecuador")
