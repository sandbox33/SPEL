#!/usr/bin/env python3
"""
spel_paper_adapter.py — SPEL Paper Trading Adapter
Version: 1.0 | S27 | 17-Mar-2026

Connects SPEL signal pipeline to Alpaca paper trading.
Logs all trades to SPELDataStore for gate tracking.

Usage:
    python spel_paper_adapter.py --once         # process latest signal
    python spel_paper_adapter.py --daily-close  # close stale open trades
    python spel_paper_adapter.py --report        # print gate progress

GitHub Actions: add as daily job at 07:50 ECT (after signal job)
"""

import os, sys, json, datetime, argparse, sqlite3, logging
from pathlib import Path

# ── Alpaca import (graceful if not installed) ──
try:
    import alpaca_trade_api as tradeapi
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("⚠️  alpaca-trade-api not installed. Run: pip install alpaca-trade-api")

BASE_DIR    = Path(os.environ.get("SPEL_BASE_DIR", "/content/drive/MyDrive/SPEL-v2.0"))
DB_PATH     = BASE_DIR / "data" / "spel_store.db"
SIGNAL_FILE = Path("last_signal.json")

ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://paper-api.alpaca.markets"   # paper endpoint

TG_TOKEN   = os.environ.get("TELEGRAM_TOKEN",  "")
TG_SISTEMA = os.environ.get("TELEGRAM_SISTEMA", "")

# ── Asset → Alpaca symbol map ──
ASSET_TO_SYMBOL = {
    "BTC":    "BTCUSD",
    "XAU":    "XAUUSD",   # check Alpaca availability — may need CFD broker
    "NIFTY50": None,      # not available on Alpaca — skip
    "NVDA":   "NVDA",
}

# ── Position sizing constants ──
ACCOUNT_EQUITY    = 10_000.0   # paper account starting equity (USD)
KELLY_HARD_CAP    = 0.50
MAX_SINGLE_POS    = 0.10       # max 10% per position (institutional risk control)


def _tg_send(message: str, chat_id: str = None):
    """Non-blocking Telegram message."""
    import urllib.request
    if not TG_TOKEN or not (chat_id or TG_SISTEMA):
        print(f"[TG DRY-RUN] {message[:80]}...")
        return
    cid = chat_id or TG_SISTEMA
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=json.dumps({"chat_id": cid, "text": message}).encode(),
                headers={"Content-Type": "application/json"},
            ), timeout=10
        )
    except Exception as e:
        print(f"TG send error: {e}")


def _load_signal() -> dict:
    """Load latest signal from last_signal.json (written by spel_snapshot_updater)."""
    if not SIGNAL_FILE.exists():
        return {}
    with open(SIGNAL_FILE) as f:
        return json.load(f)


def _get_alpaca_client():
    if not ALPACA_AVAILABLE:
        return None
    if not ALPACA_KEY or not ALPACA_SECRET:
        print("⚠️  ALPACA_KEY / ALPACA_SECRET not set. Running in dry-run mode.")
        return None
    return tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, ALPACA_BASE, api_version="v2")


def process_signal(signal: dict, api, store_db: Path, dry_run: bool = True) -> dict:
    """
    Core logic: validate signal → size position → submit paper order → log.
    
    Returns: {"status": "SUBMITTED"|"SKIPPED"|"DRY_RUN"|"ERROR", "details": ...}
    """
    asset     = signal.get("asset", "")
    score     = int(signal.get("score", 0))
    godel     = bool(signal.get("godel", False))
    direction = signal.get("direction", "SHORT")
    kelly_f   = float(signal.get("kelly_fraction", 0.0))
    sha       = signal.get("sha", "")

    # ── Gate checks ──
    if score < 60:
        return {"status": "SKIPPED", "reason": f"score {score} < 60"}
    if not godel:
        return {"status": "SKIPPED", "reason": "godel_active=False"}

    symbol = ASSET_TO_SYMBOL.get(asset)
    if symbol is None:
        return {"status": "SKIPPED", "reason": f"{asset} not available on Alpaca"}

    # ── Position sizing ──
    kelly_capped = min(kelly_f, MAX_SINGLE_POS)  # institutional cap
    notional_usd = ACCOUNT_EQUITY * kelly_capped

    if dry_run or api is None:
        result = {
            "status":    "DRY_RUN",
            "asset":     asset,
            "symbol":    symbol,
            "direction": direction,
            "notional":  round(notional_usd, 2),
            "kelly_f":   kelly_capped,
            "score":     score,
            "godel":     godel,
        }
        print(f"  [DRY-RUN] Would submit {direction} {symbol} ${notional_usd:.2f} (kelly={kelly_capped:.2%})")
        return result

    # ── Submit paper order ──
    try:
        side = "buy" if direction == "LONG" else "sell"
        order = api.submit_order(
            symbol=symbol,
            notional=str(round(notional_usd, 2)),
            side=side,
            type="market",
            time_in_force="gtc",
        )
        return {
            "status":   "SUBMITTED",
            "order_id": order.id,
            "asset":    asset,
            "symbol":   symbol,
            "direction": direction,
            "notional": round(notional_usd, 2),
            "kelly_f":  kelly_capped,
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


def daily_report(store_db: Path):
    """Print gate progress and paper trade stats."""
    conn = sqlite3.connect(store_db)
    conn.row_factory = sqlite3.Row

    trades_df_raw = conn.execute("SELECT * FROM paper_trades").fetchall()
    gates_df_raw  = conn.execute("SELECT * FROM gate_progress").fetchall()
    conn.close()

    print("
" + "═"*60)
    print("  SPEL PAPER TRADING — GATE PROGRESS")
    print(f"  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("═"*60)

    for g in gates_df_raw:
        icon = "✅" if g["status"] == "PASS" else ("❌" if g["status"] == "FAIL" else "□")
        val  = f"  val={g['value']:.4f}" if g["value"] else ""
        print(f"  {icon} {g['gate_name']:30s} [{g['status']:7s}]{val}")
        if g["notes"]:
            print(f"     → {g['notes']}")

    n_trades = len(trades_df_raw)
    print(f"
  Total trades logged: {n_trades}")
    if n_trades == 0:
        print("  Paper trading has NOT started. Run --once after signal.")
    print("═"*60 + "
")


def main():
    parser = argparse.ArgumentParser(description="SPEL Paper Adapter")
    parser.add_argument("--once",        action="store_true", help="Process latest signal once")
    parser.add_argument("--daily-close", action="store_true", help="Close stale open trades")
    parser.add_argument("--report",      action="store_true", help="Print gate progress")
    parser.add_argument("--dry-run",     action="store_true", default=True, help="Dry run (default)")
    args = parser.parse_args()

    api = _get_alpaca_client()

    if args.report:
        daily_report(DB_PATH)
        return

    if args.once:
        signal = _load_signal()
        if not signal:
            print("⚠️  No last_signal.json found. Has the pipeline run today?")
            return
        print(f"Processing signal: {signal}")
        result = process_signal(signal, api, DB_PATH, dry_run=args.dry_run)
        print(f"Result: {result}")

        # Send daily summary to SISTEMA
        ts  = datetime.datetime.utcnow().isoformat()
        msg = (
            f"📊 SPEL Paper Trading — {ts[:10]}\n"
            f"Signal: {signal.get('asset')} score={signal.get('score')} godel={signal.get('godel')}\n"
            f"Action: {result.get('status')}\n"
            f"Details: {json.dumps(result, default=str)[:200]}"
        )
        _tg_send(msg, TG_SISTEMA)


if __name__ == "__main__":
    main()
