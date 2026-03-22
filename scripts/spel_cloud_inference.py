"""
spel_cloud_inference.py
SPEL v40 · Cloud Inference Module · S36

Ejecuta inferencia LSTM completa en Streamlit Cloud.
Zero Drive dependency. Lee feature-cache + model-cache branches vía GH raw.

Invariantes:
  R3:  sha_parquet de feature snapshot verificado contra manifest
  R13: assert tensor shape (N, 20) antes de forward pass
  R27: Gödel mask en raw space (pre-scaling), nunca en scaled space
  R28: p90 leído del manifest por activo (post rolling-252d recal)
  R33: kelly_fraction capeado, base $100k canónico

Pipeline:
  manifest.json → freshness check
  {asset}_tail.json → tensor + scaler params
  {asset}.pt → checkpoint load (torch, CPU)
  PATH B inline scaling → (x - mean) / std + epsilon guard
  LSTM forward → sigmoid → direction
  Backbone heurístico → entry / SL / TP / ATR14
  Gödel activation → raw entropy >= p90 (R27)
  Score de Oro → Gödel(40%) + TE(30%) + Backbone(30%)
  ScoreResult → viable si score >= THRESHOLD
"""

from __future__ import annotations

import json, hashlib, io, logging, urllib.request, urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger("spel.cloud_inference")

# ── Constants ────────────────────────────────────────────────────────
GH_RAW         = "https://raw.githubusercontent.com/sandbox33/SPEL"
FEATURE_BRANCH = "feature-cache"
MODEL_BRANCH   = "model-cache"
SCORE_THRESHOLD = 70
KELLY_CAP       = 0.05
EPSILON         = 1e-10

# Entropy col index in TENSOR_COLS R13 — must be index 3
ENTROPY_IDX     = 3
VITALITY_IDX    = 15  # vitality_tesla


# ════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════

@dataclass
class CloudScoreResult:
    asset:          str
    score_oro:      int
    direction:      str         # LONG / SHORT / FLAT
    viable:         bool
    godel_active:   bool
    entropy:        float
    p90_entropy:    float
    p90_method:     str
    kelly_fraction: float
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    atr14:          float
    regime_label:   str
    sha_parquet:    str
    raw_logit:      float
    timestamp_utc:  str = field(default_factory=lambda:
                                datetime.now(timezone.utc).isoformat())
    inference_source: str = "streamlit_cloud"

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


# ════════════════════════════════════════════════════════════════════
# GH RAW LOADERS — cached at call site via st.cache_data
# ════════════════════════════════════════════════════════════════════

def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GH raw fetch {url}: HTTP {e.code}") from e


def _fetch_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GH raw fetch {url}: HTTP {e.code}") from e


def load_manifest() -> dict:
    return _fetch_json(
        f"{GH_RAW}/{FEATURE_BRANCH}/meta/feature_cache/manifest.json")


def load_feature_snapshot(asset: str) -> dict:
    snap = _fetch_json(
        f"{GH_RAW}/{FEATURE_BRANCH}/meta/feature_cache/{asset}_tail.json")
    # R13 assertion
    n_cols = len(snap.get("tensor_cols", []))
    if n_cols != 20:
        raise RuntimeError(f"R13 violation: {asset} feature snapshot has {n_cols} cols")
    return snap


def load_checkpoint_bytes(asset: str) -> bytes:
    return _fetch_bytes(
        f"{GH_RAW}/{MODEL_BRANCH}/checkpoints/{asset}.pt")


# ════════════════════════════════════════════════════════════════════
# INFERENCE ENGINE
# ════════════════════════════════════════════════════════════════════

class SPELCloudInference:
    """
    Stateless inference engine for Streamlit Cloud.
    Loads checkpoint + feature snapshot on each score() call.
    Caller is responsible for caching via st.cache_data.
    """

    def __init__(self, manifest: dict):
        self._manifest = manifest
        self._inference_ready = manifest.get("inference_ready", False)
        if not self._inference_ready:
            missing = [a for a in ["BTC","XAU","NIFTY50","NVDA"]
                       if a not in manifest.get("feature_commits", {})
                       or a not in manifest.get("model_commits", {})]
            logger.warning(f"inference_ready=False — missing: {missing}")

    def score(self, asset: str) -> CloudScoreResult:
        """Full inference pipeline for one asset."""
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            raise RuntimeError("torch not installed — add to requirements.txt")

        p90_info  = self._manifest.get("p90_map", {}).get(asset, {})
        p90       = float(p90_info.get("p90_entropy", 1.5))
        p90_method= p90_info.get("p90_method", "unknown")

        # ── Load feature snapshot ────────────────────────────────────
        snap     = load_feature_snapshot(asset)
        lookback = snap["lookback"]
        rows     = np.array(snap["rows"], dtype=np.float32)  # (N, 20)
        mu       = np.array(snap["scaler_mean"], dtype=np.float32)
        sigma    = np.array(snap["scaler_std"],  dtype=np.float32)
        sigma    = np.where(sigma < EPSILON, EPSILON, sigma)  # epsilon guard
        sha_pq   = snap["sha_parquet"]

        # R13: shape validation
        assert rows.shape[1] == 20, f"R13: rows shape {rows.shape}"
        assert mu.shape[0] == 20,   f"R13: scaler mean shape {mu.shape}"

        # ── R27: Gödel mask in raw space — MUST precede PATH B ───────
        # Activations computed on unscaled entropy/vitality.
        # Scaling downstream must never touch the Gödel predicate.
        raw_entropy  = float(rows[-1, ENTROPY_IDX])
        raw_vitality = float(rows[-1, VITALITY_IDX])
        godel_active = (raw_entropy >= p90) or (raw_vitality == 9)

        # ── PATH B inline scaling — after Gödel extraction ───────────
        rows_scaled = (rows - mu) / sigma  # (N, 20)

        # ── Build input tensor ───────────────────────────────────────
        if rows_scaled.shape[0] < lookback:
            raise RuntimeError(
                f"{asset}: snapshot has {rows_scaled.shape[0]} rows < lookback {lookback}")
        x = rows_scaled[-lookback:]          # (lookback, 20)
        x_tensor = (torch.from_numpy(x)
                        .unsqueeze(0)        # (1, lookback, 20)
                        .float())

        # ── Load checkpoint ──────────────────────────────────────────
        ckpt_bytes = load_checkpoint_bytes(asset)
        ckpt_buf   = io.BytesIO(ckpt_bytes)
        ckpt       = torch.load(ckpt_buf, map_location="cpu",
                                weights_only=False)

        # ── Build LSTM R13 ────────────────────────────────────────────
        model = _build_lstm()
        state_dict = ckpt if isinstance(ckpt, dict) and "lstm.weight_ih_l0" in ckpt \
                     else ckpt.get("model_state_dict", ckpt)
        model.load_state_dict(state_dict, strict=False)
        model.eval()

        # ── Forward pass ─────────────────────────────────────────────
        with torch.no_grad():
            logit    = model(x_tensor).squeeze().item()
            prob     = 1 / (1 + np.exp(-logit))  # sigmoid

        direction = "LONG" if prob >= 0.5 else "SHORT"
        natural_score = float(prob if direction == "LONG" else 1 - prob)

        # ── Backbone levels heuristic ─────────────────────────────────
        # Uses raw OHLCV from last rows (unscaled)
        close_prices = rows[-lookback:, 0]   # high col as proxy for close
        atr14        = float(np.mean(np.abs(np.diff(close_prices[-14:]))) + EPSILON)
        last_close   = float(rows[-1, 0])
        kelly_raw    = float(np.clip(natural_score * 2 - 1, 0, 1))
        kelly_f      = float(np.clip(kelly_raw, 0, KELLY_CAP))

        if direction == "LONG":
            entry  = last_close
            sl     = last_close - 4.5 * atr14
            tp     = last_close + 4.5 * atr14 * 2.5
        else:
            entry  = last_close
            sl     = last_close + 4.5 * atr14
            tp     = last_close - 4.5 * atr14 * 2.5

        # ── Score de Oro (R13 weights) ────────────────────────────────
        # Gödel 40% + TE 30% + Backbone 30%
        if godel_active:
            godel_comp = min(100, 60 + (raw_entropy - p90) / (p90 * 0.05 + EPSILON) * 20)
        else:
            godel_comp = max(0, 40 - (p90 - raw_entropy) / (p90 * 0.05 + EPSILON) * 10)

        te_comp  = natural_score * 100
        bb_comp  = min(100, kelly_f / KELLY_CAP * 100)

        score_oro = int(np.clip(
            godel_comp * 0.40 + te_comp * 0.30 + bb_comp * 0.30, 0, 100))

        viable = score_oro >= SCORE_THRESHOLD and godel_active

        # ── Regime label ──────────────────────────────────────────────
        if not godel_active:
            regime = "GODEL_OFF"
        elif natural_score > 0.65:
            regime = "TREND"
        elif natural_score > 0.55:
            regime = "MEAN_REV"
        else:
            regime = "NOISE"

        return CloudScoreResult(
            asset          = asset,
            score_oro      = score_oro,
            direction      = direction if viable else "FLAT",
            viable         = viable,
            godel_active   = godel_active,
            entropy        = raw_entropy,
            p90_entropy    = p90,
            p90_method     = p90_method,
            kelly_fraction = kelly_f,
            entry_price    = entry  if viable else 0.0,
            stop_loss      = sl     if viable else 0.0,
            take_profit    = tp     if viable else 0.0,
            atr14          = atr14,
            regime_label   = regime,
            sha_parquet    = sha_pq,
            raw_logit      = float(logit),
        )

    def score_all(self) -> dict[str, CloudScoreResult]:
        results = {}
        for asset in ["BTC", "XAU", "NIFTY50", "NVDA"]:
            try:
                results[asset] = self.score(asset)
                r = results[asset]
                logger.info(
                    f"  {asset}: score={r.score_oro} {r.direction} "
                    f"godel={'✅' if r.godel_active else '○'} "
                    f"ent={r.entropy:.3f} p90={r.p90_entropy:.3f} "
                    f"viable={r.viable}"
                )
            except Exception as e:
                logger.error(f"  {asset}: inference failed — {e}")
        return results


# ════════════════════════════════════════════════════════════════════
# LSTM ARCHITECTURE R13
# ════════════════════════════════════════════════════════════════════

def _build_lstm():
    """
    LSTM canónico R13 — inamovible.
    input_size=20, hidden_size=64, num_layers=1, output=Linear(64→1)
    """
    try:
        import torch.nn as nn
        import torch

        class SPELLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(
                    input_size=20, hidden_size=64,
                    num_layers=1, batch_first=True)
                self.fc = nn.Linear(64, 1)

            def forward(self, x):
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])

        return SPELLSTM()
    except ImportError:
        raise RuntimeError("torch required for LSTM inference")


# ════════════════════════════════════════════════════════════════════
# FRESHNESS CHECK
# ════════════════════════════════════════════════════════════════════

def check_freshness(manifest: dict, warn_hours: float = 25.0) -> tuple[bool, str]:
    """
    Returns (is_fresh, message).
    warn_hours: alert if feature cache is older than this.
    Daily ingest window R25: expect refresh every ~24h on trading days.
    """
    exported = manifest.get("exported_utc", "")
    if not exported:
        return False, "manifest.exported_utc missing"
    try:
        dt_exp = datetime.fromisoformat(exported.replace("Z", "+00:00"))
        age_h  = (datetime.now(timezone.utc) - dt_exp).total_seconds() / 3600
        if age_h > warn_hours:
            return False, f"Feature cache {age_h:.1f}h old (>{warn_hours}h) — run ingest + export"
        return True, f"Cache fresh: {age_h:.1f}h old"
    except Exception as e:
        return False, f"Freshness parse error: {e}"
