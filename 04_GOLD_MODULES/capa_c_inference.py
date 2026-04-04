# ── SPEL · CAPA C: MOTOR DE INFERENCIA NEURONAL ─────────────────────────────
# Módulo: capa_c_inference.py
# Proyecto: Socio-Political Entropy Loss (SPEL) · Holmes OS V4.0
# Autor: Abraham Fuenmayor · v2.0 · S41 · 28-Mar-2026
#
# [LEY2-FIX-APPLIED] — BUG-LEY2-001 RESUELTO (S41)
# import torch ELIMINADO del nivel de módulo. Lazy import dentro de funciones.
# Protocolo OMEGA — R37 / Ley 2 inamovible.
#
# Regla 13 OBLIGATORIA: Arquitectura canónica inamovible:
#   input_size=20 · hidden_size=64 · num_layers=1 · capa linear (self.linear)
# Regla 16 OBLIGATORIA: LSTMConfig se define ANTES de cualquier torch.load().
# EF-21: NUNCA self.fc — siempre self.linear. NUNCA strict=False.
# EF-23: NUNCA importar gdelt_foundation.py ni critical_loss_optimized.py.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import numpy as np
import polars as pl

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

# TYPE_CHECKING: permite type hints de torch sin importarlo en runtime.
# En GH Actions (sin torch) esto es cero overhead.
if TYPE_CHECKING:
    import torch
    import torch.nn as nn

logger = logging.getLogger(__name__)

# ── CONSTANTES CANÓNICAS (R13 — inamovibles) ─────────────────────────────────
_INPUT_SIZE       = 20
_HIDDEN_SIZE      = 64
_NUM_LAYERS       = 1
_STALE_HORAS      = 2
_LOOKBACK_DEFAULT = 63

_CHECKPOINTS_CANONICOS: dict[str, str] = {
    "NVDA":    "NVDA_LSTM_v3_godel_valloss0.3857.pt",   # SHA v45 canónico
    "BTC":     "BTC_LSTM_v3c_F1_0.4994.pt",
    "XAU":     "XAU_LSTM_v3_godel_valloss0.4386.pt",
    "NIFTY50": "NIFTY50_LSTM_v3_godel_valloss0.3784.pt",
}

_LOOKBACKS_CANONICOS: dict[str, int] = {
    "NVDA": 63, "BTC": 21, "XAU": 63, "NIFTY50": 42,
}

_WIDTH_PENALTY: dict[str, float] = {
    "NVDA": 0.1, "BTC": 0.0, "XAU": 0.1, "NIFTY50": 0.1,
}

# P90 canónicos SHA v45 — fallback si META ausente (R28)
_P90_FALLBACKS: dict[str, float] = {
    "BTC": 2.002221, "XAU": 1.904465, "NIFTY50": 1.186823, "NVDA": 1.900615,
}


# ── CONFIGURACIÓN CANÓNICA (definida ANTES de cualquier torch.load — R16) ─────
@dataclass
class LSTMConfig:
    """Configuración de arquitectura canónica. NO modificar sin actualizar SHA_REGISTRY."""
    input_size:  int = _INPUT_SIZE
    hidden_size: int = _HIDDEN_SIZE
    num_layers:  int = _NUM_LAYERS
    output_size: int = 1


# ── LAZY TORCH FACTORY — construye clases nn.Module solo cuando torch disponible
def _build_spel_lstm_class():
    """
    Factory lazy: construye SPELLSTMModel solo cuando se llama por primera vez.
    GH Actions runner NUNCA toca esta función (no hay torch, no hay import).
    Solo Colab/Termux con torch instalado llegan aquí.
    """
    import torch                  # [R37] lazy import — dentro de función
    import torch.nn as nn         # [R37] lazy import

    class SPELLSTMModel(nn.Module):
        """
        Arquitectura LSTM canónica SPEL.
        EF-21: output = self.linear (NO self.fc). load_state_dict strict=True.
        """
        def __init__(self, config: LSTMConfig = LSTMConfig()):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=config.input_size,
                hidden_size=config.hidden_size,
                num_layers=config.num_layers,
                batch_first=True,
            )
            self.linear = nn.Linear(config.hidden_size, config.output_size)  # EF-21

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            out, _ = self.lstm(x)
            return self.linear(out[:, -1, :])

    return SPELLSTMModel


def _build_godel_head_class():
    """Factory lazy para cabeza de intervalo Gödel (Nivel 5-B)."""
    import torch.nn as nn  # [R37] lazy import

    class GodelIntervalHead(nn.Module):
        def __init__(self, hidden_size: int = _HIDDEN_SIZE):
            super().__init__()
            self.linear_lower = nn.Linear(hidden_size, 1)
            self.linear_upper = nn.Linear(hidden_size, 1)

        def forward(self, lstm_hidden: "torch.Tensor"):
            return self.linear_lower(lstm_hidden), self.linear_upper(lstm_hidden)

    return GodelIntervalHead


# ── CARGA SEGURA DE CHECKPOINT ────────────────────────────────────────────────
def safe_load_checkpoint(
    ruta_ckpt: Path,
    config: LSTMConfig = LSTMConfig(),
    dispositivo: str = "cpu",
) -> Optional[object]:
    """
    Carga checkpoint canónico. Lazy import de torch dentro de la función.
    Retorna None en cualquier fallo — nunca propaga excepción (R16).
    """
    if not ruta_ckpt.exists():
        logger.error("[INFERENCE] Checkpoint no encontrado: %s", ruta_ckpt)
        return None
    try:
        import torch  # [R37] lazy import

        SPELLSTMModel = _build_spel_lstm_class()
        modelo = SPELLSTMModel(config)
        estado = torch.load(str(ruta_ckpt), map_location=dispositivo, weights_only=False)

        if isinstance(estado, dict) and "model_state_dict" in estado:
            modelo.load_state_dict(estado["model_state_dict"], strict=True)
        elif isinstance(estado, dict) and "state_dict" in estado:
            modelo.load_state_dict(estado["state_dict"], strict=True)
        else:
            modelo.load_state_dict(estado, strict=True)

        modelo.eval()
        logger.info("[INFERENCE] Checkpoint cargado: %s", ruta_ckpt.name)
        return modelo
    except Exception as e:
        logger.error("[INFERENCE] Error cargando checkpoint %s: %s", ruta_ckpt.name, e)
        return None


# ── MOTOR DE INFERENCIA PRINCIPAL ─────────────────────────────────────────────
class SPELInferenceEngine:
    """
    Interfaz principal de inferencia SPEL. Consumible por Score Engine y Holmes.
    Torch se importa solo al llamar cargar_activo() — cero overhead en GH Actions.

    API:
        engine = SPELInferenceEngine(spel_path, meta)
        engine.cargar_activo("BTC")
        resultado = engine.inferir(df_parquet)
    """

    def __init__(self, spel_path: Path, meta: dict):
        self.spel_path            = Path(spel_path)
        self.meta                 = meta
        self.config               = LSTMConfig()
        self._modelo              = None
        self._activo_cargado: Optional[str] = None
        self._health: str         = "OFFLINE"

    def cargar_activo(self, activo: str) -> bool:
        """Carga checkpoint canónico. Retorna False si falla sin propagar."""
        if activo not in _CHECKPOINTS_CANONICOS:
            logger.error("[INFERENCE] Activo '%s' sin checkpoint registrado.", activo)
            self._health = "OFFLINE"
            return False

        nombre_ckpt = _CHECKPOINTS_CANONICOS[activo]
        ruta = self.spel_path / "checkpoints" / nombre_ckpt
        modelo = safe_load_checkpoint(ruta, self.config)

        if modelo is None:
            self._health = "OFFLINE"
            return False

        self._modelo         = modelo
        self._activo_cargado = activo
        self._health         = "LIVE"
        return True

    def inferir(self, df: pl.DataFrame) -> dict:
        """
        Inferencia sobre parquet canónico v4.
        Returns dict: val_dir, entropy_shannon, godel_activo, intervalo, status.
        """
        if self._modelo is None or self._activo_cargado is None:
            return self._resultado_offline("Motor no inicializado. Llamar cargar_activo() primero.")

        try:
            import torch  # [R37] lazy import

            activo   = self._activo_cargado
            lookback = _LOOKBACKS_CANONICOS.get(activo, _LOOKBACK_DEFAULT)
            p90      = self._obtener_p90(activo)

            status_final = self._verificar_frescura(df)

            features = self._extraer_features(df, lookback)
            if features is None:
                return self._resultado_offline("Features insuficientes en parquet.")

            x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)  # (1, T, F)

            with torch.no_grad():
                pred_raw = self._modelo(x).squeeze().item()
                val_dir  = float(torch.sigmoid(torch.tensor(pred_raw)).item())
                entropy  = self._obtener_ultima_entropia(df)
                lower, upper = self._inferir_intervalo(x, pred_raw)

            godel_activo = entropy >= p90

            return {
                "status":          status_final,
                "activo":          activo,
                "val_dir":         round(val_dir, 4),
                "entropy_shannon": round(entropy, 4),
                "p90_threshold":   round(p90, 4),
                "godel_activo":    godel_activo,
                "intervalo_lower": round(lower, 6),
                "intervalo_upper": round(upper, 6),
                "timestamp_utc":   datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error("[INFERENCE] Error en inferencia: %s", e)
            return self._resultado_offline(f"Error en inferencia: {e}")

    @property
    def health(self) -> str:
        return self._health

    # ── PRIVADOS ──────────────────────────────────────────────────────────────

    def _extraer_features(self, df: pl.DataFrame, lookback: int) -> Optional[np.ndarray]:
        cols_feature = self.meta.get("feature_columns", [])
        if not cols_feature:
            cols_feature = [
                c for c in df.columns
                if c not in ("date", "symbol")
                and df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
            ][:_INPUT_SIZE]

        if len(cols_feature) < _INPUT_SIZE:
            logger.warning("[INFERENCE] Solo %d features disponibles (req %d).",
                           len(cols_feature), _INPUT_SIZE)
            return None

        cols_feature = cols_feature[:_INPUT_SIZE]
        datos = df.tail(lookback).select(cols_feature).to_numpy().astype(np.float32)

        if datos.shape[0] < lookback:
            logger.warning("[INFERENCE] Parquet tiene %d filas (lookback=%d).",
                           datos.shape[0], lookback)
            return None

        mn  = datos.min(axis=0, keepdims=True)
        mx  = datos.max(axis=0, keepdims=True)
        rng = np.where((mx - mn) > 1e-9, mx - mn, 1.0)
        return (datos - mn) / rng

    def _obtener_ultima_entropia(self, df: pl.DataFrame) -> float:
        if "entropy_shannon" in df.columns:
            val = df["entropy_shannon"][-1]
            return float(val) if val is not None else 0.0
        if "close" in df.columns:
            retornos = df["close"].tail(21).to_numpy().astype(float)
            retornos = np.diff(np.log(retornos + 1e-9))
            hist, _  = np.histogram(retornos, bins=10, density=True)
            hist     = hist + 1e-9
            hist    /= hist.sum()
            return float(-np.sum(hist * np.log2(hist)))
        return 0.0

    def _obtener_p90(self, activo: str) -> float:
        thresholds = self.meta.get("godel_thresholds", {})
        return float(thresholds.get(activo, _P90_FALLBACKS.get(activo, 1.19)))

    def _inferir_intervalo(self, x: object, pred_raw: float):
        """Intervalo Gödel si la cabeza existe; heurística ±1.5σ si no."""
        try:
            import torch  # [R37] lazy import
            if hasattr(self._modelo, "linear_lower") and hasattr(self._modelo, "linear_upper"):
                with torch.no_grad():
                    hidden, _ = self._modelo.lstm(x)
                    h_last    = hidden[:, -1, :]
                    lower = self._modelo.linear_lower(h_last).squeeze().item()
                    upper = self._modelo.linear_upper(h_last).squeeze().item()
                return lower, upper
        except Exception:
            pass
        sigma = abs(pred_raw) * 0.3 + 1e-6
        return pred_raw - 1.5 * sigma, pred_raw + 1.5 * sigma

    def _verificar_frescura(self, df: pl.DataFrame) -> str:
        try:
            ultima  = df.sort("date")["date"][-1]
            if hasattr(ultima, "replace"):
                ultima = ultima.replace(tzinfo=None)
            delta_h = (datetime.utcnow() - ultima).total_seconds() / 3600
            status  = "STALE" if delta_h > _STALE_HORAS else "LIVE"
            self._health = status
            return status
        except Exception:
            self._health = "STALE"
            return "STALE"

    @staticmethod
    def _resultado_offline(razon: str) -> dict:
        return {
            "status":          "OFFLINE",
            "razon_offline":   razon,
            "val_dir":         0.5,
            "entropy_shannon": 0.0,
            "p90_threshold":   1.19,
            "godel_activo":    False,
            "intervalo_lower": 0.0,
            "intervalo_upper": 0.0,
            "timestamp_utc":   datetime.utcnow().isoformat(),
        }


# ── DIAGNÓSTICO D-2 (R14/R15 · standalone) ───────────────────────────────────
def diagnostico_activaciones_godel(df: pl.DataFrame, p90: float) -> dict:
    """
    Verifica ≥5% activaciones Gödel en el dataset de test.
    Ejecutar antes de entrenar cabezas de intervalo (R15).
    """
    if "entropy_shannon" not in df.columns:
        return {"ok": False, "pct_activaciones": 0.0,
                "mensaje": "entropy_shannon no encontrada en parquet."}

    entropias     = df["entropy_shannon"].to_numpy().astype(float)
    n_activaciones = int((entropias >= p90).sum())
    pct           = n_activaciones / max(len(entropias), 1) * 100
    ok            = pct >= 5.0

    return {
        "ok":               ok,
        "pct_activaciones": round(pct, 2),
        "n_activaciones":   n_activaciones,
        "n_total":          len(entropias),
        "p90_usado":        p90,
        "mensaje": (
            f"✅ {pct:.1f}% activaciones Gödel — mínimo 5% cumplido." if ok
            else f"❌ {pct:.1f}% activaciones Gödel — mínimo 5% NO cumplido."
        ),
    }
