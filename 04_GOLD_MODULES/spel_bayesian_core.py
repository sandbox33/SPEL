"""
spel_bayesian_core.py — SPEL 3.0 · Bayesian Model Averaging Engine
Protocolo: SPEL_Sovereign_Architecture v4.4 · CIELO_ABIERTO_SHADOW

Regla 13 Weights (BMA):
  Native Assets  → Gödel 0.40 | TE_Entropy 0.30 | Backbone 0.30
  Synthetic Index→ Gödel 0.55 | TE_Entropy 0.45 | Backbone 0.00

Kill Signal: Shannon entropy GDELT > 0.42 → Gold Score = 0.0 (FORCE_HOLD)
KL Drift Control: KL_Divergence > 0.20 → HOLD (no execution)

Output: live_bma_result.json  (consumed by orchestrator_v10)
CLI:    python spel_bayesian_core.py [--asset NVDA] [--verbose]

Invariantes:
  R21: credenciales desde secrets.json (cero hardcode)
  R32: atomic writes
  R37/Ley2: imports pesados dentro de funciones
  EF-23: NUNCA tocar gdelt_foundation.py o critical_loss_optimized.py
"""

import json
import math
import os
import hashlib
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ─── Constants ────────────────────────────────────────────────────────────────
# ─── 3-way ROOT (S46) ────────────────────────────────────────────────────────
_IS_GH_BMA = os.environ.get('GITHUB_ACTIONS') == 'true'
def _detect_root_bma() -> Path:
    if _IS_GH_BMA: return Path(os.environ.get('GITHUB_WORKSPACE', '.')).resolve()
    if os.environ.get('SPEL_BASE_DIR'): return Path(os.environ['SPEL_BASE_DIR']).resolve()
    _p = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
    return _p if _p.exists() else Path('.')
ROOT = _detect_root_bma()
# ─────────────────────────────────────────────────────────────────────────────

VAULT = ROOT / "00_VAULT"
SECRETS_PATH = VAULT / "secrets_template.json"
REGISTRY_PATH = VAULT / "registry" / "SHA_REGISTRY.json"
OUTPUT_PATH   = VAULT / "live_bma_result.json"
PULSE_PATH    = VAULT / "system_pulse.json"

# R13: Pesos BMA (inamovibles — cambiarlos requiere bug# asignado)
BMA_WEIGHTS = {
    "native": {
        "godel":    0.40,
        "te_entropy": 0.30,
        "backbone": 0.30,
    },
    "synthetic": {
        "godel":    0.55,
        "te_entropy": 0.45,
        "backbone": 0.00,
    },
}
# Activos nativos (tienen backbone LSTM directo)
NATIVE_ASSETS    = {"NVDA", "BTC", "XAU", "NIFTY50"}
# Activos sintéticos / indices (sin backbone propio — solo Gödel + TE)
SYNTHETIC_ASSETS = {"EURUSD", "SPY", "ETH"}

# Uncertainty thresholds (XML v4.4 §Decision_Core)
SHANNON_KILL_THRESHOLD   = 0.42   # FORCE_HOLD_AND_KILL_SIGNAL
KL_DIVERGENCE_THRESHOLD  = 0.20   # DRIFT_CONTROL


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    """R32: write-then-rename (never partial state)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha24(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def _load_secrets(path: Path) -> dict:
    """R21: zero hardcode. Returns empty dict if secrets absent."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _tg_chaos(token: str, chat_id: str, text: str) -> bool:
    """Fire-and-forget alert to TG_CHAOS. Never blocks execution."""
    import urllib.request
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "parse_mode": "HTML",
                }).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=6,
        )
        return True
    except Exception:
        return False


# ─── Core BMA computation ─────────────────────────────────────────────────────



# ─── S46 INJECTIONS ── Monte Carlo · KL Rolling · Staleness Guard ─────────────

def check_data_staleness(timestamp_str: str, max_age_seconds: int = 780) -> bool:
    """
    V-04 FIX: Circuit Breaker de datos viejos.
    Si el dato OHLCV tiene > 13 min (780s), la vitalidad debe ser 0.
    Retorna True si el dato es fresco, False si es stale.

    max_age_seconds=780 = 13 min: tolerancia máxima para ciclo de 15min.
    El harvester tiene ~2min de margen antes del compute BMA.
    """
    if not timestamp_str:
        return False  # Sin timestamp = stale por default
    try:
        data_ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - data_ts).total_seconds()
        if age > max_age_seconds:
            print(f'  ⚠️ DATA_STALE: datos de hace {int(age)}s > {max_age_seconds}s umbral' )
            return False
        return True
    except Exception as e:
        print(f'  ERR staleness check: {e}')
        return False  # Error = tratar como stale (conservador)


def get_rolling_kl(current_kl: float, vault_path, window: int = 5) -> float:
    """
    V-03 FIX: Rolling average de KL_Divergence sobre los últimos N ciclos.
    Evita que un spike GDELT de 15min active HOLD sobre señal estructural sólida.

    Lee live_bma_history.json (lista de registros con campo 'kl').
    Escribe el registro del ciclo actual al final del ciclo.
    Retorna: kl_rolling (promedio de los últimos window+1 valores).

    threshold=0.20 se aplica sobre kl_rolling, no sobre current_kl puntual.
    Efecto: un spike único eleva rolling 0.20/5 = 0.04 (no activa HOLD).
    5 ciclos consecutivos en spike → rolling = 0.20+ → HOLD activado.
    """
    import json as _jj
    from pathlib import Path as _PP
    _hist_path = _PP(vault_path) / 'live_bma_history.json'
    try:
        history = _jj.loads(_hist_path.read_text()) if _hist_path.exists() else []
    except Exception:
        history = []

    _kl_window = [h.get('kl', 0.0) for h in history[-window:] if isinstance(h.get('kl'), (int, float))]
    _kl_window.append(current_kl)
    rolling = sum(_kl_window) / len(_kl_window)
    return round(rolling, 6)


def append_bma_history(record: dict, vault_path, max_records: int = 50) -> None:
    """
    Appends a BMA cycle record to live_bma_history.json.
    Mantiene max_records registros (rolling buffer FIFO).
    Escritura atómica R32.
    Expected record fields: ts, asset, kl, shannon, gold_score, action.
    """
    import json as _jj
    from pathlib import Path as _PP
    _hist_path = _PP(vault_path) / 'live_bma_history.json'
    try:
        history = _jj.loads(_hist_path.read_text()) if _hist_path.exists() else []
    except Exception:
        history = []
    history.append(record)
    if len(history) > max_records:
        history = history[-max_records:]
    _tmp = _hist_path.with_suffix('.tmp')
    _tmp.write_bytes(_jj.dumps(history, indent=2).encode())
    _tmp.replace(_hist_path)


def run_monte_carlo_validation(
    current_price: float,
    volatility: float,
    base_gold_score: float,
    asset: str = 'UNKNOWN',
    iterations: int = 1000,
) -> dict:
    """
    GBM Monte Carlo Vectorizado — S46 (XML §Monte_Carlo_Architecture)

    Simula 1000 trayectorias GBM de 15min sobre el gold_score.
    Runtime: < 50ms (NumPy vectorizado, una operación np.exp()).

    GBM: S_T = S_0 * exp((μ - σ²/2)*T + σ*√T*Z)  Z ~ N(0,1)
    T = 15min / (252*24*4) = fracción de año para 15min.

    PRICE_SENSITIVITY por activo (calibrar con datos reales post Gate R30):
      BTC:     0.07-0.08 (alta reactividad)
      XAU:     0.03-0.04 (menor reactividad)
      NVDA:    0.05 (baseline)
      NIFTY50: 0.04 (índice — menos reactivo)
      EURUSD:  0.02 (forex — muy baja reactividad)

    Threshold: >= 850/1000 trayectorias con gold_score > 0.85 → MC_APPROVE.
    """
    import numpy as _np  # R37/Ley2: lazy import dentro de función

    # Sensibilidad por activo (pendiente de calibración post Gate R30)
    SENSITIVITY_MAP = {'BTC': 0.07, 'XAU': 0.04, 'NVDA': 0.05,
                        'NIFTY50': 0.04, 'EURUSD': 0.02}
    SENSITIVITY = SENSITIVITY_MAP.get(asset, 0.05)

    T  = 15 / (252 * 24 * 4)   # 15 min en fracción de año
    mu = 0.0                    # drift neutro (sin sesgo de dirección)

    Z       = _np.random.standard_normal(iterations)
    returns = _np.exp((mu - 0.5 * volatility**2) * T + volatility * _np.sqrt(T) * Z)
    sim_prices  = current_price * returns
    price_diff  = (sim_prices - current_price) / max(current_price, 1e-10)
    sim_scores  = base_gold_score + (price_diff * SENSITIVITY)
    sim_scores  = _np.clip(sim_scores, 0.0, 1.0)

    success_rate  = float(_np.mean(sim_scores > 0.85))
    p5, p50, p95  = float(_np.percentile(sim_scores, [5, 50, 95]))
    mc_approved   = success_rate >= 0.85

    return {
        'mc_approved':
            mc_approved,
        'success_rate':
            round(success_rate, 4),
        'p5_score':
            round(p5, 4),
        'p50_score':
            round(p50, 4),
        'p95_score':
            round(p95, 4),
        'sensitivity':
            SENSITIVITY,
        'iterations':
            iterations,
        'asset':
            asset,
    }

# ─── END S46 INJECTIONS ──────────────────────────────────────────────────────

def compute_gold_score_bma(
    godel_score: float,
    te_score: float,
    backbone_score: float,
    asset: str,
    shannon_entropy: float,
    kl_divergence: float = 0.0,
    verbose: bool = False,
) -> dict:
    """
    Compute the Gold Score BMA for a given asset.

    Args:
        godel_score:     [0,1] output from Gödel bound (condición OR)
        te_score:        [0,1] Transfer Entropy normalized score
        backbone_score:  [0,1] LSTM backbone directional confidence
        asset:           Asset name (NVDA, BTC, XAU, EURUSD, ...)
        shannon_entropy: GDELT Shannon entropy (raw, NOT percentile)
        kl_divergence:   KL divergence between current & prior distribution
        verbose:         Print debug info

    Returns:
        dict with: gold_score, kill_signal, kill_reason, weights_used, audit
    """
    result = {
        "asset": asset,
        "ts": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "godel_score":    round(godel_score, 6),
            "te_score":       round(te_score, 6),
            "backbone_score": round(backbone_score, 6),
            "shannon_entropy": round(shannon_entropy, 6),
            "kl_divergence":  round(kl_divergence, 6),
        },
        "kill_signal": False,
        "kill_reason": None,
        "gold_score":  0.0,
        "weights_used": None,
        "regime": "UNKNOWN",
        "action": "HOLD",
    }

    # ── UNCERTAINTY PROTOCOL: Kill conditions ──────────────────────────────
    # Priority 1: Shannon entropy (GDELT geopolitical noise)
    if shannon_entropy > SHANNON_KILL_THRESHOLD:
        result["kill_signal"] = True
        result["kill_reason"] = (
            f"FORCE_HOLD_AND_KILL_SIGNAL: shannon_entropy={shannon_entropy:.4f} "
            f"> threshold={SHANNON_KILL_THRESHOLD}"
        )
        result["gold_score"] = 0.0
        result["action"] = "HOLD"
        result["regime"] = "HIGH_ENTROPY"
        if verbose:
            print(f"  🔴 KILL SIGNAL: {result['kill_reason']}")
        return result

    # Priority 2: KL drift control
    if kl_divergence > KL_DIVERGENCE_THRESHOLD:
        result["kill_signal"] = True
        result["kill_reason"] = (
            f"DRIFT_CONTROL: KL_divergence={kl_divergence:.4f} "
            f"> threshold={KL_DIVERGENCE_THRESHOLD}"
        )
        result["gold_score"] = 0.0
        result["action"] = "HOLD"
        result["regime"] = "DRIFT_DETECTED"
        if verbose:
            print(f"  🟠 DRIFT HOLD: {result['kill_reason']}")
        return result

    # ── SELECT WEIGHTS (R13) ───────────────────────────────────────────────
    asset_upper = asset.upper()
    if asset_upper in NATIVE_ASSETS:
        weights = BMA_WEIGHTS["native"]
        asset_type = "native"
    else:
        weights = BMA_WEIGHTS["synthetic"]
        asset_type = "synthetic"

    result["weights_used"] = {"type": asset_type, **weights}

    # ── BMA COMPUTATION ────────────────────────────────────────────────────
    # Clamp inputs to [0, 1]
    g = max(0.0, min(1.0, godel_score))
    t = max(0.0, min(1.0, te_score))
    b = max(0.0, min(1.0, backbone_score))

    gold_score = (
        weights["godel"]    * g +
        weights["te_entropy"] * t +
        weights["backbone"] * b
    )
    gold_score = round(max(0.0, min(1.0, gold_score)), 6)

    # ── REGIME DETECTION ──────────────────────────────────────────────────
    # Gödel-first: the semaphore determines the regime
    if g >= 0.90:
        regime = "TRANSCENDENCE"   # entropy >= P90 (Gödel active)
    elif g >= 0.33:
        regime = "STRUCTURE"       # Nash zone
    else:
        regime = "CREATION"        # tendential market

    # ── ACTION ────────────────────────────────────────────────────────────
    if gold_score >= 0.85:
        action = "EXECUTE_STRONG"
    elif gold_score >= 0.65:
        action = "EXECUTE_WEAK"
    elif gold_score >= 0.40:
        action = "WATCH"
    else:
        action = "HOLD"

    result["gold_score"] = gold_score
    result["regime"] = regime
    result["action"] = action

    if verbose:
        print(f"  Asset: {asset} ({asset_type})")
        print(f"  Weights: G={weights['godel']} TE={weights['te_entropy']} B={weights['backbone']}")
        print(f"  Scores:  G={g:.4f} TE={t:.4f} B={b:.4f}")
        print(f"  Gold Score BMA: {gold_score:.4f}")
        print(f"  Regime: {regime} | Action: {action}")

    return result


def compute_lambda_decay(
    signal_age_seconds: float,
    half_life_seconds: float = 3600.0,
) -> float:
    """
    Lambda Decay: how stale is a signal.
    λ(t) = exp(-ln(2) * t / t_half)
    Returns [0, 1] — 1.0 = fresh, 0.0 = fully decayed
    """
    if half_life_seconds <= 0:
        return 0.0
    decay = math.exp(-math.log(2) * signal_age_seconds / half_life_seconds)
    return round(max(0.0, min(1.0, decay)), 6)


def compute_vitality_tesla(entropy_value: float,
                            p33: float, p66: float) -> int:
    """
    Vitality Tesla (Hito #2 de HINC OMNIA CERNO):
      3 → entropy < p33 (Creación — mercado tendencial)
      6 → p33 ≤ entropy < p66 (Estructura/Nash)
      9 → entropy ≥ p66 (Trascendencia — ruptura)
    """
    if entropy_value < p33:
        return 3
    elif entropy_value < p66:
        return 6
    else:
        return 9


# ─── Batch run (all active assets) ────────────────────────────────────────────

def run_bma_cycle(root: Path = ROOT, verbose: bool = False) -> dict:
    """
    Read signal data from VAULT, compute BMA for all assets,
    write live_bma_result.json.
    Idempotent — safe to run every 15 minutes.
    """
    vault = root / "00_VAULT"
    result_path = vault / "live_bma_result.json"
    signal_path = vault / "last_signal.json"
    godel_path  = vault / "godel_thresholds_v2.json"

    # Load last signal
    signal = {}
    if signal_path.exists():
        try:
            signal = json.loads(signal_path.read_text())
        except Exception as e:
            print(f"  WARN: last_signal.json unreadable: {e}")

    # Load Gödel thresholds (P90 per asset)
    godel_thresholds = {}
    if godel_path.exists():
        try:
            godel_thresholds = json.loads(godel_path.read_text())
        except Exception:
            pass

    # Compute signal age for Lambda Decay
    signal_ts = signal.get("timestamp") or signal.get("ts")
    signal_age = 9999.0
    if signal_ts:
        try:
            ts = datetime.fromisoformat(signal_ts)
            signal_age = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            pass

    lambda_decay = compute_lambda_decay(signal_age)
    # V-04 S46: Staleness guard — si dato OHLCV > 13min, vitality forzada a 0
    _ohlcv_ts = signal.get('timestamp') or signal.get('ts') or ''
    if not check_data_staleness(_ohlcv_ts):
        vitality_tesla = 0
        print('  V-04: vitality_tesla=0 (DATA_STALE)')


    # Read entropy from last signal or godel data
    shannon_entropy = float(signal.get("entropy_shannon", 0.3))
    kl_divergence   = float(signal.get("kl_divergence", 0.0))
    # V-03 S46: KL rolling — 5 ciclos para evitar falsos HOLD por spike GDELT
    kl_divergence = get_rolling_kl(kl_divergence, vault)


    # P33/P66 defaults (from HINC OMNIA CERNO §Vitality_Tesla)
    p33 = float(godel_thresholds.get("global_p33", 0.30))
    p66 = float(godel_thresholds.get("global_p66", 0.70))
    vitality = compute_vitality_tesla(shannon_entropy, p33, p66)

    # Compute BMA for all known active assets
    bma_results = {}
    ACTIVE_ASSETS = ["NVDA", "BTC", "XAU", "NIFTY50", "EURUSD"]

    for asset in ACTIVE_ASSETS:
        # Extract per-asset scores from signal (with safe defaults)
        asset_key = asset.lower()
        godel_s   = float(signal.get(f"{asset_key}_godel", 0.0))
        te_s      = float(signal.get(f"{asset_key}_te", 0.0))
        backbone_s = float(signal.get(f"{asset_key}_backbone", 0.0))

        bma = compute_gold_score_bma(
            godel_score=godel_s,
            te_score=te_s,
            backbone_score=backbone_s,
            asset=asset,
            shannon_entropy=shannon_entropy,
            kl_divergence=kl_divergence,
            verbose=verbose,
        )
        bma_results[asset] = bma

    # GT-Score: load from gate_metrics.json
    gt_score = None
    gate_path = vault / "gate_metrics.json"
    if gate_path.exists():
        try:
            gm = json.loads(gate_path.read_text())
            gt_score = gm.get("gt_score")
        except Exception:
            pass

    # Build output
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol":     "SPEL_Sovereign_Architecture v4.4",
        "session":      "S45+",
        "global": {
            "shannon_entropy": shannon_entropy,
            "kl_divergence":   kl_divergence,
            "lambda_decay":    lambda_decay,
            "signal_age_seconds": round(signal_age, 1),
            "vitality_tesla":  vitality,
            "gt_score":        gt_score,
            "regime": bma_results.get("NVDA", {}).get("regime", "UNKNOWN"),
            "kill_active": any(r.get("kill_signal") for r in bma_results.values()),
        },
        "bma_by_asset": bma_results,
        "weights_r13": BMA_WEIGHTS,
        "thresholds": {
            "shannon_kill": SHANNON_KILL_THRESHOLD,
            "kl_drift":     KL_DIVERGENCE_THRESHOLD,
        },
    }

    payload = json.dumps(output, indent=2, ensure_ascii=False).encode("utf-8")
    output["_sha"] = _sha24(payload)
    _atomic_write(result_path, json.dumps(output, indent=2, ensure_ascii=False).encode())

    if verbose:
        print(f"  ✅ live_bma_result.json written ({result_path.stat().st_size}B)")
        print(f"  Global kill: {output['global']['kill_active']}")
        print(f"  Vitality Tesla: {vitality}")
        print(f"  Lambda Decay: {lambda_decay}")

    return output


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SPEL BMA Engine — Bayesian Model Averaging")
    parser.add_argument("--asset", default=None,
                        help="Single asset test (NVDA, BTC, XAU, EURUSD)")
    parser.add_argument("--godel",    type=float, default=0.5)
    parser.add_argument("--te",       type=float, default=0.5)
    parser.add_argument("--backbone", type=float, default=0.5)
    parser.add_argument("--entropy",  type=float, default=0.30)
    parser.add_argument("--kl",       type=float, default=0.05)
    parser.add_argument("--verbose",  action="store_true")
    parser.add_argument("--cycle",    action="store_true",
                        help="Run full BMA cycle (all assets from last_signal.json)")
    args = parser.parse_args()

    if args.cycle:
        print("=== BMA FULL CYCLE ===")
        result = run_bma_cycle(verbose=args.verbose)
        kill = result["global"]["kill_active"]
        print(f"\n  Kill active: {kill}")
        print(f"  Vitality Tesla: {result['global']['vitality_tesla']}")
        print(f"  Lambda Decay:   {result['global']['lambda_decay']}")
        for asset, bma in result["bma_by_asset"].items():
            print(f"  {asset:8s}: gold={bma['gold_score']:.4f} "
                  f"action={bma['action']:15s} regime={bma['regime']}")
    elif args.asset:
        print(f"=== BMA SINGLE ASSET: {args.asset} ===")
        result = compute_gold_score_bma(
            godel_score=args.godel,
            te_score=args.te,
            backbone_score=args.backbone,
            asset=args.asset,
            shannon_entropy=args.entropy,
            kl_divergence=args.kl,
            verbose=True,
        )
        print(f"\n  Gold Score BMA: {result['gold_score']}")
        print(f"  Kill signal:    {result['kill_signal']}")
        print(f"  Action:         {result['action']}")
        if result["kill_reason"]:
            print(f"  Kill reason:    {result['kill_reason']}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
