"""
Holmes/modules/detective/sha_detective.py
Verifica SHA_REGISTRY.json (Ley 1) contra artefactos físicos en Drive.
Detecta: phantom entries, ghost files, mismatch post-write, missing checkpoints.
R32: SHA_REGISTRY es la única fuente de verdad. Cualquier divergencia = CRIT.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .base_detective import BaseDetective

log = logging.getLogger("Holmes.SHADetective")

# Canonical SHA_REGISTRY keys cross-referenced in v44 §2
_CANONICAL_ASSETS = ("BTC", "XAU", "NIFTY50", "NVDA")
_CHECKPOINT_NAMES = {
    "BTC":     "BTC_LSTM_v3c_F1_0.4994.pt",
    "XAU":     "XAU_LSTM_v3_godel_valloss0.4386.pt",
    "NIFTY50": "NIFTY50_LSTM_v3_godel_valloss0.3784.pt",
    "NVDA":    "NVDA_LSTM_v3_godel_valloss0.3857.pt",
}
# sha_v5 values from SHA_REGISTRY post-S36 (R28 — inamovible until next ingest)
_EXPECTED_SHA_V5 = {
    "BTC":     "89f5872b5d4d",
    "XAU":     "c38c6fac1084",
    "NIFTY50": "ec534663b272",
    "NVDA":    "80dff64429d4",
}


def _sha12(path: Path) -> str:
    """SHA-256 of file (header + footer 64KB each). Returns hex[:12]."""
    h = hashlib.sha256()
    size = path.stat().st_size
    try:
        with open(path, "rb") as f:
            h.update(f.read(65_536))
            if size > 65_536:
                f.seek(max(0, size - 65_536))
                h.update(f.read(65_536))
    except Exception:
        return "READ_ERROR"
    return h.hexdigest()[:12]


class SHADetective(BaseDetective):
    """
    Three scan tracks:
      A. SHA_REGISTRY.json itself: loads, parses, checks completeness.
      B. Parquet files: real SHA vs SHA_REGISTRY[asset].sha_v5.
      C. Checkpoints: physical .pt existence vs R29 expectation.
    """

    def scan(self) -> list:
        from kernel.holmes import Anomaly
        anomalies: list[Anomaly] = []

        registry, reg_anomalies = self._load_registry(Anomaly)
        anomalies.extend(reg_anomalies)

        if registry:
            anomalies.extend(self._check_parquets(registry, Anomaly))
            anomalies.extend(self._check_checkpoints(Anomaly))

        return anomalies

    # ── Track A: registry load + completeness ────────────────────────────────
    def _load_registry(self, Anomaly) -> tuple[dict, list]:
        reg_path = (self.root / "meta" / "SHA_REGISTRY.json")
        vault_path = Path(__file__).resolve().parents[2] / "vault" / "SHA_REGISTRY.json"

        path = reg_path if reg_path.exists() else (
               vault_path if vault_path.exists() else None)

        if path is None:
            return {}, [Anomaly(
                type="SHA_REGISTRY_EMPTY",
                component="meta/SHA_REGISTRY.json",
                description=(
                    "SHA_REGISTRY.json not found in meta/ or vault/. "
                    "Rebuild: python scripts/spel_preflight_s24.py"
                ),
                severity="CRITICAL",
            )]

        try:
            registry = json.loads(path.read_text())
        except Exception as e:
            return {}, [Anomaly(
                type="SHA_REGISTRY_EMPTY",
                component=str(path),
                description=f"SHA_REGISTRY.json unreadable: {e}",
                severity="CRITICAL",
            )]

        anomalies = []
        for asset in _CANONICAL_ASSETS:
            if asset not in registry:
                anomalies.append(Anomaly(
                    type="SHA_MISMATCH",
                    component=f"SHA_REGISTRY/{asset}",
                    description=(
                        f"Asset '{asset}' absent from SHA_REGISTRY. "
                        "Execute atomic parquet write (R32)."
                    ),
                    severity="HIGH",
                ))

        return registry, anomalies

    # ── Track B: parquet SHA verification ────────────────────────────────────
    def _check_parquets(self, registry: dict, Anomaly) -> list:
        anomalies = []
        for asset in _CANONICAL_ASSETS:
            entry   = registry.get(asset, {})
            exp_sha = entry.get("sha_v5", _EXPECTED_SHA_V5.get(asset, "?"))

            pq_dir  = self.root / "data_lake" / asset / "intraday"
            if not pq_dir.exists():
                # Try legacy path layout
                pq_dir = self.root / "data_lake" / asset / "ohlcv" / "aggregated"

            pqs = sorted(pq_dir.glob("*.parquet")) if pq_dir.exists() else []

            if not pqs:
                anomalies.append(Anomaly(
                    type="SHA_MISMATCH",
                    component=f"data_lake/{asset}",
                    description=(
                        f"No parquet found for {asset} in {pq_dir}. "
                        "Execute harvest: HARVESTER.run([asset])."
                    ),
                    severity="CRITICAL",
                    metadata={"expected_sha": exp_sha},
                ))
                continue

            real_sha = _sha12(pqs[-1])

            if real_sha == "READ_ERROR":
                anomalies.append(Anomaly(
                    type="CHECKPOINT_CORRUPT",
                    component=str(pqs[-1].relative_to(self.root)),
                    description=f"{asset} parquet unreadable (ghost file).",
                    severity="CRITICAL",
                ))
            elif real_sha != exp_sha and exp_sha != "?":
                anomalies.append(Anomaly(
                    type="SHA_MISMATCH",
                    component=str(pqs[-1].relative_to(self.root)),
                    description=(
                        f"{asset} SHA mismatch. "
                        f"real={real_sha} expected={exp_sha}. "
                        "R32 violation: SHA_REGISTRY not updated after write. "
                        "Run: python scripts/SPEL_INSTITUTIONAL_AUDITOR_V41.py"
                    ),
                    severity="CRITICAL",
                    metadata={"real_sha": real_sha, "expected_sha": exp_sha,
                              "n_rows": entry.get("n_rows", "?")},
                ))
            else:
                log.info("  SHA OK: %s = %s", asset, real_sha)

        return anomalies

    # ── Track C: checkpoint existence (R29) ──────────────────────────────────
    def _check_checkpoints(self, Anomaly) -> list:
        """
        R29: checkpoints live in Drive /checkpoints/, never in GitHub main.
        Minimum size guard: < 50KB = ghost/corrupt.
        R35: does not verify internal layer name (self.linear vs self.fc) —
             that requires torch, which is never imported here (R37).
        """
        ckpt_dir  = self.root / "checkpoints"
        anomalies = []

        for asset, filename in _CHECKPOINT_NAMES.items():
            path = ckpt_dir / filename
            if not path.exists():
                anomalies.append(Anomaly(
                    type="CHECKPOINT_CORRUPT",
                    component=f"checkpoints/{filename}",
                    description=(
                        f"{asset} checkpoint MISSING. "
                        "Restore from TG_BACKUP (R29)."
                    ),
                    severity="CRITICAL",
                    metadata={"asset": asset},
                ))
            elif path.stat().st_size < 50_000:
                anomalies.append(Anomaly(
                    type="CHECKPOINT_CORRUPT",
                    component=f"checkpoints/{filename}",
                    description=(
                        f"{asset} checkpoint suspect: {path.stat().st_size}B "
                        f"(< 50KB). Likely ghost. Restore from TG_BACKUP."
                    ),
                    severity="HIGH",
                    metadata={"size_bytes": path.stat().st_size},
                ))
            else:
                log.info("  CKPT OK: %s (%.2fKB)", filename, path.stat().st_size/1024)

        return anomalies
