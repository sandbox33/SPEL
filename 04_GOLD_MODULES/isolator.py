"""
Holmes/modules/quarantine/isolator.py
Ley 4: NUNCA elimina. Mueve a ARCHIVE_S37/. Registra en quarantine_log.json.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("Holmes.Isolator")

_ARCHIVE_DIR = "ARCHIVE_S37"


class Isolator:
    def __init__(self, root: Path):
        self.root    = root
        self.archive = root / _ARCHIVE_DIR
        self.archive.mkdir(parents=True, exist_ok=True)
        self._log_path = (
            Path(__file__).resolve().parents[2] / "vault" / "quarantine_log.json"
        )

    def quarantine(self, path: Path, reason: str = "") -> Path:
        """Move file to ARCHIVE_S37/. Returns new path. Never deletes."""
        if not path.exists():
            raise FileNotFoundError(f"Quarantine target not found: {path}")

        dest = self.archive / path.name
        if dest.exists():
            ts    = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            dest  = self.archive / f"{path.stem}_{ts}{path.suffix}"

        shutil.move(str(path), str(dest))
        self._log(str(path), str(dest), reason)
        log.info("QUARANTINE: %s → %s (reason=%s)", path.name, dest.name, reason)
        return dest

    def _log(self, src: str, dst: str, reason: str) -> None:
        entries = []
        if self._log_path.exists():
            try:
                entries = json.loads(self._log_path.read_text())
            except Exception:
                pass
        entries.append({
            "ts":     datetime.now(timezone.utc).isoformat(),
            "src":    src,
            "dst":    dst,
            "reason": reason,
        })
        try:
            self._log_path.write_text(
                json.dumps(entries[-500:], indent=2, default=str)
            )
        except Exception as e:
            log.error("quarantine_log write failed: %s", e)
