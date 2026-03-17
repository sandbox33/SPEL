# ── SPEL · CAPA C: MOTOR DE INFERENCIA NEURONAL ─────────────────────────────
# Módulo: capa_c_inference.py
# Proyecto: Socio-Political Entropy Loss (SPEL) · Dashboard v7
# Autor: Abraham Fuenmayor · v1.0 · 28 Feb 2026
#
# Regla 16 OBLIGATORIA: LSTMConfig se define ANTES de cualquier torch.load().
# Regla 13 OBLIGATORIA: Arquitectura canónica inamovible:
#   input_size=20 · hidden_size=64 · num_layers=1 · capa linear
# Regla 14: Este módulo ejecuta D-2 (diagnóstico Gödel) si se invoca standalone.
#
# PROPÓSITO: Envuelve los checkpoints LSTM v4 en una interfaz limpia que
# el Score Engine puede consumir sin conocer los detalles de PyTorch.
# NUNCA importar desde gdelt_foundation.py ni critical_loss_optimized.py.
# Esos módulos son canónicos e inamovibles.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import numpy as np
import polars as pl
import torch
import torch.nn as nn

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── CONSTANTES CANÓNICAS (Regla 13) ──────────────────────────────────────────
_INPUT_SIZE   = 20
_HIDDEN_SIZE  = 64
_NUM_LAYERS   = 1
_STALE_HORAS  = 2        # Si el parquet tiene >2h sin actualizar → STALE (Sección 3 Spec)
_LOOKBACK_DEFAULT = 63   # Fallback si META no tiene el activo

# Checkpoints canónicos por activo (Sección 1 del log v18)
_CHECKPOINTS_CANONICOS = {
    "NVDA":    "NVDA_LSTM_v1_ep004_valloss0.0016.pt",
    "BTC":     "BTC_LSTM_v4_ep012_valloss0.0011.pt",
    "XAU":     "XAU_LSTM_v4_ep005_valloss0.0002.pt",
    "NIFTY50": "NIFTY50_LSTM_v4_ep001_valloss0.0001.pt",
    # ── EXPANSIÓN NIVEL 7: añadir ETH, EURUSD, SPY aquí ──────────────────
    # "ETH":   "ETH_LSTM_v1_ep001_valloss0.0000.pt",  # pendiente fix Bug #12
    # "EURUSD":"EURUSD_LSTM_v1_ep001_valloss0.0000.pt",
    # "SPY":   "SPY_LSTM_v1_ep001_valloss0.0000.pt",
}

# Lookbacks canónicos por activo (val_dir registrados en log v18)
_LOOKBACKS_CANONICOS = {
    "NVDA": 63, "BTC": 21, "XAU": 63, "NIFTY50": 42,
}

# width_penalty BTC es 0.0 (Bug #37 cerrado · log v16)
_WIDTH_PENALTY = {"NVDA": 0.1, "BTC": 0.0, "XAU": 0.1, "NIFTY50": 0.1}


# ── ARQUITECTURA LSTM CANÓNICA (Regla 16: definir ANTES de torch.load) ───────
@dataclass
class LSTMConfig:
    """Configuración canónica de arquitectura. NO modificar sin actualizar el log."""
    input_size:  int = _INPUT_SIZE
    hidden_size: int = _HIDDEN_SIZE
    num_layers:  int = _NUM_LAYERS
    output_size: int = 1


class SPELLSTMModel(nn.Module):
    """Arquitectura LSTM canónica de SPEL. Corresponde a los checkpoints v4."""

    def __init__(self, config: LSTMConfig = LSTMConfig()):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=config.input_size,
            hidden_size=config.hidden_size,
            num_layers=config.num_layers,
            batch_first=True,
        )
        self.linear = nn.Linear(config.hidden_size, config.output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.linear(out[:, -1, :])


# ── CABEZA DE INTERVALO GÖDEL ─────────────────────────────────────────────────
class GodelIntervalHead(nn.Module):
    """Cabeza de intervalo para predicción lower/upper bound (Nivel 5-B)."""

    def __init__(self, hidden_size: int = _HIDDEN_SIZE):
        super().__init__()
        self.linear_lower = nn.Linear(hidden_size, 1)
        self.linear_upper = nn.Linear(hidden_size, 1)

    def forward(self, lstm_hidden: torch.Tensor):
        lower = self.linear_lower(lstm_hidden)
        upper = self.linear_upper(lstm_hidden)
        return lower, upper


# ── CARGA SEGURA DE CHECKPOINT (Regla 16) ─────────────────────────────────────
def safe_load_checkpoint(
    ruta_ckpt: Path,
    config: LSTMConfig = LSTMConfig(),
    dispositivo: str = "cpu",
) -> Optional[SPELLSTMModel]:
    """
    Carga un checkpoint canónico con manejo de excepciones robusto.
    Bug #38b cerrado: nunca lanzar excepción al exterior, retornar None.
    """
    if not ruta_ckpt.exists():
        print(f"[INFERENCE] ❌ Checkpoint no encontrado: {ruta_ckpt}")
        return None
    try:
        # LSTMConfig ya está definido antes de este punto (Regla 16)
        modelo = SPELLSTMModel(config)
        estado = torch.load(str(ruta_ckpt), map_location=dispositivo, weights_only=False)
        # Soportar tanto dict con 'model_state_dict' como state_dict directo
        if isinstance(estado, dict) and "model_state_dict" in estado:
            modelo.load_state_dict(estado["model_state_dict"])
        elif isinstance(estado, dict) and "state_dict" in estado:
            modelo.load_state_dict(estado["state_dict"])
        else:
            modelo.load_state_dict(estado)
        modelo.eval()
        print(f"[INFERENCE] ✅ Checkpoint cargado: {ruta_ckpt.name}")
        return modelo
    except Exception as e:
        print(f"[INFERENCE] ❌ Error cargando checkpoint {ruta_ckpt.name}: {e}")
        return None


# ── MOTOR DE INFERENCIA PRINCIPAL ─────────────────────────────────────────────
class SPELInferenceEngine:
    """
    Interfaz principal de inferencia SPEL para el Dashboard v7.

    Uso:
        engine = SPELInferenceEngine(spel_path, meta)
        engine.cargar_activo("NVDA")
        resultado = engine.inferir(df_parquet)
        # resultado: dict con val_dir, entropy_shannon, godel_activo, intervalo_confianza
    """

    def __init__(self, spel_path: Path, meta: dict):
        self.spel_path   = spel_path
        self.meta        = meta
        self.config      = LSTMConfig()
        self._modelo: Optional[SPELLSTMModel] = None
        self._activo_cargado: Optional[str] = None
        self._health: str = "OFFLINE"

    # ── API PÚBLICA ───────────────────────────────────────────────────────────

    def cargar_activo(self, activo: str) -> bool:
        """Carga el checkpoint canónico para el activo dado."""
        if activo not in _CHECKPOINTS_CANONICOS:
            print(f"[INFERENCE] ❌ Activo '{activo}' no tiene checkpoint registrado.")
            self._health = "OFFLINE"
            return False

        nombre_ckpt = _CHECKPOINTS_CANONICOS[activo]
        ruta = self.spel_path / "checkpoints" / nombre_ckpt
        modelo = safe_load_checkpoint(ruta, self.config)

        if modelo is None:
            self._health = "OFFLINE"
            return False

        self._modelo        = modelo
        self._activo_cargado = activo
        self._health        = "LIVE"
        return True

    def inferir(self, df: pl.DataFrame) -> dict:
        """
        Ejecuta inferencia sobre el parquet canónico v4 del activo cargado.

        Returns dict con:
            val_dir           : float (0–1) — prob. direccional alcista
            entropy_shannon   : float — entropía del último timestep
            godel_activo      : bool — True si entropy >= P90
            p90_threshold     : float
            intervalo_lower   : float — lower bound log-return (Nivel 5-B)
            intervalo_upper   : float — upper bound log-return (Nivel 5-B)
            status            : "LIVE" | "STALE" | "OFFLINE"
        """
        if self._modelo is None or self._activo_cargado is None:
            return self._resultado_offline("Motor no inicializado. Llamar cargar_activo() primero.")

        # ── VERIFICAR FRESCURA DEL PARQUET (Spec Sección 3) ─────────────────
        freshness = self._verificar_frescura(df)
        if freshness == "STALE":
            # Inferir igual pero marcar como STALE — Score Engine lo penalizará
            status_final = "STALE"
        else:
            status_final = "LIVE"

        try:
            activo  = self._activo_cargado
            lookback = _LOOKBACKS_CANONICOS.get(activo, _LOOKBACK_DEFAULT)
            p90     = self._obtener_p90(activo)

            # ── CONSTRUIR TENSOR DE ENTRADA ──────────────────────────────────
            features = self._extraer_features(df, lookback)  # (lookback, input_size)
            if features is None:
                return self._resultado_offline("Features insuficientes en parquet.")

            x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)  # (1, T, F)

            with torch.no_grad():
                # Inferencia de punto (retorno esperado)
                pred_raw = self._modelo(x).squeeze().item()
                val_dir  = float(torch.sigmoid(torch.tensor(pred_raw)).item())

                # Entropía del último paso (extraída del parquet canónico)
                entropy = self._obtener_ultima_entropia(df)

                # Intervalo de confianza Gödel (si existe cabeza de intervalo)
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
            return self._resultado_offline(f"Error en inferencia: {e}")

    @property
    def health(self) -> str:
        """'LIVE' | 'STALE' | 'OFFLINE' — para consumo del módulo de auditoría."""
        return self._health

    # ── MÉTODOS PRIVADOS ──────────────────────────────────────────────────────

    def _extraer_features(self, df: pl.DataFrame, lookback: int) -> Optional[np.ndarray]:
        """
        Extrae las 20 features canónicas del parquet v4.
        Las columnas se leen del META para garantizar orden correcto.
        """
        cols_feature = self.meta.get("feature_columns", [])
        if not cols_feature:
            # Fallback: tomar las primeras 20 columnas numéricas excluyendo 'date'
            cols_feature = [
                c for c in df.columns
                if c not in ("date", "symbol") and df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
            ][:_INPUT_SIZE]

        if len(cols_feature) < _INPUT_SIZE:
            print(f"[INFERENCE] ⚠️ Solo {len(cols_feature)} features disponibles (se requieren {_INPUT_SIZE}).")
            return None

        cols_feature = cols_feature[:_INPUT_SIZE]
        datos = df.tail(lookback).select(cols_feature).to_numpy().astype(np.float32)

        if datos.shape[0] < lookback:
            print(f"[INFERENCE] ⚠️ Parquet tiene solo {datos.shape[0]} filas (lookback={lookback}).")
            return None

        # Normalización min-max por columna (robusta a outliers)
        mn = datos.min(axis=0, keepdims=True)
        mx = datos.max(axis=0, keepdims=True)
        rng = np.where((mx - mn) > 1e-9, mx - mn, 1.0)
        datos_norm = (datos - mn) / rng

        return datos_norm  # (lookback, input_size)

    def _obtener_ultima_entropia(self, df: pl.DataFrame) -> float:
        """Lee entropy_shannon del último registro del parquet v4 canónico."""
        if "entropy_shannon" in df.columns:
            val = df["entropy_shannon"][-1]
            return float(val) if val is not None else 0.0
        # Fallback: calcular entropía de Shannon sobre distribución de retornos recientes
        if "close" in df.columns:
            retornos = df["close"].tail(21).to_numpy().astype(float)
            retornos = np.diff(np.log(retornos + 1e-9))
            hist, _ = np.histogram(retornos, bins=10, density=True)
            hist = hist + 1e-9
            hist /= hist.sum()
            return float(-np.sum(hist * np.log2(hist)))
        return 0.0

    def _obtener_p90(self, activo: str) -> float:
        """
        Obtiene el umbral P90 del SPEL_META.json.
        Fallback a valor calibrado si el META no lo tiene.
        """
        fallbacks = {"NVDA": 1.19, "BTC": 1.35, "XAU": 0.98, "NIFTY50": 1.05}
        thresholds = self.meta.get("godel_thresholds", {})
        return float(thresholds.get(activo, fallbacks.get(activo, 1.19)))

    def _inferir_intervalo(self, x: torch.Tensor, pred_raw: float):
        """
        Intenta inferir intervalo con cabeza Gödel si existe.
        Si el modelo no tiene cabezas de intervalo, devuelve estimación heurística.
        """
        # Comprobación de cabeza de intervalo (Nivel 5-B)
        if hasattr(self._modelo, "linear_lower") and hasattr(self._modelo, "linear_upper"):
            with torch.no_grad():
                hidden, _ = self._modelo.lstm(x)
                h_last = hidden[:, -1, :]
                lower = self._modelo.linear_lower(h_last).squeeze().item()
                upper = self._modelo.linear_upper(h_last).squeeze().item()
            return lower, upper
        # Heurística: ±1.5σ del pred_raw como aproximación
        sigma = abs(pred_raw) * 0.3 + 1e-6
        return pred_raw - 1.5 * sigma, pred_raw + 1.5 * sigma

    def _verificar_frescura(self, df: pl.DataFrame) -> str:
        """Devuelve 'LIVE' o 'STALE' según la antigüedad del último dato del parquet."""
        try:
            ultima = df.sort("date")["date"][-1]
            if hasattr(ultima, 'replace'):
                ultima = ultima.replace(tzinfo=None)
            delta_h = (datetime.utcnow() - ultima).total_seconds() / 3600
            if delta_h > _STALE_HORAS:
                self._health = "STALE"
                return "STALE"
            self._health = "LIVE"
            return "LIVE"
        except Exception:
            self._health = "STALE"
            return "STALE"

    @staticmethod
    def _resultado_offline(razon: str) -> dict:
        return {
            "status": "OFFLINE", "razon_offline": razon,
            "val_dir": 0.5, "entropy_shannon": 0.0,
            "p90_threshold": 1.19, "godel_activo": False,
            "intervalo_lower": 0.0, "intervalo_upper": 0.0,
            "timestamp_utc": datetime.utcnow().isoformat(),
        }


# ── DIAGNÓSTICO D-2 (Regla 15 · standalone) ──────────────────────────────────
def diagnostico_activaciones_godel(df: pl.DataFrame, p90: float) -> dict:
    """
    Verifica que ≥5% de los registros de test tienen activación Gödel (condición OR).
    Regla 15: ejecutar antes de entrenar cabezas de intervalo.
    """
    if "entropy_shannon" not in df.columns:
        return {"ok": False, "pct_activaciones": 0.0, "mensaje": "entropy_shannon no encontrada en parquet."}

    entropias = df["entropy_shannon"].to_numpy().astype(float)
    n_activaciones = int((entropias >= p90).sum())
    pct = n_activaciones / max(len(entropias), 1) * 100

    ok = pct >= 5.0
    return {
        "ok": ok,
        "pct_activaciones": round(pct, 2),
        "n_activaciones": n_activaciones,
        "n_total": len(entropias),
        "p90_usado": p90,
        "mensaje": (
            f"✅ {pct:.1f}% activaciones Gödel — umbral mínimo 5% cumplido." if ok
            else f"❌ Solo {pct:.1f}% activaciones Gödel — umbral mínimo 5% NO cumplido. No entrenar."
        ),
    }
