# ── SPEL · SCORE ENGINE — MOTOR DE PUNTUACIÓN GOLD SCORE ─────────────────────
# Módulo: spel_score_engine.py
# Proyecto: SPEL 3.0 · Holmes OS V4.0
# Autor: Abraham Fuenmayor · v2.0 · S41 · 28-Mar-2026
#
# [LEY2-FIX-SCORE] — BUG-LEY2-002 RESUELTO (S41)
# import torch ELIMINADO del nivel de módulo. Lazy import dentro de funciones.
# Protocolo OMEGA — R37 / Ley 2 inamovible.
#
# R13 — Gold Score canónico:
#   NATIVE_FUTURES/CRYPTO : Gödel(40%) + TE(30%) + Backbone(30%)
#   SYNTHETIC_INDEX       : Gödel(55%) + TE(45%) + Backbone(0%) + Vol(0%)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import numpy as np
import polars as pl

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── PESOS GOLD SCORE (R13 — inamovibles) ─────────────────────────────────────
_WEIGHTS: dict[str, dict[str, float]] = {
    "NATIVE_FUTURES": {"godel": 0.40, "transfer_entropy": 0.30, "backbone": 0.30, "vol": 1.0},
    "CRYPTO":         {"godel": 0.40, "transfer_entropy": 0.30, "backbone": 0.30, "vol": 1.0},
    "SYNTHETIC_INDEX":{"godel": 0.55, "transfer_entropy": 0.45, "backbone": 0.00, "vol": 0.0},
}
_DEFAULT_ASSET_TYPE = "NATIVE_FUTURES"

_ASSET_TYPE_MAP: dict[str, str] = {
    "BTC":    "CRYPTO",
    "ETH":    "CRYPTO",
    "XAU":    "NATIVE_FUTURES",
    "NVDA":   "NATIVE_FUTURES",
    "NIFTY50":"SYNTHETIC_INDEX",
    "SPY":    "SYNTHETIC_INDEX",
}


@dataclass
class GoldScoreResult:
    """Resultado completo del motor de puntuación. 512 bytes target para SignalPacket."""
    asset:          str
    gold_score:     float          # 0.0 – 1.0
    regime:         str            # GODEL_ON | CRISIS_CONTRA | NORMAL
    godel_component:float
    te_component:   float
    backbone_component: float
    vol_factor:     float
    godel_active:   bool
    entropy_raw:    float
    p90_threshold:  float
    kelly_fraction: float
    direction:      str            # LONG | SHORT | HOLD
    confidence:     float          # P(direction)
    asset_type:     str
    timestamp_utc:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GoldScoreEngine:
    """
    Motor Gold Score R13. Consumible por Holmes patrol() y por J2 del pipeline.
    Importa capa_c_inference y torch de forma lazy — cero overhead en GH Actions.
    """

    def __init__(self, spel_root: Path, meta: dict, sha_registry: dict):
        self.root         = Path(spel_root)
        self.meta         = meta
        self.sha_registry = sha_registry
        self._inference_engines: dict[str, object] = {}

    def compute(self, asset: str, df: pl.DataFrame) -> GoldScoreResult:
        """
        Calcula Gold Score completo para el activo dado sobre su parquet canónico.
        Retorna GoldScoreResult con régimen y kelly fraction.
        """
        asset_type = _ASSET_TYPE_MAP.get(asset, _DEFAULT_ASSET_TYPE)
        weights    = _WEIGHTS[asset_type]
        p90        = self._get_p90(asset)

        # ── COMPONENTE GÖDEL (inferencia LSTM) ───────────────────────────────
        inference_result = self._run_inference(asset, df)
        godel_active     = inference_result.get("godel_activo", False)
        entropy_raw      = inference_result.get("entropy_shannon", 0.0)
        val_dir          = inference_result.get("val_dir", 0.5)
        inf_status       = inference_result.get("status", "OFFLINE")

        godel_score = float(godel_active) * val_dir if godel_active else 0.0

        # ── COMPONENTE TRANSFER ENTROPY ────────────────────────────────────
        te_score = self._compute_transfer_entropy(df, asset)

        # ── COMPONENTE BACKBONE (tendencia 252d) ─────────────────────────
        backbone_score = self._compute_backbone(df) if weights["backbone"] > 0 else 0.0

        # ── VOLATILITY FACTOR (multiplicativo — excluido en sintéticos) ──
        vol_factor = self._compute_vol_factor(df, asset_type)

        # ── GOLD SCORE FINAL (R13) ────────────────────────────────────────
        raw_score = (
            weights["godel"] * godel_score
            + weights["transfer_entropy"] * te_score
            + weights["backbone"] * backbone_score
        ) * vol_factor

        gold_score = float(np.clip(raw_score, 0.0, 1.0))

        # ── RÉGIMEN (R28) ─────────────────────────────────────────────────
        regime = self._classify_regime(godel_active, entropy_raw, p90, df)

        # ── KELLY FRACTION ────────────────────────────────────────────────
        kelly = self._kelly_fraction(gold_score, regime)

        # ── DIRECCIÓN ────────────────────────────────────────────────────
        direction, confidence = self._compute_direction(val_dir, gold_score, regime)

        return GoldScoreResult(
            asset=asset, gold_score=round(gold_score, 4),
            regime=regime, godel_component=round(godel_score, 4),
            te_component=round(te_score, 4), backbone_component=round(backbone_score, 4),
            vol_factor=round(vol_factor, 4), godel_active=godel_active,
            entropy_raw=round(entropy_raw, 4), p90_threshold=round(p90, 4),
            kelly_fraction=round(kelly, 4), direction=direction,
            confidence=round(confidence, 4), asset_type=asset_type,
        )

    # ── COMPONENTES INTERNOS ──────────────────────────────────────────────────

    def _run_inference(self, asset: str, df: pl.DataFrame) -> dict:
        """
        Lazy-load SPELInferenceEngine. En GH Actions sin torch → retorna OFFLINE dict.
        """
        try:
            # [R37] lazy import — capa_c_inference solo se carga si torch existe
            import importlib.util
            if importlib.util.find_spec("torch") is None:
                return {"status": "OFFLINE", "godel_activo": False,
                        "entropy_shannon": 0.0, "val_dir": 0.5}

            if asset not in self._inference_engines:
                # Import lazy de capa_c_inference
                from capa_c_inference import SPELInferenceEngine  # [R37] lazy
                engine = SPELInferenceEngine(self.root, self.meta)
                loaded = engine.cargar_activo(asset)
                if not loaded:
                    return {"status": "OFFLINE", "godel_activo": False,
                            "entropy_shannon": 0.0, "val_dir": 0.5}
                self._inference_engines[asset] = engine

            engine = self._inference_engines[asset]
            return engine.inferir(df)

        except Exception as e:
            logger.error("[SCORE] Inference failed for %s: %s", asset, e)
            return {"status": "OFFLINE", "godel_activo": False,
                    "entropy_shannon": 0.0, "val_dir": 0.5}

    def _compute_transfer_entropy(self, df: pl.DataFrame, asset: str) -> float:
        """
        Transfer Entropy estimada sobre retornos log vs rezago 1.
        Proxy rápido en stdlib/numpy — sin torch.
        """
        try:
            if "close" not in df.columns:
                return 0.0
            closes = df["close"].tail(63).to_numpy().astype(float)
            if len(closes) < 10:
                return 0.0
            ret = np.diff(np.log(closes + 1e-9))
            # TE approx: I(X_t ; X_{t-1}) via mutual info discreta
            bins = 5
            x, y = ret[1:], ret[:-1]
            h_xy, _, _ = np.histogram2d(x, y, bins=bins)
            h_x,  _    = np.histogram(x, bins=bins)
            h_y,  _    = np.histogram(y, bins=bins)
            h_xy = h_xy / (h_xy.sum() + 1e-12)
            h_x  = h_x  / (h_x.sum()  + 1e-12)
            h_y  = h_y  / (h_y.sum()  + 1e-12)
            mi   = 0.0
            for i in range(bins):
                for j in range(bins):
                    if h_xy[i, j] > 1e-12:
                        mi += h_xy[i, j] * np.log2(h_xy[i, j] / (h_x[i] * h_y[j] + 1e-12))
            return float(np.clip(mi / (np.log2(bins) + 1e-9), 0.0, 1.0))
        except Exception as e:
            logger.warning("[SCORE] TE computation failed: %s", e)
            return 0.0

    def _compute_backbone(self, df: pl.DataFrame) -> float:
        """Tendencia backbone: EMA_20 vs EMA_63 normalizada a 0-1."""
        try:
            if "close" not in df.columns or len(df) < 63:
                return 0.5
            closes  = df["close"].to_numpy().astype(float)
            ema20   = self._ema(closes, 20)
            ema63   = self._ema(closes, 63)
            delta   = (ema20[-1] - ema63[-1]) / (abs(ema63[-1]) + 1e-9)
            return float(np.clip(0.5 + delta * 5.0, 0.0, 1.0))
        except Exception:
            return 0.5

    def _compute_vol_factor(self, df: pl.DataFrame, asset_type: str) -> float:
        """Ley 2 Gold Score: volatility excluida en SYNTHETIC_INDEX."""
        if asset_type == "SYNTHETIC_INDEX":
            return 1.0
        try:
            if "close" not in df.columns or len(df) < 21:
                return 1.0
            closes = df["close"].tail(21).to_numpy().astype(float)
            vol    = np.std(np.diff(np.log(closes + 1e-9)))
            # Normalizar: vol baja → factor alto (≤1), vol alta → factor bajo
            return float(np.clip(1.0 - vol * 10.0, 0.2, 1.0))
        except Exception:
            return 1.0

    def _classify_regime(self, godel_active: bool, entropy: float,
                         p90: float, df: pl.DataFrame) -> str:
        """R28: tres regímenes de mercado."""
        if not godel_active:
            return "NORMAL"
        # CRISIS_CONTRA: entropy spike post-pánico (entropy > 2*p90)
        if entropy > 2.0 * p90:
            return "CRISIS_CONTRA"
        return "GODEL_ON"

    def _kelly_fraction(self, gold_score: float, regime: str) -> float:
        """Kelly fraction según régimen. NORMAL → 0 (inacción disciplinada)."""
        if regime == "NORMAL":
            return 0.0
        elif regime == "GODEL_ON":
            # Kelly pleno: f = gold_score (bounded 0.05–0.25)
            return float(np.clip(gold_score * 0.25, 0.05, 0.25))
        elif regime == "CRISIS_CONTRA":
            # Kelly reducido: contrarian con exposición media
            return float(np.clip(gold_score * 0.10, 0.02, 0.12))
        return 0.0

    def _compute_direction(self, val_dir: float, gold_score: float,
                           regime: str) -> tuple[str, float]:
        if regime == "NORMAL":
            return "HOLD", 0.5
        if regime == "CRISIS_CONTRA":
            # Invertir dirección de la señal LSTM
            return ("SHORT" if val_dir > 0.5 else "LONG"), 1.0 - val_dir
        if val_dir > 0.55:
            return "LONG", val_dir
        elif val_dir < 0.45:
            return "SHORT", 1.0 - val_dir
        return "HOLD", 0.5

    def _get_p90(self, asset: str) -> float:
        reg = self.sha_registry.get(asset, {})
        return float(reg.get("p90_entropy", _P90_FALLBACKS.get(asset, 1.19)))

    @staticmethod
    def _ema(arr: np.ndarray, span: int) -> np.ndarray:
        alpha  = 2.0 / (span + 1)
        result = np.empty_like(arr)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
        return result


# ── FALLBACKS P90 (también usados por score engine standalone) ────────────────
_P90_FALLBACKS: dict[str, float] = {
    "BTC": 2.002221, "XAU": 1.904465, "NIFTY50": 1.186823, "NVDA": 1.900615,
}
