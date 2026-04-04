"""
spel_forex_bridge.py — SPEL 3.0 · Forex Bridge · IQ Option Demo
Protocolo: SPEL_Sovereign_Architecture v4.4 · Execution_Heads §Forex_Bridge

Provider:  IQ Option (Demo account — NEVER live funds)
Focus:     EURUSD
Trigger:   GDELT Sentiment Threshold (from spel_bayesian_core.py output)

Resilience Shield:
  - Blocks all entries if Shannon entropy > 0.42 (GDELT geopolitical noise)
  - Blocks if KL_Divergence > 0.20 (model drift)
  - Blocks if Lambda Decay < 0.30 (signal too stale, age > ~2.5h)

CLI: python spel_forex_bridge.py [--check | --cycle | --test-block]

Invariantes:
  R21: credenciales desde secrets.json
  R32: atomic writes
  Idempotente: seguro cada 15 min
  No top-level requests/numpy — Ley2/R37
"""

import json
import hashlib
import os
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ─── Paths ─────────────────────────────────────────────────────────────────────
ROOT    = Path(os.environ.get("SPEL_BASE_DIR",
               "/content/drive/MyDrive/ORDEN/SPEL 3.0"))
VAULT   = ROOT / "00_VAULT"
SECRETS = VAULT / "secrets_template.json"

BMA_RESULT_PATH   = VAULT / "live_bma_result.json"
FOREX_LOG_PATH    = VAULT / "forex_bridge_log.json"
FOREX_STATE_PATH  = VAULT / "forex_bridge_state.json"
SIGNAL_PATH       = VAULT / "last_signal.json"

# ─── Resilience Shield thresholds (XML §Forex_Bridge) ─────────────────────────
SHIELD_ENTROPY_MAX   = 0.42   # Shannon GDELT — same as BMA kill
SHIELD_KL_MAX        = 0.20   # KL divergence drift
SHIELD_LAMBDA_MIN    = 0.30   # Minimum signal freshness (Lambda Decay)
SHIELD_GOLD_MIN_EURUSD = 0.55 # Minimum Gold Score BMA to consider EURUSD entry

# IQ Option demo API endpoint (conceptual — replace with real SDK call)
IQ_DEMO_ENDPOINT = "https://iqoption.com/api/demo"   # placeholder


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha24(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def _load_secrets(path: Path = SECRETS) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _tg(token: str, chat_id: str, text: str) -> None:
    """Fire-and-forget Telegram message."""
    import urllib.request
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({"chat_id": chat_id, "text": text[:4096],
                                  "parse_mode": "HTML"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=6,
        )
    except Exception:
        pass


# ─── Resilience Shield ─────────────────────────────────────────────────────────

class ResilienceShield:
    """
    Multi-layer guard that prevents EURUSD entries during
    geopolitical instability, model drift, or stale signals.
    All conditions are checked sequentially; first failure blocks.
    """

    def __init__(self,
                 entropy_max: float  = SHIELD_ENTROPY_MAX,
                 kl_max: float       = SHIELD_KL_MAX,
                 lambda_min: float   = SHIELD_LAMBDA_MIN,
                 gold_min: float     = SHIELD_GOLD_MIN_EURUSD):
        self.entropy_max = entropy_max
        self.kl_max      = kl_max
        self.lambda_min  = lambda_min
        self.gold_min    = gold_min

    def evaluate(self,
                 shannon_entropy: float,
                 kl_divergence: float,
                 lambda_decay: float,
                 gold_score_eurusd: float) -> dict:
        """
        Returns:
            {
              "blocked": bool,
              "reason":  str | None,
              "checks":  dict  (all individual check results)
            }
        """
        checks = {
            "entropy_ok":    shannon_entropy <= self.entropy_max,
            "kl_ok":         kl_divergence   <= self.kl_max,
            "lambda_ok":     lambda_decay     >= self.lambda_min,
            "gold_score_ok": gold_score_eurusd >= self.gold_min,
        }
        check_details = {
            "shannon_entropy":    shannon_entropy,
            "kl_divergence":      kl_divergence,
            "lambda_decay":       lambda_decay,
            "gold_score_eurusd":  gold_score_eurusd,
            "thresholds": {
                "entropy_max":  self.entropy_max,
                "kl_max":       self.kl_max,
                "lambda_min":   self.lambda_min,
                "gold_min":     self.gold_min,
            },
        }

        # Check in priority order
        if not checks["entropy_ok"]:
            return {
                "blocked": True,
                "reason":  f"GEOPOLITICAL_NOISE: shannon_entropy={shannon_entropy:.4f} > {self.entropy_max}",
                "checks":  checks,
                "details": check_details,
            }
        if not checks["kl_ok"]:
            return {
                "blocked": True,
                "reason":  f"MODEL_DRIFT: kl_divergence={kl_divergence:.4f} > {self.kl_max}",
                "checks":  checks,
                "details": check_details,
            }
        if not checks["lambda_ok"]:
            return {
                "blocked": True,
                "reason":  f"STALE_SIGNAL: lambda_decay={lambda_decay:.4f} < {self.lambda_min}",
                "checks":  checks,
                "details": check_details,
            }
        if not checks["gold_score_ok"]:
            return {
                "blocked": True,
                "reason":  f"INSUFFICIENT_CONFIDENCE: gold_score_eurusd={gold_score_eurusd:.4f} < {self.gold_min}",
                "checks":  checks,
                "details": check_details,
            }

        return {
            "blocked": False,
            "reason":  None,
            "checks":  checks,
            "details": check_details,
        }


SHIELD = ResilienceShield()


# ─── IQ Option demo interface ──────────────────────────────────────────────────

def _simulate_iq_order(
    action: str,       # "CALL" | "PUT"
    amount: float,     # position size in USD
    expiry_seconds: int,  # 60, 300, 900, etc.
    gold_score: float,
    account_id: str = "spel-demo",
) -> dict:
    """
    Simulate IQ Option Demo order placement.
    Real implementation: iqoptionapi SDK
      from iqoptionapi.stable_api import IQ_Option
      api = IQ_Option(email, password)
      api.buy(amount, "EURUSD-OTC", action, expiry_seconds)
    """
    import uuid
    order_id = str(uuid.uuid4()).replace("-", "")[:16]
    # Simulated fill probability based on gold_score
    fill_ok = gold_score > 0.60
    expiry_ts = datetime.now(timezone.utc).timestamp() + expiry_seconds

    return {
        "order_id":      order_id,
        "asset":         "EURUSD",
        "action":        action.upper(),
        "amount_usd":    amount,
        "expiry_seconds": expiry_seconds,
        "expiry_ts":     expiry_ts,
        "status":        "DEMO_FILLED" if fill_ok else "DEMO_REJECTED",
        "account_id":    account_id,
        "gold_score":    gold_score,
        "mode":          "DEMO",
        "note":          "Simulated IQ Option Demo. Zero real funds.",
        "ts":            datetime.now(timezone.utc).isoformat(),
    }


# ─── Core: place_order ─────────────────────────────────────────────────────────

def place_order(
    action: str,
    amount: float,
    expiry_seconds: int,
    gold_score: float,
    shield_result: dict,
    secrets: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """
    Attempt to place an EURUSD order through IQ Option Demo.
    Blocked immediately if shield_result["blocked"] is True.
    """
    result = {
        "ts":        datetime.now(timezone.utc).isoformat(),
        "asset":     "EURUSD",
        "action":    action,
        "amount":    amount,
        "gold_score": gold_score,
        "shield":    shield_result,
        "order":     None,
        "placed":    False,
    }

    if shield_result["blocked"]:
        result["reason"] = shield_result["reason"]
        if verbose:
            print(f"  🛡️  SHIELD BLOCKED: {shield_result['reason']}")
        return result

    # Load IQ Option demo credentials
    if secrets is None:
        secrets = _load_secrets()
    iq_account = secrets.get("IQ_ACCOUNT_ID", "spel-demo@example.com")

    order = _simulate_iq_order(
        action=action,
        amount=amount,
        expiry_seconds=expiry_seconds,
        gold_score=gold_score,
        account_id=iq_account,
    )

    result["order"]  = order
    result["placed"] = order["status"] == "DEMO_FILLED"
    result["reason"] = order["status"]

    if verbose:
        print(f"  {'✅' if result['placed'] else '❌'} ORDER: "
              f"{action} EURUSD ${amount} exp={expiry_seconds}s")
        print(f"     ID: {order['order_id']} | Status: {order['status']}")

    return result


# ─── Log management ─────────────────────────────────────────────────────────────

def append_forex_log(trade_result: dict, log_path: Path = FOREX_LOG_PATH) -> None:
    log = []
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text())
        except Exception:
            log = []
    log.append(trade_result)
    if len(log) > 1000:
        log = log[-1000:]
    _atomic_write(log_path, json.dumps(log, indent=2).encode())


# ─── Cycle runner ──────────────────────────────────────────────────────────────

def run_forex_cycle(root: Path = ROOT, verbose: bool = False) -> dict:
    """
    Main 15-min cycle:
    1. Load BMA result
    2. Run Resilience Shield
    3. If clear: place simulated EURUSD order
    4. Log result + update state
    Idempotent.
    """
    vault = root / "00_VAULT"
    bma_path = vault / "live_bma_result.json"
    secrets  = _load_secrets(vault / "secrets_template.json")
    tg_token = secrets.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    tg_chaos = secrets.get("TELEGRAM_CHAOS") or os.environ.get("TELEGRAM_CHAOS", "")

    if not bma_path.exists():
        msg = "SKIPPED: live_bma_result.json missing — run spel_bayesian_core.py first"
        if verbose:
            print(f"  ⚠️  {msg}")
        return {"status": "SKIPPED", "reason": msg}

    try:
        bma = json.loads(bma_path.read_text())
    except Exception as e:
        return {"status": "ERROR", "reason": f"BMA parse error: {e}"}

    # Extract EURUSD-specific values
    eurusd_bma  = bma.get("bma_by_asset", {}).get("EURUSD", {})
    global_data = bma.get("global", {})

    shannon_entropy  = float(global_data.get("shannon_entropy", 0.5))
    kl_divergence    = float(global_data.get("kl_divergence", 0.0))
    lambda_decay     = float(global_data.get("lambda_decay", 0.0))
    gold_score_eur   = float(eurusd_bma.get("gold_score", 0.0))
    action_raw       = eurusd_bma.get("action", "HOLD")

    # Map BMA action to IQ Option direction
    iq_action = None
    if action_raw == "EXECUTE_STRONG":
        iq_action = "CALL"
    elif action_raw == "EXECUTE_WEAK":
        iq_action = "CALL"   # Conservative: CALL on weak signal too (trend bias)
    # else: HOLD, WATCH → no order

    # Run shield
    shield = SHIELD.evaluate(
        shannon_entropy=shannon_entropy,
        kl_divergence=kl_divergence,
        lambda_decay=lambda_decay,
        gold_score_eurusd=gold_score_eur,
    )

    if verbose:
        print(f"  Shield: blocked={shield['blocked']}")
        if shield["reason"]:
            print(f"  Reason: {shield['reason']}")
        print(f"  Gold Score EURUSD: {gold_score_eur:.4f}")
        print(f"  Action: {iq_action or 'HOLD (no trade)'}")

    cycle_result = {
        "ts":          datetime.now(timezone.utc).isoformat(),
        "shield":      shield,
        "gold_score":  gold_score_eur,
        "action_bma":  action_raw,
        "iq_action":   iq_action,
        "order":       None,
        "status":      "HOLD",
    }

    # Place order only if shield clears AND action is defined
    if not shield["blocked"] and iq_action:
        order_result = place_order(
            action=iq_action,
            amount=50.0,           # $50 paper size
            expiry_seconds=900,    # 15-min expiry (matches orchestrator cycle)
            gold_score=gold_score_eur,
            shield_result=shield,
            secrets=secrets,
            verbose=verbose,
        )
        cycle_result["order"]  = order_result
        cycle_result["status"] = "EXECUTED" if order_result["placed"] else "REJECTED"

        # Alert TG_CHAOS on every execution
        if order_result["placed"] and tg_token and tg_chaos:
            _tg(tg_token, tg_chaos,
                f"📈 <b>FOREX EURUSD DRY-RUN</b>\n"
                f"Action: {iq_action} | Gold: {gold_score_eur:.4f}\n"
                f"Shield: CLEAR ✅\n"
                f"Order: {order_result.get('order', {}).get('order_id', 'N/A')}\n"
                f"<i>IQ Option Demo — Simulated</i>")
    elif shield["blocked"] and tg_token and tg_chaos:
        # Optionally report blocks to CHAOS for visibility
        _tg(tg_token, tg_chaos,
            f"🛡️ <b>FOREX SHIELD BLOCK</b>\n"
            f"EURUSD: {shield['reason']}\n"
            f"Gold Score: {gold_score_eur:.4f}\n"
            f"<i>Resilience Shield Active</i>")

    # Persist state
    _atomic_write(vault / "forex_bridge_state.json",
                  json.dumps(cycle_result, indent=2).encode())
    append_forex_log(cycle_result, vault / "forex_bridge_log.json")

    return cycle_result


# ─── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SPEL Forex Bridge — IQ Option Demo")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check",      action="store_true",
                       help="Print current BMA state + shield evaluation")
    group.add_argument("--cycle",      action="store_true",
                       help="Run full forex bridge cycle")
    group.add_argument("--test-block", action="store_true",
                       help="Test shield with high-entropy scenario (should block)")
    parser.add_argument("--verbose",   action="store_true")
    args = parser.parse_args()

    if args.test_block:
        print("=== SHIELD TEST: HIGH ENTROPY ===")
        shield = SHIELD.evaluate(
            shannon_entropy=0.80,   # >> 0.42 → should block
            kl_divergence=0.05,
            lambda_decay=0.90,
            gold_score_eurusd=0.75,
        )
        print(f"  Blocked: {shield['blocked']}")
        print(f"  Reason:  {shield['reason']}")

        print("\n=== SHIELD TEST: CLEAR ENTRY ===")
        shield2 = SHIELD.evaluate(
            shannon_entropy=0.20,
            kl_divergence=0.05,
            lambda_decay=0.85,
            gold_score_eurusd=0.75,
        )
        print(f"  Blocked: {shield2['blocked']}")
        print(f"  Reason:  {shield2['reason'] or 'NONE — ENTRY ALLOWED'}")
        return

    if args.check:
        print("=== FOREX BRIDGE STATUS ===")
        bma_path = BMA_RESULT_PATH
        if not bma_path.exists():
            print("  ⚠️  live_bma_result.json not found")
            return
        bma = json.loads(bma_path.read_text())
        eur = bma.get("bma_by_asset", {}).get("EURUSD", {})
        g   = bma.get("global", {})
        print(f"  Shannon entropy:   {g.get('shannon_entropy')}")
        print(f"  KL divergence:     {g.get('kl_divergence')}")
        print(f"  Lambda decay:      {g.get('lambda_decay')}")
        print(f"  EURUSD gold score: {eur.get('gold_score')}")
        print(f"  EURUSD action:     {eur.get('action')}")
        shield = SHIELD.evaluate(
            shannon_entropy  = float(g.get("shannon_entropy", 0.5)),
            kl_divergence    = float(g.get("kl_divergence", 0.1)),
            lambda_decay     = float(g.get("lambda_decay", 0.5)),
            gold_score_eurusd= float(eur.get("gold_score", 0.0)),
        )
        status = "🛡️ BLOCKED" if shield["blocked"] else "✅ CLEAR"
        print(f"\n  Shield: {status}")
        if shield["reason"]:
            print(f"  Reason: {shield['reason']}")
        return

    if args.cycle:
        print("=== FOREX BRIDGE CYCLE ===")
        result = run_forex_cycle(verbose=args.verbose)
        print(f"\n  Status:     {result['status']}")
        print(f"  Gold Score: {result['gold_score']}")
        if result.get("shield", {}).get("blocked"):
            print(f"  Blocked by: {result['shield']['reason']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
