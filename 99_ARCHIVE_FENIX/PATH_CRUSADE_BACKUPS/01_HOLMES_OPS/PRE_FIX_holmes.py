"""
Holmes/kernel/holmes.py
SPEL 3.0 — Holmes OS v1  ·  Hinc Omnia Cerno
Orquestador soberano: audita, detecta, cuarentena, notifica.
Holmes v1: detección + notificación. NO ejecuta fixes automáticos (eso es v2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path bootstrap (standalone + importable) ─────────────────────────────────
_KERNEL_DIR = Path(__file__).resolve().parent
_HOLMES_DIR = _KERNEL_DIR.parent
_ROOT       = Path(os.environ.get("SPEL_BASE_DIR",
                                  "/content/drive/MyDrive/SPEL-v2.0"))

if str(_HOLMES_DIR) not in sys.path:
    sys.path.insert(0, str(_HOLMES_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("Holmes.Kernel")

# ── Data contracts (canonical — sección 12 v44) ───────────────────────────────
@dataclass
class Anomaly:
    type: str          # "SECRET_MISSING"|"TORCH_VIOLATION"|"SHA_MISMATCH"|"CHECKPOINT_CORRUPT"
    component: str     # archivo o módulo afectado
    description: str
    severity: str      # "LOW"|"MEDIUM"|"HIGH"|"CRITICAL"
    metadata: dict     = field(default_factory=dict)
    timestamp: str     = field(default_factory=lambda:
                               datetime.now(timezone.utc).isoformat())


@dataclass
class SignalPacket:
    correlation_id: str        # SHA-256[:12] del contenido serializado
    decision: str              # "AUDIT_PASS"|"AUDIT_FAIL"|"DEGRADED"
    anomalies: list[Anomaly]
    timestamp_utc: str
    _chain_hash: str           # Ley 1 — HashChain encadenada


def _hashchain(content: str, prev_hash: str = "") -> str:
    """Ley 1: SHA-256(content + prev_hash). Garantiza cadena inmutable."""
    raw = (content + prev_hash).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _build_signal_packet(
    anomalies: list[Anomaly],
    prev_chain_hash: str = "",
) -> SignalPacket:
    ts  = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {"anomalies": [asdict(a) for a in anomalies], "ts": ts},
        sort_keys=True, default=str,
    )
    corr_id    = hashlib.sha256(payload.encode()).hexdigest()[:12]
    chain_hash = _hashchain(payload, prev_chain_hash)

    criticals = [a for a in anomalies if a.severity == "CRITICAL"]
    highs     = [a for a in anomalies if a.severity == "HIGH"]

    if criticals:
        decision = "AUDIT_FAIL"
    elif highs:
        decision = "DEGRADED"
    else:
        decision = "AUDIT_PASS"

    return SignalPacket(
        correlation_id=corr_id,
        decision=decision,
        anomalies=anomalies,
        timestamp_utc=ts,
        _chain_hash=chain_hash,
    )


# ── HolmesKernel ─────────────────────────────────────────────────────────────
class HolmesKernel:
    """
    Orquesta los detectores en ThreadPoolExecutor, cuarentena via Isolator,
    routing de alertas via TelegramRouter, y persiste el SignalPacket.

    Máquina de estados (sección 3 v44):
      IDLE → AUDIT → (pass) → FETCH/INGEST → INFERENCE → REPORT → IDLE
               ↓ (fail)
             QUARANTINE → HEAL_HINT → Telegram → IDLE
    """

    PATROL_INTERVAL_S = 900   # 15 min
    MAX_PATROL_WORKERS = 4

    def __init__(
        self,
        root: Path = _ROOT,
        vault_dir: Optional[Path] = None,
        patrol_interval: int = PATROL_INTERVAL_S,
    ):
        self.root            = root
        self.vault_dir       = vault_dir or (_HOLMES_DIR / "vault")
        self.patrol_interval = patrol_interval
        self._state          = "IDLE"
        self._last_chain     = ""
        self._repair_history : list[dict] = []

        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self._load_history()

        # Lazy imports of sibling modules — never at top-level (Ley 2)
        self._router   : Optional[object] = None
        self._isolator : Optional[object] = None
        self._detectors: list[object]     = []

    # ── Initialization helpers ────────────────────────────────────────────────

    def _load_history(self) -> None:
        hist_path = self.vault_dir / "repair_history.json"
        if hist_path.exists():
            try:
                data = json.loads(hist_path.read_text())
                self._repair_history = data if isinstance(data, list) else []
                if self._repair_history:
                    self._last_chain = self._repair_history[-1].get("_chain_hash", "")
            except Exception as e:
                log.warning("repair_history.json unreadable: %s", e)

    def _save_history(self, packet: SignalPacket) -> None:
        hist_path = self.vault_dir / "repair_history.json"
        entry = {
            **asdict(packet),
            "anomalies": [asdict(a) for a in packet.anomalies],
        }
        self._repair_history.append(entry)
        try:
            hist_path.write_text(
                json.dumps(self._repair_history[-200:], indent=2, default=str)
            )
        except Exception as e:
            log.error("Cannot write repair_history: %s", e)

    def _init_modules(self) -> None:
        """Lazy-import sibling modules. Called once per patrol."""
        if self._router is None:
            try:
                from modules.telegram.messenger import TelegramRouter
                self._router = TelegramRouter()
            except Exception as e:
                log.warning("TelegramRouter unavailable: %s — using fallback", e)
                self._router = _FallbackRouter()

        if self._isolator is None:
            try:
                from modules.quarantine.isolator import Isolator
                self._isolator = Isolator(root=self.root)
            except Exception as e:
                log.warning("Isolator unavailable: %s", e)

        if not self._detectors:
            try:
                from modules.detective.error_detective import ErrorDetective
                self._detectors.append(ErrorDetective(root=self.root))
            except Exception as e:
                log.warning("ErrorDetective unavailable: %s", e)
            try:
                from modules.detective.sha_detective import SHADetective
                self._detectors.append(SHADetective(root=self.root))
            except Exception as e:
                log.warning("SHADetective unavailable: %s", e)

    # ── Core patrol ──────────────────────────────────────────────────────────

    def patrol(self) -> SignalPacket:
        """
        Single patrol cycle. Thread-safe.
        Runs all detectors in parallel, builds SignalPacket, routes alerts.
        """
        self._state = "AUDIT"
        self._init_modules()

        log.info("PATROL START — %d detectors", len(self._detectors))
        all_anomalies: list[Anomaly] = []

        with ThreadPoolExecutor(max_workers=self.MAX_PATROL_WORKERS,
                                thread_name_prefix="holmes-det") as pool:
            futures = {
                pool.submit(det.scan): det.__class__.__name__
                for det in self._detectors
            }
            for fut in as_completed(futures, timeout=60):
                name = futures[fut]
                try:
                    result = fut.result()
                    all_anomalies.extend(result)
                    log.info("  %s → %d anomalies", name, len(result))
                except Exception as e:
                    log.error("  %s CRASHED: %s", name, e)
                    all_anomalies.append(Anomaly(
                        type="DETECTOR_CRASH",
                        component=name,
                        description=str(e),
                        severity="HIGH",
                        metadata={"exception": type(e).__name__},
                    ))

        packet = _build_signal_packet(all_anomalies, self._last_chain)
        self._last_chain = packet._chain_hash
        self._state      = packet.decision

        # Quarantine CRITICAL components
        self._handle_quarantine(all_anomalies)

        # Route alerts
        self._route_alerts(packet)

        # Persist
        self._save_history(packet)
        self._write_signal_packet(packet)

        log.info(
            "PATROL END — decision=%s criticals=%d highs=%d corr=%s",
            packet.decision,
            sum(1 for a in all_anomalies if a.severity == "CRITICAL"),
            sum(1 for a in all_anomalies if a.severity == "HIGH"),
            packet.correlation_id,
        )
        return packet

    def _handle_quarantine(self, anomalies: list[Anomaly]) -> None:
        """Move CRITICAL components to ARCHIVE_S37/ — NEVER deletes (Ley 4)."""
        if self._isolator is None:
            return
        for a in anomalies:
            if a.severity == "CRITICAL" and a.component:
                path = Path(a.component)
                if path.exists() and path.is_file():
                    try:
                        self._isolator.quarantine(path, reason=a.type)
                        log.warning("QUARANTINED: %s (reason=%s)", path.name, a.type)
                    except Exception as e:
                        log.error("Quarantine failed for %s: %s", path, e)

    def _route_alerts(self, packet: SignalPacket) -> None:
        """Route CRITICAL and HIGH anomalies to Telegram via TelegramRouter."""
        if self._router is None:
            return
        criticals = [a for a in packet.anomalies
                     if a.severity in ("CRITICAL", "HIGH")]
        if not criticals:
            return

        header = (
            f"<b>Holmes PATROL [{packet.decision}]</b>\n"
            f"id={packet.correlation_id}\n"
            f"{packet.timestamp_utc[:16]} UTC\n"
            f"{'─'*30}\n"
        )
        lines = []
        for a in criticals:
            repair = _REPAIR_HINTS.get(a.type, "Ver log para instrucciones")
            lines.append(
                f"[{a.severity}] {a.type}\n"
                f"  component: {a.component}\n"
                f"  {a.description}\n"
                f"  fix: {repair}"
            )

        msg = header + "\n\n".join(lines)
        try:
            self._router.send_to_sistema(msg)
        except Exception as e:
            log.error("Telegram route failed: %s", e)

    def _write_signal_packet(self, packet: SignalPacket) -> None:
        """Persist latest SignalPacket to vault/signal_packet_latest.json."""
        out = self.vault_dir / "signal_packet_latest.json"
        try:
            out.write_text(
                json.dumps(
                    {**asdict(packet),
                     "anomalies": [asdict(a) for a in packet.anomalies]},
                    indent=2, default=str,
                )
            )
        except Exception as e:
            log.error("Cannot write signal_packet: %s", e)

    # ── Continuous patrol loop ────────────────────────────────────────────────

    def run_continuous(self) -> None:
        """Blocking loop — runs patrol() every patrol_interval seconds."""
        log.info("Holmes continuous patrol STARTED (interval=%ds)", self.patrol_interval)
        while True:
            try:
                packet = self.patrol()
                log.info("Next patrol in %ds", self.patrol_interval)
            except KeyboardInterrupt:
                log.info("Holmes patrol interrupted by operator")
                break
            except Exception as e:
                log.critical("patrol() unhandled exception: %s", e, exc_info=True)
            time.sleep(self.patrol_interval)

    # ── State probe ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state


# ── Repair hints (Ley 3 v44 — comandos exactos) ──────────────────────────────
_REPAIR_HINTS: dict[str, str] = {
    "TORCH_VIOLATION":    "Mover 'import torch' dentro de función. Ver sección 7 v44.",
    "SHA_MISMATCH":       "Ejecutar: python scripts/SPEL_INSTITUTIONAL_AUDITOR_V41.py",
    "SECRET_MISSING":     "Colab: Secrets panel → verificar nombre exacto (R34).",
    "CHECKPOINT_CORRUPT": "Restaurar desde TG_BACKUP. R29: backup semanal obligatorio.",
    "DETECTOR_CRASH":     "Ver Holmes repair_history.json para traceback completo.",
    "YAML_CONDITION_BUG": "GitHub UI → editar j_audit.if: 'github.event.schedule != \"\"'",
    "LAST_SIGNAL_STALE":  "workflow_dispatch manual → verificar J2 produce last_signal.json",
    "SHA_REGISTRY_EMPTY": "Ejecutar: python scripts/spel_preflight_s24.py → rebuild registry",
}


# ── Fallback router (no Telegram configured) ─────────────────────────────────
class _FallbackRouter:
    def send_to_sistema(self, msg: str) -> None:
        log.warning("[FALLBACK_TG] %s", msg[:200])


# ── CLI entrypoint ────────────────────────────────────────────────────────────
def _cli() -> None:
    parser = argparse.ArgumentParser(
        prog="holmes",
        description="Holmes OS v1 — SPEL 3.0 Sovereign Auditor",
    )
    parser.add_argument("--patrol",    action="store_true",
                        help="Run single patrol cycle and exit")
    parser.add_argument("--continuous",action="store_true",
                        help="Run continuous patrol loop (15min interval)")
    parser.add_argument("--interval",  type=int, default=900,
                        help="Patrol interval in seconds (default: 900)")
    parser.add_argument("--root",      type=str, default=None,
                        help="Override SPEL_BASE_DIR")
    args = parser.parse_args()

    root = Path(args.root) if args.root else _ROOT
    kernel = HolmesKernel(root=root, patrol_interval=args.interval)

    if args.patrol:
        packet = kernel.patrol()
        print(json.dumps(
            {**asdict(packet),
             "anomalies": [asdict(a) for a in packet.anomalies]},
            indent=2, default=str,
        ))
    elif args.continuous:
        kernel.run_continuous()
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
