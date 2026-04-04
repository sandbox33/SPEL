"""
spel_web3_adapter.py — SPEL 3.0 · Web3 Sovereign Adapter
Protocolo: SPEL_Sovereign_Architecture v4.4 · Execution_Heads §Web3_Sovereign

Toolkit: Coinbase AgentKit (conceptual — no live SDK required for testnet sim)
Network: NEAR Testnet (dry-run simulation)
Mode:    Dry_Run_On_Chain

dry_run_trade() ONLY fires when Gold Score BMA > 0.85.
All "on-chain" ops are simulated and logged — zero real funds at risk.

CLI: python spel_web3_adapter.py --gold-score 0.90 --asset BTC

Invariantes:
  R21: credenciales desde secrets.json (cero hardcode)
  R32: atomic writes
  Idempotente: seguro de re-ejecutar en cada ciclo de 15 min
"""

import json
import hashlib
import os
import sys
import argparse
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ─── Paths ─────────────────────────────────────────────────────────────────────
ROOT    = Path(os.environ.get("SPEL_BASE_DIR",
               "/content/drive/MyDrive/ORDEN/SPEL 3.0"))
VAULT   = ROOT / "00_VAULT"
SECRETS = VAULT / "secrets_template.json"
WEB3_LOG_PATH = VAULT / "web3_dry_run_log.json"
BMA_RESULT    = VAULT / "live_bma_result.json"

# Execution threshold (XML §Web3_Sovereign)
GOLD_SCORE_THRESHOLD = 0.85

# NEAR Testnet simulated params
NEAR_TESTNET_RPC = "https://rpc.testnet.near.org"
NEAR_NETWORK     = "testnet"

# Supported assets for Web3 dry-run
WEB3_ASSETS = {"BTC", "NVDA"}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha24(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def _load_secrets(path: Path = SECRETS) -> dict:
    """R21: load from secrets.json. Returns empty dict if absent."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _tg_chaos(token: str, chat_id: str, text: str) -> None:
    """Sends alert to TG_CHAOS channel — fire-and-forget."""
    import urllib.request
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({
                    "chat_id": chat_id,
                    "text":    text[:4096],
                    "parse_mode": "HTML",
                }).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=6,
        )
    except Exception:
        pass


def _simulate_near_tx(
    account_id: str,
    asset: str,
    action: str,
    size_usd: float,
    gold_score: float,
) -> dict:
    """
    Simulate a NEAR Protocol transaction on testnet.
    In dry-run mode: generates a synthetic tx_hash and receipt.
    Real AgentKit integration would call:
        coinbase_agentkit.Action.create_transaction(...)
    """
    tx_id = str(uuid.uuid4()).replace("-", "")[:32]
    block_height_sim = 140000000 + int(datetime.now(timezone.utc).timestamp() % 1000000)

    return {
        "tx_hash":     tx_id,
        "network":     NEAR_NETWORK,
        "account_id":  account_id,
        "asset":       asset,
        "action":      action,
        "size_usd":    size_usd,
        "gold_score":  gold_score,
        "block_height_sim": block_height_sim,
        "status":      "DRY_RUN_SUCCESS",
        "gas_used_sim": round(gold_score * 0.0012, 6),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "note":        "Simulated. Zero real funds. NEAR Testnet conceptual.",
    }


# ─── Core: dry_run_trade ────────────────────────────────────────────────────────

def dry_run_trade(
    asset: str,
    action: str,           # "BUY" | "SELL" | "HOLD"
    size_usd: float,
    gold_score: float,
    secrets: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """
    Execute a dry-run trade simulation on NEAR Testnet.

    Fires ONLY if gold_score > GOLD_SCORE_THRESHOLD (0.85).
    Returns a trade_result dict (idempotent — no side effects).

    Args:
        asset:      "BTC", "NVDA" (web3-compatible assets)
        action:     "BUY" or "SELL"
        size_usd:   Position size in USD (from Kelly fraction)
        gold_score: BMA Gold Score [0,1]
        secrets:    Dict from secrets.json (R21)
        verbose:    Debug output
    """
    result = {
        "asset":       asset,
        "action":      action,
        "size_usd":    size_usd,
        "gold_score":  gold_score,
        "ts":          datetime.now(timezone.utc).isoformat(),
        "executed":    False,
        "reason":      None,
        "tx":          None,
    }

    # Gate 1: threshold check
    if gold_score <= GOLD_SCORE_THRESHOLD:
        result["reason"] = (
            f"BELOW_THRESHOLD: gold_score={gold_score:.4f} "
            f"≤ {GOLD_SCORE_THRESHOLD} — no trade"
        )
        if verbose:
            print(f"  ⏭️  {asset} HOLD: {result['reason']}")
        return result

    # Gate 2: asset support
    if asset.upper() not in WEB3_ASSETS:
        result["reason"] = f"UNSUPPORTED_ASSET: {asset} not in {WEB3_ASSETS}"
        if verbose:
            print(f"  ⚠️  {result['reason']}")
        return result

    # Gate 3: action validation
    if action.upper() not in ("BUY", "SELL"):
        result["reason"] = f"INVALID_ACTION: {action}"
        return result

    # Load secrets (R21)
    if secrets is None:
        secrets = _load_secrets()

    near_account = secrets.get("NEAR_ACCOUNT_ID", "spel-testnet.testnet")

    # Execute simulation
    tx = _simulate_near_tx(
        account_id=near_account,
        asset=asset.upper(),
        action=action.upper(),
        size_usd=size_usd,
        gold_score=gold_score,
    )

    result["executed"] = True
    result["reason"]   = "GOLD_SCORE_THRESHOLD_MET"
    result["tx"]       = tx

    if verbose:
        print(f"  ✅ DRY_RUN TRADE:")
        print(f"     Asset:      {asset}")
        print(f"     Action:     {action}")
        print(f"     Size USD:   ${size_usd:,.2f}")
        print(f"     Gold Score: {gold_score:.4f}")
        print(f"     TX hash:    {tx['tx_hash']}")
        print(f"     Block sim:  {tx['block_height_sim']}")

    # Alert TG_CHAOS for every simulated execution (useful for monitoring)
    tg_token   = secrets.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    tg_chaos   = secrets.get("TELEGRAM_CHAOS") or os.environ.get("TELEGRAM_CHAOS", "")
    if tg_token and tg_chaos:
        _tg_chaos(tg_token, tg_chaos,
                  f"⚡ <b>WEB3 DRY-RUN EXECUTED</b>\n"
                  f"Asset: {asset} | Action: {action}\n"
                  f"Gold Score: {gold_score:.4f}\n"
                  f"Size USD: ${size_usd:,.0f}\n"
                  f"TX: {tx['tx_hash']}\n"
                  f"<i>NEAR Testnet — Simulated</i>")

    return result


# ─── Log management ─────────────────────────────────────────────────────────────

def append_trade_log(trade_result: dict, log_path: Path = WEB3_LOG_PATH) -> None:
    """Append trade result to persistent log. R32 idempotent."""
    log = []
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text())
        except Exception:
            log = []
    log.append(trade_result)
    # Keep last 500 entries max
    if len(log) > 500:
        log = log[-500:]
    _atomic_write(log_path, json.dumps(log, indent=2).encode())


# ─── Cycle runner (called by orchestrator_v10) ──────────────────────────────────

def run_web3_cycle(root: Path = ROOT, verbose: bool = False) -> dict:
    """
    Read live_bma_result.json, execute dry_run_trade for each asset
    where gold_score > 0.85.
    Idempotent — safe to run every 15 minutes.
    """
    vault = root / "00_VAULT"
    bma_path = vault / "live_bma_result.json"
    secrets = _load_secrets(vault / "secrets_template.json")

    if not bma_path.exists():
        return {"status": "SKIPPED", "reason": "live_bma_result.json missing"}

    try:
        bma = json.loads(bma_path.read_text())
    except Exception as e:
        return {"status": "ERROR", "reason": f"bma parse error: {e}"}

    if bma.get("global", {}).get("kill_active"):
        return {"status": "KILL_ACTIVE", "reason": "BMA global kill — no web3 trades"}

    trades_executed = []
    for asset, bma_result in bma.get("bma_by_asset", {}).items():
        if asset.upper() not in WEB3_ASSETS:
            continue
        gold_score = bma_result.get("gold_score", 0.0)
        action     = "BUY" if bma_result.get("action") in ("EXECUTE_STRONG", "EXECUTE_WEAK") else "HOLD"
        if action == "HOLD":
            continue

        trade = dry_run_trade(
            asset=asset,
            action=action,
            size_usd=1000.0,   # Fixed $1000 size (paper mode)
            gold_score=gold_score,
            secrets=secrets,
            verbose=verbose,
        )
        if trade["executed"]:
            append_trade_log(trade, vault / "web3_dry_run_log.json")
            trades_executed.append(trade)

    return {
        "status":          "OK",
        "trades_executed": len(trades_executed),
        "ts":              datetime.now(timezone.utc).isoformat(),
        "details":         trades_executed,
    }


# ─── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SPEL Web3 Adapter — NEAR Testnet")
    parser.add_argument("--asset",      default="BTC",   help="BTC | NVDA")
    parser.add_argument("--action",     default="BUY",   help="BUY | SELL")
    parser.add_argument("--gold-score", type=float, default=0.90)
    parser.add_argument("--size",       type=float, default=1000.0)
    parser.add_argument("--cycle",      action="store_true",
                        help="Run full BMA-driven cycle")
    parser.add_argument("--verbose",    action="store_true")
    args = parser.parse_args()

    if args.cycle:
        print("=== WEB3 CYCLE ===")
        result = run_web3_cycle(verbose=args.verbose)
        print(json.dumps(result, indent=2))
    else:
        print(f"=== DRY RUN TRADE: {args.asset} ===")
        result = dry_run_trade(
            asset=args.asset,
            action=args.action,
            size_usd=args.size,
            gold_score=args.gold_score,
            verbose=True,
        )
        print(f"\n  Executed:   {result['executed']}")
        print(f"  Reason:     {result['reason']}")
        if result["tx"]:
            print(f"  TX hash:    {result['tx']['tx_hash']}")


if __name__ == "__main__":
    main()
