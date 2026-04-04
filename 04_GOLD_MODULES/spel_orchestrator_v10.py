"""
spel_orchestrator_v10.py — SPEL 3.0 · Main Orchestrator v10
Protocolo: SPEL_Sovereign_Architecture v4.4 · CIELO_ABIERTO_SHADOW

Reemplaza spel_orchestrator_v9.py (añade: BMA, Web3, Forex, dashboard export,
Termux Watchdog SOS, SHA registry intraday assets, force-unlock).

Ciclo: cada 15 minutos (cron: */15 * * * *)
Secuencia de ejecución:
  1. FORCE_UNLOCK en live_graph_data.json (XML §Audit_Fixes Lock_Release)
  2. Actualizar system_pulse.json (Dead Man's Switch)
  3. SHA_REGISTRY: registrar EURUSD + XAU_1m/5m/15m/30m (H-05)
  4. Correr spel_bayesian_core.py → live_bma_result.json
  5. Correr spel_web3_adapter.py  → dry-run trades BTC/NVDA
  6. Correr spel_forex_bridge.py  → EURUSD shield + demo order
  7. Exportar live_dashboard_stats.json (GT-Score, VT, Lambda, Gold Score BMA)
  8. Watchdog SOS: si pulse > 15 min sin actualizar → TG_CHAOS
  9. Actualizar live_graph_data.json con BMA fields

CLI: python spel_orchestrator_v10.py [--dry-run | --watchdog-check | --status]

Invariantes:
  R21: credenciales desde secrets.json
  R32: atomic writes
  R37/Ley2: imports pesados dentro de funciones
  Idempotente: seguro si el cron lo re-ejecuta antes de terminar el ciclo anterior
  EF-25: sandbox PROHIBIDO en sys.path
"""

import json
import hashlib
import os
import sys
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

# ─── Paths ─────────────────────────────────────────────────────────────────────
# ─── 3-way ROOT detection (S46 PATH_COLLISION fix) ───────────────────────────
_IS_GH = os.environ.get('GITHUB_ACTIONS') == 'true'
def _detect_root() -> Path:
    if _IS_GH:
        return Path(os.environ.get('GITHUB_WORKSPACE', '.')).resolve()
    if os.environ.get('SPEL_BASE_DIR'):
        return Path(os.environ['SPEL_BASE_DIR']).resolve()
    _p = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
    return _p if _p.exists() else Path('.')
ROOT = _detect_root()
# ─────────────────────────────────────────────────────────────────────────────

VAULT   = ROOT / "00_VAULT"
SECRETS = VAULT / "secrets_template.json"
REGISTRY_PATH    = VAULT / "registry" / "SHA_REGISTRY.json"
LIVE_GRAPH_PATH  = VAULT / "live_graph_data.json"
PULSE_PATH       = VAULT / "system_pulse.json"
BMA_RESULT_PATH  = VAULT / "live_bma_result.json"
DASHBOARD_PATH   = VAULT / "live_dashboard_stats.json"
GATE_METRICS     = VAULT / "gate_metrics.json"
SIGNAL_PATH      = VAULT / "last_signal.json"
DATA_LAKE        = ROOT  / "05_DATA_LAKE"
ORCH_LOG_PATH    = VAULT / "orchestrator_v10_log.json"

# Watchdog: SOS if pulse older than this
PULSE_MAX_AGE_SECONDS = 1200  # S46 FIX: 20min (GH scheduler latency tolerance)

# Gold SPEL modules (add to sys.path safely)
GOLD_DIR   = ROOT / "04_GOLD_MODULES"
HOLMES_OPS = ROOT / "01_HOLMES_OPS"

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    """R32: never partial state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha12(path: Path) -> str:
    """SHA-256 truncated to 12 chars (SHA_REGISTRY standard)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return "ERR"
    return h.hexdigest()[:12]


def _sha24(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def _load_secrets(path: Path = SECRETS) -> dict:
    """R21: zero hardcode."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _tg(token: str, chat_id: str, text: str, tag: str = "") -> bool:
    """Fire-and-forget TG message. Returns True on success."""
    import urllib.request
    if not token or not chat_id:
        return False
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({"chat_id": chat_id, "text": text[:4096],
                                  "parse_mode": "HTML"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=8,
        )
        return True
    except Exception as e:
        if tag:
            print(f"  TG[{tag}] warn: {e}")
        return False


def _log_event(events: list, status: str, step: str, detail: str = "") -> None:
    events.append({
        "ts":     datetime.now(timezone.utc).isoformat(),
        "status": status,
        "step":   step,
        "detail": detail,
    })
    icon = {"OK": "✅", "WARN": "⚠️", "ERR": "❌",
            "SKIP": "⏭️", "SOS": "🚨", "LOCK": "🔓"}.get(status, "•")
    print(f"  {icon} [{status:5}] {step}: {detail}")


# ─── Step 1: Force-Unlock live_graph_data.json ─────────────────────────────────

def step_force_unlock(events: list) -> bool:
    """
    XML §Audit_Fixes Lock_Release: live_graph_data.json
    action='FORCE_UNLOCK_ON_START'
    """
    if not LIVE_GRAPH_PATH.exists():
        _log_event(events, "SKIP", "force_unlock", "live_graph_data.json not found")
        return True
    try:
        lg = json.loads(LIVE_GRAPH_PATH.read_text())
        was_locked = lg.get("lock", False)
        lg["lock"] = False
        lg["lock_released_by"] = "orchestrator_v10"
        lg["lock_released_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(LIVE_GRAPH_PATH, json.dumps(lg, indent=2).encode())
        status = "LOCK" if was_locked else "OK"
        _log_event(events, status, "force_unlock",
                   f"lock: {was_locked} → False")
        return True
    except Exception as e:
        _log_event(events, "ERR", "force_unlock", str(e))
        return False


# ─── Step 2: Update system_pulse.json ──────────────────────────────────────────

def step_update_pulse(events: list) -> None:
    """Dead Man's Switch: touch every cycle."""
    pulse = {}
    if PULSE_PATH.exists():
        try:
            pulse = json.loads(PULSE_PATH.read_text())
        except Exception:
            pass
    pulse["last_pulse_utc"]   = datetime.now(timezone.utc).isoformat()
    pulse["orchestrator"]     = "v10"
    pulse["cycle_count"]      = pulse.get("cycle_count", 0) + 1
    pulse["system_health"]    = "SOVEREIGN"
    _atomic_write(PULSE_PATH, json.dumps(pulse, indent=2).encode())
    _log_event(events, "OK", "pulse_update",
               f"cycle #{pulse['cycle_count']}")


# ─── Step 3: SHA_REGISTRY — register intraday assets ────────────────────────────

def step_sha_registry_intraday(events: list) -> None:
    """
    XML §Audit_Fixes Registry_Update:
    Append EURUSD, XAU_1m, XAU_5m, XAU_15m, XAU_30m to SHA_REGISTRY.
    H-05 fix: these assets were absent from the registry.
    Idempotent: only adds missing entries.
    """
    INTRADAY_ASSETS = {
        "EURUSD":   "05_DATA_LAKE/processed/EURUSD_features.parquet",
        "XAU_1m":   "05_DATA_LAKE/processed/XAU_1m.parquet",
        "XAU_5m":   "05_DATA_LAKE/processed/XAU_5m.parquet",
        "XAU_15m":  "05_DATA_LAKE/processed/XAU_15m.parquet",
        "XAU_30m":  "05_DATA_LAKE/processed/XAU_30m.parquet",
    }

    if not REGISTRY_PATH.exists():
        _log_event(events, "SKIP", "sha_registry_intraday",
                   "SHA_REGISTRY.json not found")
        return

    try:
        reg = json.loads(REGISTRY_PATH.read_text())
    except Exception as e:
        _log_event(events, "ERR", "sha_registry_intraday", f"parse: {e}")
        return

    updated = 0
    for asset_key, rel_path in INTRADAY_ASSETS.items():
        full_path = ROOT / rel_path
        if full_path.exists():
            new_hash = _sha12(full_path)
            if reg.get(asset_key) != new_hash:
                reg[asset_key] = new_hash
                updated += 1
        else:
            # Register as PENDING if file doesn't exist yet (H-05 placeholder)
            if asset_key not in reg:
                reg[asset_key] = "PENDING_HARVEST"
                updated += 1

    if updated:
        _atomic_write(REGISTRY_PATH, json.dumps(reg, indent=2).encode())
        _log_event(events, "OK", "sha_registry_intraday",
                   f"{updated} intraday assets updated/added")
    else:
        _log_event(events, "SKIP", "sha_registry_intraday",
                   "all intraday assets already current")


# ─── Step 4: Run BMA core ──────────────────────────────────────────────────────

def step_run_bma(events: list, verbose: bool = False) -> Optional[dict]:
    """
    Import spel_bayesian_core (from GOLD_DIR) and run cycle.
    Lazy import — R37/Ley2.
    """
    try:
        # EF-25: no sandbox in path
        gold_str = str(GOLD_DIR)
        if gold_str not in sys.path and "sandbox" not in gold_str:
            sys.path.insert(0, gold_str)

        import importlib.util
        bma_path = GOLD_DIR / "spel_bayesian_core.py"
        if not bma_path.exists():
            _log_event(events, "SKIP", "bma_core",
                       "spel_bayesian_core.py not in GOLD_DIR")
            return None

        spec = importlib.util.spec_from_file_location("spel_bayesian_core",
                                                       str(bma_path))
        bma_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bma_mod)

        result = bma_mod.run_bma_cycle(root=ROOT, verbose=verbose)
        kill_active = result.get("global", {}).get("kill_active", False)
        _log_event(events, "OK", "bma_core",
                   f"Gold score computed. kill_active={kill_active}")
        return result
    except Exception as e:
        _log_event(events, "ERR", "bma_core", f"{e}")
        return None


# ─── Step 5: Run Web3 cycle ────────────────────────────────────────────────────

def step_run_web3(events: list, bma_result: Optional[dict],
                  verbose: bool = False) -> None:
    """Lazy-import and run spel_web3_adapter.py cycle."""
    if bma_result and bma_result.get("global", {}).get("kill_active"):
        _log_event(events, "SKIP", "web3_adapter",
                   "BMA kill_active — no web3 trades this cycle")
        return
    try:
        import importlib.util
        w3_path = GOLD_DIR / "spel_web3_adapter.py"
        if not w3_path.exists():
            _log_event(events, "SKIP", "web3_adapter",
                       "spel_web3_adapter.py not in GOLD_DIR")
            return
        spec = importlib.util.spec_from_file_location("spel_web3_adapter",
                                                       str(w3_path))
        w3_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w3_mod)

        res = w3_mod.run_web3_cycle(root=ROOT, verbose=verbose)
        _log_event(events, "OK", "web3_adapter",
                   f"trades_executed={res.get('trades_executed', 0)}")
    except Exception as e:
        _log_event(events, "ERR", "web3_adapter", str(e))


# ─── Step 6: Run Forex cycle ───────────────────────────────────────────────────

def step_run_forex(events: list, bma_result: Optional[dict],
                   verbose: bool = False) -> None:
    """Lazy-import and run spel_forex_bridge.py cycle."""
    try:
        import importlib.util
        fx_path = GOLD_DIR / "spel_forex_bridge.py"
        if not fx_path.exists():
            _log_event(events, "SKIP", "forex_bridge",
                       "spel_forex_bridge.py not in GOLD_DIR")
            return
        spec = importlib.util.spec_from_file_location("spel_forex_bridge",
                                                       str(fx_path))
        fx_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fx_mod)

        res = fx_mod.run_forex_cycle(root=ROOT, verbose=verbose)
        shield_blocked = res.get("shield", {}).get("blocked", False)
        _log_event(events, "OK", "forex_bridge",
                   f"status={res.get('status')} shield_blocked={shield_blocked}")
    except Exception as e:
        _log_event(events, "ERR", "forex_bridge", str(e))


# ─── Step 7: Export live_dashboard_stats.json ───────────────────────────────────

def step_export_dashboard(events: list, bma_result: Optional[dict]) -> None:
    """
    XML §Dashboard_C2 §Metrics_Exposed:
    Export live_dashboard_stats.json with:
      GT_Score, Vitality_Tesla, Lambda_Decay, Gold_Score_BMA
    Consumed by Telegram WebApp HTML5.
    """
    try:
        # Load BMA result
        bma = bma_result or {}
        if not bma and BMA_RESULT_PATH.exists():
            try:
                bma = json.loads(BMA_RESULT_PATH.read_text())
            except Exception:
                pass

        global_data = bma.get("global", {})

        # Load GT-Score from gate_metrics
        gt_score = global_data.get("gt_score")
        if gt_score is None and GATE_METRICS.exists():
            try:
                gm = json.loads(GATE_METRICS.read_text())
                gt_score = gm.get("gt_score")
            except Exception:
                pass

        # Load last signal for additional fields
        signal = {}
        if SIGNAL_PATH.exists():
            try:
                signal = json.loads(SIGNAL_PATH.read_text())
            except Exception:
                pass

        # Per-asset Gold Scores BMA
        asset_scores = {
            asset: {
                "gold_score_bma": data.get("gold_score", 0.0),
                "action":         data.get("action", "HOLD"),
                "regime":         data.get("regime", "UNKNOWN"),
                "kill_signal":    data.get("kill_signal", False),
            }
            for asset, data in bma.get("bma_by_asset", {}).items()
        }

        # Determine best signal (highest non-killed gold score)
        best_asset = None
        best_score = 0.0
        for asset, d in asset_scores.items():
            if not d["kill_signal"] and d["gold_score_bma"] > best_score:
                best_score = d["gold_score_bma"]
                best_asset = asset

        dashboard = {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "protocol":        "SPEL_Sovereign_Architecture v4.4",
            "gate_r30": {
                "day":    9,
                "target": 63,
                "capital": 100000,
            },
            # XML §Dashboard_C2 §Metrics_Exposed
            "metrics": {
                "GT_Score":       gt_score,
                "Vitality_Tesla": global_data.get("vitality_tesla"),
                "Lambda_Decay":   global_data.get("lambda_decay"),
                "Gold_Score_BMA": global_data.get(
                    "gold_score_bma",
                    asset_scores.get("NVDA", {}).get("gold_score_bma")
                ),
                "Shannon_Entropy": global_data.get("shannon_entropy"),
                "KL_Divergence":   global_data.get("kl_divergence"),
                "Kill_Active":     global_data.get("kill_active", False),
                "Regime":          global_data.get("regime", "UNKNOWN"),
            },
            "assets":          asset_scores,
            "best_signal": {
                "asset":      best_asset,
                "gold_score": best_score,
            },
            "last_signal": {
                "signal":     signal.get("signal"),
                "action":     signal.get("action", "HOLD"),
                "confidence": signal.get("confidence", 0.0),
                "ts":         signal.get("timestamp") or signal.get("ts"),
            },
            "system": {
                "orchestrator": "v10",
                "mode":         "STEALTH_AUDIT",
                "web3_active":  True,
                "forex_active": True,
                "tg_webapp":    "SPEL_MOBILE/index.html",
            },
        }

        payload = json.dumps(dashboard, indent=2, ensure_ascii=False).encode()
        dashboard["_sha"] = _sha24(payload)
        _atomic_write(DASHBOARD_PATH,
                      json.dumps(dashboard, indent=2, ensure_ascii=False).encode())
        _log_event(events, "OK", "dashboard_export",
                   f"live_dashboard_stats.json ({DASHBOARD_PATH.stat().st_size}B)")
    except Exception as e:
        _log_event(events, "ERR", "dashboard_export", f"{e}\n{traceback.format_exc()[:300]}")


# ─── Step 8: Watchdog SOS ──────────────────────────────────────────────────────

def step_watchdog_sos(events: list, secrets: dict) -> None:
    """
    XML §Environment_Sync method='Termux_Watchdog_v3':
    If system_pulse.json is older than PULSE_MAX_AGE_SECONDS (15 min),
    send SOS to TG_CHAOS. Indicates Colab/GH Actions failure.
    """
    tg_token = secrets.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    tg_chaos = secrets.get("TELEGRAM_CHAOS") or os.environ.get("TELEGRAM_CHAOS", "")

    if not PULSE_PATH.exists():
        # No pulse file — create initial state
        _atomic_write(PULSE_PATH, json.dumps({
            "last_pulse_utc": datetime.now(timezone.utc).isoformat(),
            "orchestrator": "v10",
            "cycle_count": 0,
            "system_health": "INITIALIZING",
        }, indent=2).encode())
        _log_event(events, "OK", "watchdog",
                   "system_pulse.json created (initial state)")
        return

    try:
        pulse = json.loads(PULSE_PATH.read_text())
        last_pulse_str = pulse.get("last_pulse_utc") or pulse.get("ts")

        if not last_pulse_str:
            raise ValueError("No timestamp in pulse file")

        last_pulse = datetime.fromisoformat(last_pulse_str)
        # Make timezone-aware if needed
        if last_pulse.tzinfo is None:
            last_pulse = last_pulse.replace(tzinfo=timezone.utc)

        age_seconds = (datetime.now(timezone.utc) - last_pulse).total_seconds()

        if age_seconds > PULSE_MAX_AGE_SECONDS:
            msg = (
                f"🚨 <b>SPEL SOS — DEAD MAN'S SWITCH TRIGGERED</b>\n"
                f"system_pulse.json age: {age_seconds/60:.1f} min "
                f"(threshold: {PULSE_MAX_AGE_SECONDS/60:.0f} min)\n"
                f"Last pulse: {last_pulse_str}\n"
                f"Orchestrator: v10\n"
                f"Action required: Check Colab / GH Actions J0-J4\n"
                f"<i>Termux Watchdog v3 · Hinc Omnia Cerno</i>"
            )
            sent = _tg(tg_token, tg_chaos, msg, tag="SOS")
            _log_event(events, "SOS", "watchdog",
                       f"pulse age={age_seconds/60:.1f}min > threshold. "
                       f"TG_CHAOS alert sent: {sent}")
        else:
            _log_event(events, "OK", "watchdog",
                       f"pulse age={age_seconds:.0f}s (healthy)")
    except Exception as e:
        _log_event(events, "WARN", "watchdog", f"pulse check error: {e}")


# ─── Step 9: Update live_graph with BMA enrichment ─────────────────────────────

def step_enrich_live_graph(events: list, bma_result: Optional[dict]) -> None:
    """
    Enrich live_graph_data.json nodes with BMA gold_score field.
    Non-destructive: only adds/updates 'gold_score_bma' and 'bma_ts' keys.
    """
    if not bma_result:
        _log_event(events, "SKIP", "live_graph_enrich", "no BMA result")
        return
    if not LIVE_GRAPH_PATH.exists():
        _log_event(events, "SKIP", "live_graph_enrich", "live_graph_data.json missing")
        return

    try:
        lg = json.loads(LIVE_GRAPH_PATH.read_text())
        global_bma = bma_result.get("global", {})
        assets_bma = bma_result.get("bma_by_asset", {})

        # Enrich summary at graph level
        lg["bma_summary"] = {
            "kill_active":   global_bma.get("kill_active"),
            "vitality_tesla": global_bma.get("vitality_tesla"),
            "lambda_decay":  global_bma.get("lambda_decay"),
            "shannon_entropy": global_bma.get("shannon_entropy"),
            "regime":        global_bma.get("regime"),
            "bma_ts":        bma_result.get("generated_at"),
        }

        # Enrich DATA nodes that match known assets
        nodes = lg.get("nodes", [])
        for node in nodes:
            node_file = node.get("file", "")
            for asset, bma_data in assets_bma.items():
                # Match by filename pattern (e.g. NVDA_features.parquet)
                if asset.upper() in node_file.upper():
                    node["gold_score_bma"] = bma_data.get("gold_score", 0.0)
                    node["bma_action"]     = bma_data.get("action", "HOLD")
                    node["bma_regime"]     = bma_data.get("regime", "UNKNOWN")
                    node["bma_ts"]         = bma_result.get("generated_at")
                    break

        lg["lock"] = False  # Always ensure unlocked after enrichment
        _atomic_write(LIVE_GRAPH_PATH, json.dumps(lg, indent=2).encode())
        _log_event(events, "OK", "live_graph_enrich",
                   f"{len(nodes)} nodes enriched with BMA fields")
    except Exception as e:
        _log_event(events, "ERR", "live_graph_enrich", str(e))


# ─── Persist orchestrator log ───────────────────────────────────────────────────

def _persist_orch_log(events: list) -> None:
    """Append cycle events to rolling orchestrator log (max 200 cycles)."""
    log = []
    if ORCH_LOG_PATH.exists():
        try:
            log = json.loads(ORCH_LOG_PATH.read_text())
        except Exception:
            log = []

    cycle_entry = {
        "cycle_ts": datetime.now(timezone.utc).isoformat(),
        "events":   events,
        "ok_count": sum(1 for e in events if e["status"] == "OK"),
        "err_count": sum(1 for e in events if e["status"] == "ERR"),
    }
    log.append(cycle_entry)
    if len(log) > 200:
        log = log[-200:]
    _atomic_write(ORCH_LOG_PATH, json.dumps(log, indent=2).encode())


# ─── Main orchestrator cycle ────────────────────────────────────────────────────



def _secrets_health_check() -> bool:
    """
    V-01 FIX: Verifica presencia y validez de secrets críticos.
    En GH Actions: lee de os.environ (GitHub Secrets inyectados).
    En Colab: lee de secrets_template.json con fallback a os.environ.
    Detecta placeholders <YOUR_X_HERE> — tan peligrosos como ausentes.
    Retorna False si algún secret crítico falta o es placeholder.
    Emite SOS a TG_CHAOS y (en GH Actions) llama sys.exit(1).
    """
    import re as _re_sh
    _PH_RE = _re_sh.compile(r'<YOUR_|YOUR_|CHANGE_ME|PLACEHOLDER', _re_sh.I)
    _CRITICAL = ['TELEGRAM_TOKEN', 'TELEGRAM_SISTEMA', 'TELEGRAM_CHAOS',
                  'GITHUB_TOKEN']
    _is_gh = os.environ.get('GITHUB_ACTIONS') == 'true'
    _sec = _load_secrets()
    _env = os.environ

    missing, placeholder = [], []
    for k in _CRITICAL:
        v = _env.get(k) or _sec.get(k, '')
        if not v:
            missing.append(k)
        elif _PH_RE.search(str(v)):
            placeholder.append(k)

    if missing or placeholder:
        _msg = (f'AUTH_VOID ciclo {datetime.now(timezone.utc).isoformat()}: '
                f'{"MISSING="+str(missing) if missing else ""} '
                f'{"PLACEHOLDER="+str(placeholder) if placeholder else ""}')
        print(f'  AUTH_VOID: {_msg}')
        # Intento TG (si el token sí existe)
        _tg_tok = _env.get('TELEGRAM_TOKEN') or _sec.get('TELEGRAM_TOKEN', '')
        _tg_ch  = _env.get('TELEGRAM_CHAOS', '-1003736496382')
        if _tg_tok and not _PH_RE.search(_tg_tok):
            _tg(_tg_tok, _tg_ch,
                f'🚫 AUTH_VOID\nEnv: {"GH" if _is_gh else "COLAB"}\n{_msg}')
        if _is_gh:
            sys.exit(1)  # Marca job FAIL en GH — watchdog distingue vs Colab muerto
        return False
    return True

def run_cycle(dry_run: bool = False, verbose: bool = False) -> dict:
    """
    Full 15-minute orchestration cycle.
    dry_run=True: runs steps 1-3 (infrastructure) but skips BMA/trades.
    Idempotent.
    """
    cycle_ts = datetime.now(timezone.utc).isoformat()
    events: list = []
    secrets = _load_secrets()

    print(f"\n{'='*60}")
    print(f"  SPEL ORCHESTRATOR v10 — {'DRY-RUN' if dry_run else 'LIVE'}")
    print(f"  {cycle_ts}")
    print(f"{'='*60}\n")

    # Step 1: Force-unlock
    step_force_unlock(events)

    # Step 2: Update pulse (before any SOS check)
    step_update_pulse(events)

    # Step 3: SHA registry intraday
    step_sha_registry_intraday(events)

    # Watchdog SOS check (runs even in dry-run to catch dead systems)
    step_watchdog_sos(events, secrets)

    bma_result = None
    if not dry_run:
        # Step 4: BMA core
        bma_result = step_run_bma(events, verbose=verbose)

        # Step 5: Web3 (only if BMA ran cleanly)
        step_run_web3(events, bma_result, verbose=verbose)

        # Step 6: Forex bridge
        step_run_forex(events, bma_result, verbose=verbose)

        # Step 7: Export dashboard
        step_export_dashboard(events, bma_result)

        # Step 8: Enrich live graph
        step_enrich_live_graph(events, bma_result)

    # Persist log
    _persist_orch_log(events)

    # TG SISTEMA: end-of-cycle report
    tg_token  = secrets.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    tg_sistema = secrets.get("TELEGRAM_SISTEMA") or os.environ.get("TELEGRAM_SISTEMA", "")
    ok_count  = sum(1 for e in events if e["status"] in ("OK", "LOCK"))
    err_count = sum(1 for e in events if e["status"] == "ERR")
    sos_count = sum(1 for e in events if e["status"] == "SOS")

    if tg_token and tg_sistema and not dry_run:
        _tg(tg_token, tg_sistema,
            f"⚙️ <b>ORCHESTRATOR v10 — CYCLE DONE</b>\n"
            f"OK: {ok_count} | ERR: {err_count} | SOS: {sos_count}\n"
            f"BMA kill: {bma_result.get('global',{}).get('kill_active','N/A') if bma_result else 'N/A'}\n"
            f"Gate R30: day 9/63\n"
            f"<i>Hinc Omnia Cerno</i>",
            tag="SISTEMA")

    summary = {
        "cycle_ts":  cycle_ts,
        "mode":      "DRY_RUN" if dry_run else "LIVE",
        "ok":        ok_count,
        "errors":    err_count,
        "sos":       sos_count,
        "events":    events,
        "bma_active": bma_result is not None,
    }

    print(f"\n{'='*60}")
    print(f"  CYCLE COMPLETE: OK={ok_count} ERR={err_count} SOS={sos_count}")
    print(f"{'='*60}\n")

    return summary


# ─── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SPEL Orchestrator v10 — Main 15-min cycle")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Infra-only (no BMA/trades)")
    parser.add_argument("--watchdog-check", action="store_true",
                        help="Only check pulse staleness")
    parser.add_argument("--status",        action="store_true",
                        help="Print current dashboard stats")
    parser.add_argument("--verbose",       action="store_true")
    args = parser.parse_args()

    if args.watchdog_check:
        print("=== WATCHDOG CHECK ===")
        secrets = _load_secrets()
        events: list = []
        step_watchdog_sos(events, secrets)
        for e in events:
            print(f"  [{e['status']}] {e['step']}: {e['detail']}")
        return

    if args.status:
        print("=== DASHBOARD STATUS ===")
        if DASHBOARD_PATH.exists():
            d = json.loads(DASHBOARD_PATH.read_text())
            m = d.get("metrics", {})
            print(f"  GT_Score:       {m.get('GT_Score')}")
            print(f"  Vitality_Tesla: {m.get('Vitality_Tesla')}")
            print(f"  Lambda_Decay:   {m.get('Lambda_Decay')}")
            print(f"  Gold_Score_BMA: {m.get('Gold_Score_BMA')}")
            print(f"  Kill_Active:    {m.get('Kill_Active')}")
            print(f"  Regime:         {m.get('Regime')}")
            print(f"  Generated at:   {d.get('generated_at')}")
        else:
            print("  live_dashboard_stats.json not found")
        if PULSE_PATH.exists():
            p = json.loads(PULSE_PATH.read_text())
            print(f"\n  Pulse last:     {p.get('last_pulse_utc')}")
            print(f"  Cycle count:    {p.get('cycle_count', '?')}")
        return

    run_cycle(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
