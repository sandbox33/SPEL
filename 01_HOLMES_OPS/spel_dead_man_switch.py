"""
spel_dead_man_switch.py — SPEL 3.0 · Dead Man's Switch
Ubicación: 01_HOLMES_OPS/spel_dead_man_switch.py

Propósito: escribir / verificar system_pulse.json desde GH Actions.
El workflow lo llama en dos momentos del job 'patrol':
  - Al inicio: --check  (leer estado anterior, enviar SOS si stale)
  - Al final:  --pulse  (escribir timestamp fresco)

CLI:
  python spel_dead_man_switch.py --check
  python spel_dead_man_switch.py --pulse

Invariantes:
  R21: secrets desde os.environ únicamente (nunca hardcode)
  R32: atomic write (tmp → replace)
  Idempotente: seguro si se llama múltiples veces por ciclo
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ─── Root resolution ──────────────────────────────────────────────────────────
# En GH Actions: SPEL_BASE_DIR=. (workspace root)
# En Colab:       /content/drive/MyDrive/ORDEN/SPEL 3.0
ROOT = Path(os.environ.get("SPEL_BASE_DIR", "."))
VAULT = ROOT / "00_VAULT"
PULSE_PATH = VAULT / "system_pulse.json"

# Umbral: si el pulso lleva más de N segundos → SOS
MAX_STALE_SECONDS = 1800   # 30 minutos (2 ciclos de 15 min)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    """R32: never partial state on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _tg_send(token: str, chat_id: str, text: str) -> bool:
    """Fire-and-forget Telegram message. Returns True on success."""
    if not token or not chat_id:
        return False
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
            timeout=8,
        )
        return True
    except Exception as e:
        print(f"  TG warn: {e}", file=sys.stderr)
        return False


def _read_pulse() -> dict:
    """Load system_pulse.json. Returns empty dict if missing or corrupt."""
    if not PULSE_PATH.exists():
        return {}
    try:
        return json.loads(PULSE_PATH.read_text())
    except Exception:
        return {}


def _write_pulse(status: str, extra: dict | None = None) -> None:
    """Atomically update system_pulse.json with current timestamp."""
    pulse = _read_pulse()
    now   = datetime.now(timezone.utc).isoformat()

    pulse["last_pulse_utc"]  = now
    pulse["orchestrator"]    = "github_actions_patrol"
    pulse["status"]          = status
    pulse["cycle_count"]     = pulse.get("cycle_count", 0) + 1
    pulse["spel_base_dir"]   = str(ROOT)

    if extra:
        pulse.update(extra)

    _atomic_write(PULSE_PATH, json.dumps(pulse, indent=2).encode())
    print(f"[DMS] pulse written: status={status} cycle={pulse['cycle_count']}")


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_check() -> None:
    """
    Read previous pulse. If stale → send SOS to TG_CHAOS.
    Always exits 0 (non-blocking — GH workflow uses || true anyway,
    but we're explicit here so the step is always green).
    """
    token    = os.environ.get("TELEGRAM_TOKEN", "")
    chaos_id = os.environ.get("TELEGRAM_CHAOS", "")

    pulse = _read_pulse()

    if not pulse:
        print("[DMS] No previous pulse found — first run or artifact expired. OK.")
        return

    last_ts  = pulse.get("last_pulse_utc") or pulse.get("ts", "")
    status   = pulse.get("status", "UNKNOWN")
    cycle    = pulse.get("cycle_count", "?")

    print(f"[DMS] Previous pulse:")
    print(f"      last_pulse_utc = {last_ts}")
    print(f"      status         = {status}")
    print(f"      cycle_count    = {cycle}")

    if not last_ts:
        print("[DMS] No timestamp in pulse — skipping staleness check.")
        return

    try:
        last = datetime.fromisoformat(last_ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - last).total_seconds()
    except ValueError as e:
        print(f"[DMS] Cannot parse timestamp: {e}")
        return

    print(f"[DMS] Pulse age: {age_seconds:.0f}s (threshold: {MAX_STALE_SECONDS}s)")

    if age_seconds > MAX_STALE_SECONDS:
        msg = (
            f"🚨 <b>SPEL SOS — DEAD MAN'S SWITCH</b>\n"
            f"system_pulse age: <b>{age_seconds / 60:.1f} min</b> "
            f"(threshold: {MAX_STALE_SECONDS // 60} min)\n"
            f"Last pulse: {last_ts}\n"
            f"Status: {status} | Cycle: {cycle}\n"
            f"Action: Check GH Actions 'patrol' job\n"
            f"Repo: sandbox33/SPEL → Actions tab\n"
            f"<i>Holmes OS V4.0 · Hinc Omnia Cerno</i>"
        )
        sent = _tg_send(token, chaos_id, msg)
        print(f"[DMS] SOS sent to TG_CHAOS: {sent}")
    else:
        print("[DMS] Pulse is fresh — system healthy.")


def cmd_pulse() -> None:
    """
    Write a fresh pulse timestamp. Called at the END of the patrol cycle
    to confirm the job completed successfully.
    Exits 0 on success, 1 on write failure (so GH marks the step failed
    if the vault directory is inaccessible).
    """
    try:
        _write_pulse(
            status="COMPLETE",
            extra={
                "last_job":      "patrol",
                "runner":        "github_actions",
                "spel_workflow": "patrol.yml",
            },
        )
        print(f"[DMS] Pulse updated: COMPLETE — {PULSE_PATH}")
    except Exception as e:
        print(f"[DMS] ERROR writing pulse: {e}", file=sys.stderr)
        sys.exit(1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPEL Dead Man's Switch — GH Actions patrol helper"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read previous pulse; send SOS if stale (exit 0 always)",
    )
    parser.add_argument(
        "--pulse",
        action="store_true",
        help="Write fresh COMPLETE pulse timestamp (exit 1 on write failure)",
    )
    args = parser.parse_args()

    if args.check:
        cmd_check()
    elif args.pulse:
        cmd_pulse()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
