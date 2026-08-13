#!/usr/bin/env python3
"""
benchmark_training_cpu.py — Paso 4 (auditoría de ingesta): medir tiempo real
de un ciclo de entrenamiento en CPU, para decidir si entra en el margen
gratuito de GitHub Actions (2,000 min/mes, repo privado, sin GPU).

QUÉ ES REAL ACÁ (no aproximado):
  - SPELLSTMModel / LSTMConfig  → capa_c_inference.py, arquitectura exacta
    (input_size=20, hidden_size=64, num_layers=1) — la misma que carga
    los checkpoints canónicos.
  - CriticalNatureLoss / create_synthetic_contexts → critical_loss_optimized.py
    (EF-23 inmutable — se IMPORTA, no se toca ni se copia).
  - El loop de entrenamiento (zero_grad → forward → BCEWithLogitsLoss ×
    entropy_weight → backward → clip_grad_norm → step) replica exactamente
    train_asset_v3() en spel_patch_coordinated.py.
  - batch_size=32, Adam lr=5e-4 — mismos valores que el trainer real.

QUÉ ES ESTIMADO Y POR QUÉ (no tengo los parquets reales):
  - n_secuencias de entrenamiento por activo: NO puedo contarlas exactamente
    sin los parquets reales (train_asset_v3 las filtra a solo ventanas
    Gödel-activas). Uso el rango de activación objetivo que ustedes mismos
    documentaron en spel_auditoria_total.py (P90_TARGET_ACTIVATION = 30-48%)
    sobre ~1,250 días hábiles del período train 2015-2021, con el LOOKBACK
    canónico de cada activo. Ver ASSUMPTIONS abajo — cambiarlas si el número
    real difiere.
  - Los propios tensores de entrada son ruido aleatorio de la FORMA correcta
    (batch, lookback, 20) — el costo de forward/backward no depende de si
    los números son precios reales o ruido, solo de la forma del tensor.
  - NO incluye: lectura de parquet, feature engineering, join GDELT — eso es
    I/O, típicamente mucho más barato que el descenso de gradiente para este
    tamaño de dataset, pero no lo mido acá. Decilo si querés que lo agregue.

Uso: python3 benchmark_training_cpu.py
"""

import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capa_c_inference import LSTMConfig, _build_spel_lstm_class, _LOOKBACKS_CANONICOS
from critical_loss_optimized import create_synthetic_contexts

# ── ASSUMPTIONS — cambiar acá si los números reales difieren ────────────────
TRAIN_TRADING_DAYS   = 1250   # ~2015-2021, aproximando 5/7 de los días calendario
GODEL_ACTIVATION_MID = 0.39   # punto medio del rango documentado (30%-48%)
BATCH_SIZE           = 32
EPOCHS_FULL           = 50    # el trainer real usa epochs=50 por default
EPOCHS_TO_TIME        = 3     # medimos pocas épocas reales y extrapolamos —
                               # el costo por época es ~constante (mismo n
                               # de batches, misma arquitectura cada vez)
GH_ACTIONS_FREE_MIN_MONTH = 2000  # repo privado, confirmado en auditoría

ACTIVE_ASSETS = ["NVDA", "BTC", "XAU", "NIFTY50"]  # canónicos con checkpoint real
# EURUSD queda afuera del benchmark de ENTRENAMIENTO LSTM — no tiene
# checkpoint canónico en capa_c_inference.py (es señal Gödel+TE, no backbone
# propio, según spel_score_engine.py). No lo incluyo para no inflar el número.


def n_sequences_for(asset: str) -> int:
    """Estimación razonada, no inventada — ver ASSUMPTIONS arriba."""
    lookback = _LOOKBACKS_CANONICOS.get(asset, 63)
    usable_days = max(TRAIN_TRADING_DAYS - lookback, 0)
    return max(int(usable_days * GODEL_ACTIVATION_MID), 20)


def benchmark_asset(asset: str) -> dict:
    lookback = _LOOKBACKS_CANONICOS.get(asset, 63)
    n_seq = n_sequences_for(asset)
    n_batches = max(n_seq // BATCH_SIZE, 1)

    device = torch.device("cpu")
    SPELLSTMModel = _build_spel_lstm_class()
    model = SPELLSTMModel(LSTMConfig()).to(device)
    criterion = nn.BCEWithLogitsLoss(reduction="none")
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-4)

    # Datos sintéticos de la FORMA real — no precios reales, no hace falta
    # para medir costo de cómputo.
    X = torch.randn(n_seq, lookback, LSTMConfig().input_size)
    y = torch.randint(0, 2, (n_seq,)).float()
    contexts_all = create_synthetic_contexts(n_seq, scenario="normal")

    t0 = time.perf_counter()
    model.train()
    for _epoch in range(EPOCHS_TO_TIME):
        for b in range(n_batches):
            lo, hi = b * BATCH_SIZE, min((b + 1) * BATCH_SIZE, n_seq)
            X_b, y_b = X[lo:hi], y[lo:hi]
            ctx_b = contexts_all[lo:hi]

            optimizer.zero_grad()
            logits = model(X_b).squeeze(-1)
            bce = criterion(logits, y_b)
            ew = torch.tensor([c.entropy_weight for c in ctx_b], dtype=torch.float32)
            loss = (bce * ew).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    elapsed_measured = time.perf_counter() - t0

    sec_per_epoch = elapsed_measured / EPOCHS_TO_TIME
    sec_full_50   = sec_per_epoch * EPOCHS_FULL

    return {
        "asset": asset,
        "lookback": lookback,
        "n_sequences_est": n_seq,
        "n_batches": n_batches,
        "sec_per_epoch": sec_per_epoch,
        "sec_full_50_epochs": sec_full_50,
    }


def main():
    print("═" * 70)
    print("  Benchmark real — ciclo de entrenamiento LSTM en CPU")
    print(f"  torch {torch.__version__} | device: cpu | threads: {torch.get_num_threads()}")
    print("═" * 70)

    results = []
    for asset in ACTIVE_ASSETS:
        r = benchmark_asset(asset)
        results.append(r)
        print(f"\n  {asset}  (lookback={r['lookback']}d, "
              f"~{r['n_sequences_est']} secuencias Gödel-activas estimadas)")
        print(f"    {r['n_batches']} batches/época × {EPOCHS_TO_TIME} épocas medidas "
              f"→ {r['sec_per_epoch']:.3f}s/época")
        print(f"    Extrapolado a {EPOCHS_FULL} épocas: {r['sec_full_50_epochs']:.1f}s "
              f"({r['sec_full_50_epochs']/60:.2f} min)")

    total_min = sum(r["sec_full_50_epochs"] for r in results) / 60
    print("\n" + "═" * 70)
    print(f"  TOTAL — reentrenar los {len(ACTIVE_ASSETS)} activos canónicos "
          f"({EPOCHS_FULL} épocas c/u): {total_min:.2f} minutos")
    print(f"  Presupuesto gratis GitHub Actions (repo privado): "
          f"{GH_ACTIONS_FREE_MIN_MONTH} min/mes")
    ciclos_por_mes = GH_ACTIONS_FREE_MIN_MONTH / total_min if total_min > 0 else float("inf")
    print(f"  → Con este costo, entrarían ~{ciclos_por_mes:.0f} ciclos completos "
          f"de reentrenamiento por mes dentro del margen gratis")
    print("═" * 70)
    print("\n  ⚠️  Esto mide SOLO cómputo (forward+backward+step), sin I/O real")
    print("     de parquet ni feature engineering. Si querés el número con I/O")
    print("     incluido, hace falta correrlo contra datos reales en Colab/GH.")


if __name__ == "__main__":
    main()
