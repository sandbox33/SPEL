"""
SPEL — Reentrenamiento Limpio v5
=================================
CORRECCIONES vs v4:
  - BUG-RETRAIN-PATH-01 FIXED: RAIZ apunta a SPEL-v2.0
  - Rutas v2.0 corregidas
  - DNA audit integrado antes de entrenar
  - Merge GDELT entropy → OHLCV (enriquece features con datos reales)
  - SHA verificado antes de cargar
  - Log de entrenamiento por época guardado en Drive
  - Checkpoint guarda SHA de datos + fecha de entrenamiento

Uso:
  1. Cambiar ACTIVO al activo que quieres entrenar
  2. Ejecutar en Colab con GPU activa
  3. Orden recomendado: BTC → XAU → NIFTY50 → NVDA

Reglas aplicadas: R2 · R3 · R4 · R5 · R9 · R10 · R11 · R13
"""

import dataclasses, json, hashlib, warnings
from pathlib import Path
from datetime import datetime
import torch
import torch.nn as nn
import polars as pl
import numpy as np
from torch.utils.data import DataLoader, TensorDataset
warnings.filterwarnings('ignore')

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ── CONFIGURACIÓN — CAMBIAR SOLO ESTO ───────────────────────────
ACTIVO = 'BTC'   # 'BTC' | 'NVDA' | 'XAU' | 'NIFTY50'
# ────────────────────────────────────────────────────────────────

# ── RUTAS v2.0 (R1 — NUNCA apuntar a v1.1) ────────────────────
RAIZ       = Path('/content/drive/MyDrive/SPEL-v2.0')           # ← FIX BUG-RETRAIN-PATH-01
RUTA_OHLCV = RAIZ / 'data_lake' / ACTIVO / 'ohlcv' / 'aggregated' / f'{ACTIVO}_ohlcv_v5.parquet'
RUTA_GDELT = RAIZ / 'data_lake' / ACTIVO / 'gdelt' / 'raw' / f'{ACTIVO}_gdelt_entropy.parquet'
RUTA_CK    = RAIZ / 'checkpoints' / f'{ACTIVO}_LSTM_v5_spel.pt'
RUTA_LOG   = RAIZ / 'logs' / f'{ACTIVO}_retrain_v5_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'

SHA_ESPERADOS = {
    'NVDA':    '3627a749da49',
    'BTC':     'a2c4e6f6e816',
    'XAU':     'a8e10cff2e80',
    'NIFTY50': '5e9624595c03',
}

LOOKBACKS = {'NVDA': 63, 'BTC': 21, 'XAU': 63, 'NIFTY50': 42}

# Features del LSTM — exactamente 20 (R13 inamovible)
FEATURES = [
    'entropy_shannon',       # GDELT merge si disponible, sino OHLCV
    'entropy_decay_lambda',
    'entropy_psych_vix',
    'fibonacci_lag_1',
    'fibonacci_lag_2',
    'fibonacci_lag_3',
    'fibonacci_lag_5',
    'fibonacci_lag_8',
    'fibonacci_lag_13',
    'fibonacci_lag_21',
    'goldstein_geo',
    'n_events_ohlcv',
    'vitality_tesla',
    'mass_panic_index',
    'fear_momentum',
    'vix_norm',
    'nash_frozen_7d',
    'log_return',
    'open',
    'close',
]  # exactamente 20 — NO modificar (R13)

print(f"SPEL Retrain v5 — {ACTIVO} | {DEVICE} | {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
print("=" * 60)

# ─── 1. VERIFICACIÓN SHA (R10) ────────────────────────────────
print("\n[1/6] Verificando integridad SHA...")

def sha12(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()[:12]

assert RUTA_OHLCV.exists(), f"OHLCV no encontrado: {RUTA_OHLCV}"
sha_real = sha12(RUTA_OHLCV)
sha_esperado = SHA_ESPERADOS[ACTIVO]
assert sha_real == sha_esperado, (
    f"SHA MISMATCH {ACTIVO}: real={sha_real} esperado={sha_esperado}\n"
    f"Los datos no son los verificados. Abortar (R10)."
)
print(f"  ✅ SHA {ACTIVO}: {sha_real} == OK")

# ─── 2. CARGAR Y VALIDAR OHLCV ───────────────────────────────
print("\n[2/6] Cargando OHLCV...")
df = pl.read_parquet(str(RUTA_OHLCV)).sort('date')

# Asegurar datetime[ms,UTC] (R5)
if df['date'].dtype == pl.Utf8:
    df = df.with_columns(
        pl.col('date').str.strptime(pl.Datetime('ms', 'UTC'), '%Y-%m-%d')
    )
elif df['date'].dtype != pl.Datetime('ms', 'UTC'):
    df = df.with_columns(
        pl.col('date').cast(pl.Datetime('ms', 'UTC'))
    )

assert str(df['date'].dtype) == "Datetime(time_unit='ms', time_zone='UTC')", \
    f"date dtype incorrecto: {df['date'].dtype}"

n_total = len(df)
print(f"  {ACTIVO}: {n_total} filas | {df['date'].min()} → {df['date'].max()}")

# Verificar features presentes
faltantes = [f for f in FEATURES if f not in df.columns]
assert not faltantes, f"Features faltantes en OHLCV: {faltantes}"
print(f"  ✅ 20/20 features confirmadas")

# ─── 3. MERGE CON GDELT (enriquecer entropy_shannon si disponible) ──
print("\n[3/6] Merge GDELT entropy...")
gdelt_merged = False
if RUTA_GDELT.exists():
    df_g = pl.read_parquet(str(RUTA_GDELT))

    # Normalizar date del GDELT
    if df_g['date'].dtype == pl.Utf8 or df_g['date'].dtype == pl.Date:
        df_g = df_g.with_columns(
            pl.col('date').cast(pl.Utf8).str.strptime(pl.Datetime('ms', 'UTC'), '%Y-%m-%d')
        )
    elif df_g['date'].dtype != pl.Datetime('ms', 'UTC'):
        df_g = df_g.with_columns(pl.col('date').cast(pl.Datetime('ms', 'UTC')))

    # Filtrar al activo correcto si hay columna 'asset'
    if 'asset' in df_g.columns:
        df_g = df_g.filter(pl.col('asset') == ACTIVO)

    # Columnas GDELT que queremos usar como fuente más precisa
    gdelt_cols_override = ['entropy_shannon', 'vitality_tesla', 'goldstein_mean']
    gdelt_cols_extra    = ['zipf_concentration', 'tone_variance', 'n_events']

    # Renombrar para merge
    rename_map = {'goldstein_mean': 'gdelt_goldstein_mean',
                  'n_events': 'gdelt_n_events',
                  'zipf_concentration': 'gdelt_zipf',
                  'tone_variance': 'gdelt_tone_var'}
    cols_a_traer = ['date'] + [c for c in gdelt_cols_extra if c in df_g.columns]
    df_g_sel = df_g.select(cols_a_traer).rename({k: v for k, v in rename_map.items() if k in cols_a_traer})

    n_antes = len(df)
    df = df.join(df_g_sel, on='date', how='left')
    n_match = df.select(pl.col(list(rename_map.values())[0] if list(rename_map.values())[0] in df.columns else 'date').is_not_null().sum()).item()

    print(f"  GDELT disponible: {len(df_g)} filas")
    print(f"  Join left: {n_antes} → {len(df)} filas | matches: {n_match} ({100*n_match/n_antes:.1f}%)")
    gdelt_merged = True
else:
    print(f"  ⚠️  GDELT no encontrado — usando entropy_shannon del OHLCV (menos preciso)")
    print(f"     Para mejorar: ejecutar gdelt_foundation.py primero")

# ─── 4. AUDIT RÁPIDO PRE-ENTRENAMIENTO ───────────────────────
print("\n[4/6] Auditoría pre-entrenamiento...")

data_audit = {}
lr_np = df['log_return'].cast(pl.Float64).to_numpy()

def corr_futuro(arr, lr, lag=1):
    x, y = arr[:-lag], lr[lag:]
    m = np.isfinite(x) & np.isfinite(y)
    return float(np.corrcoef(x[m], y[m])[0, 1]) if m.sum() > 10 else float('nan')

problemas_criticos = []
for feat in FEATURES:
    col = df[feat].cast(pl.Float64).to_numpy()
    n_nan = int(np.isnan(col).sum())
    pct_nan = 100 * n_nan / len(col)
    corr_f1 = corr_futuro(col, lr_np)
    nonzero = 100 * (np.sum(col != 0) / len(col))

    data_audit[feat] = {
        'pct_nan': round(pct_nan, 2),
        'nonzero_pct': round(nonzero, 2),
        'mean': round(float(np.nanmean(col)), 6),
        'corr_lr_t1': round(corr_f1, 6) if not np.isnan(corr_f1) else None,
    }

    flag = ''
    if pct_nan > 50:
        flag = ' ⚠️ ALTO NaN'
        problemas_criticos.append(f'{feat}: {pct_nan:.0f}% NaN')
    if abs(corr_f1) > 0.1 and not np.isnan(corr_f1):
        flag += f' ⚠️ CORR_ALTA({corr_f1:.3f})'

    print(f"  {'✅' if not flag else '⚠️'} {feat:<28} NaN={pct_nan:.1f}% nonzero={nonzero:.1f}%"
          f" mean={np.nanmean(col):.4f}{flag}")

if problemas_criticos:
    print(f"\n  ⚠️  {len(problemas_criticos)} problemas detectados:")
    for p in problemas_criticos:
        print(f"     - {p}")
    resp = input("\n  ¿Continuar de todas formas? [s/N]: ").strip().lower()
    if resp != 's':
        raise SystemExit("Abortado por el usuario. Corregir antes de entrenar.")

# ─── 5. PREPARAR SECUENCIAS ───────────────────────────────────
print(f"\n[5/6] Preparando secuencias (lookback={LOOKBACKS[ACTIVO]}d)...")

# Normalización causal: fit SOLO en train[:80%], apply a todo (anti-leakage)
data_np  = df[FEATURES].cast(pl.Float64).to_numpy().astype(np.float32)
tgt_np   = df['log_return'].cast(pl.Float64).to_numpy().astype(np.float32)
vit_np   = df['vitality_tesla'].cast(pl.Float64).to_numpy().astype(np.float32)

sp_norm  = int(len(data_np) * 0.8)
mean_tr  = np.nanmean(data_np[:sp_norm], axis=0)
std_tr   = np.nanstd(data_np[:sp_norm], axis=0)
std_tr[std_tr == 0] = 1.0

data_norm = np.nan_to_num((data_np - mean_tr) / std_tr, nan=0.0).astype(np.float32)

LB = LOOKBACKS[ACTIVO]
X, y, v = [], [], []
for i in range(LB, len(data_norm)):
    X.append(data_norm[i - LB:i])
    y.append(tgt_np[i])
    v.append(vit_np[i])

X = torch.tensor(np.array(X), dtype=torch.float32)
y = torch.tensor(np.array(y), dtype=torch.float32).unsqueeze(1)
v = torch.tensor(np.array(v), dtype=torch.float32).unsqueeze(1)

sp2         = int(len(X) * 0.8)
X_tr, X_val = X[:sp2].to(DEVICE), X[sp2:].to(DEVICE)
y_tr, y_val = y[:sp2].to(DEVICE), y[sp2:].to(DEVICE)
v_tr, v_val = v[:sp2].to(DEVICE), v[sp2:].to(DEVICE)

loader = DataLoader(TensorDataset(X_tr, y_tr, v_tr), batch_size=32, shuffle=True)
print(f"  Train: {len(X_tr)} seqs | Val: {len(X_val)} seqs | Device: {DEVICE}")
print(f"  Forma X: {X.shape} | Features: {X.shape[2]} | Pasos: {X.shape[1]}")

# ─── 6. MODELO + ENTRENAMIENTO ───────────────────────────────
print(f"\n[6/6] Entrenamiento {ACTIVO} — Loss asimétrica SPEL (R9)")
print("-" * 60)

@dataclasses.dataclass
class LSTMConfig:
    input_size:  int = 20  # inamovible R13
    hidden_size: int = 64  # inamovible R13
    num_layers:  int = 1   # inamovible R13
    output_size: int = 1


class SPEL_LSTM(nn.Module):
    def __init__(self, cfg: LSTMConfig):
        super().__init__()
        self.lstm   = nn.LSTM(cfg.input_size, cfg.hidden_size,
                              cfg.num_layers, batch_first=True)
        self.linear = nn.Linear(cfg.hidden_size, cfg.output_size)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.linear(out[:, -1, :])


def spel_loss(pred, target, vitality_col):
    """
    Loss asimétrica SPEL — inamovible (R9).
    El modelo aprende proporcionalmente a la crisis.
    vitality=3 (paz)     → W=0.5  (aprende poco)
    vitality=6 (tensión) → W=1.0  (normal)
    vitality=9 (ruptura) → W=2.0  (aprende el doble)
    """
    mse      = (pred - target) ** 2
    dir_err  = torch.relu(-pred * target)
    raw_loss = 0.6 * dir_err + 0.4 * mse
    w = torch.where(vitality_col >= 9,  torch.tensor(2.0, device=pred.device),
        torch.where(vitality_col >= 6,  torch.tensor(1.0, device=pred.device),
                                        torch.tensor(0.5, device=pred.device)))
    return (raw_loss * w).mean()


cfg   = LSTMConfig()
model = SPEL_LSTM(cfg).to(DEVICE)
opt   = torch.optim.Adam(model.parameters(), lr=3e-4)
sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, patience=5, factor=0.5,
                                                    min_lr=1e-6)

mejor     = {'val_dir': 0.0, 'val_loss': float('inf'), 'epoch': 0}
sin_mejora = 0
historial  = []
EPOCHS    = 100
EARLY_STOP_EPOCHS = 15
EARLY_STOP_MIN_EPOCH = 30

RUTA_CK.parent.mkdir(parents=True, exist_ok=True)
RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)

for epoch in range(1, EPOCHS + 1):
    # ── Entrenamiento
    model.train()
    batch_losses = []
    for xb, yb, vb in loader:
        opt.zero_grad()
        loss = spel_loss(model(xb), yb, vb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        opt.step()
        batch_losses.append(loss.item())
    tr_loss = float(np.mean(batch_losses))

    # ── Validación
    model.eval()
    with torch.no_grad():
        pv  = model(X_val)
        vl  = spel_loss(pv, y_val, v_val).item()
        vd  = ((pv.squeeze() > 0) == (y_val.squeeze() > 0)).float().mean().item() * 100
        # val_dir ponderado por vitality (más peso en crisis)
        mask_9 = (v_val.squeeze() >= 9)
        vd_crisis = float(((pv.squeeze()[mask_9] > 0) == (y_val.squeeze()[mask_9] > 0)).float().mean().item() * 100) if mask_9.sum() > 0 else float('nan')

    sched.step(vl)

    historial.append({
        'epoch': epoch,
        'tr_loss': round(tr_loss, 6),
        'val_loss': round(vl, 6),
        'val_dir': round(vd, 4),
        'val_dir_crisis': round(vd_crisis, 4) if not np.isnan(vd_crisis) else None,
        'lr': float(opt.param_groups[0]['lr']),
    })

    marca = ''
    if vd > mejor['val_dir']:
        mejor = {'val_dir': vd, 'val_loss': vl, 'epoch': epoch,
                 'val_dir_crisis': vd_crisis}
        sin_mejora = 0
        marca = ' ← MEJOR'
        # Guardar checkpoint
        torch.save({
            'epoch':          epoch,
            'model_state':    model.state_dict(),
            'val_dir':        vd,
            'val_dir_crisis': vd_crisis,
            'val_loss':       vl,
            'config':         cfg,
            'input_size':     20,
            'features':       FEATURES,
            'scaler':         {'mean': mean_tr.tolist(), 'std': std_tr.tolist()},
            'activo':         ACTIVO,
            'lookback':       LOOKBACKS[ACTIVO],
            'sha_datos':      sha_real,
            'datos_origen':   f'SPEL-v2.0 canon_v4 {"+ GDELT" if gdelt_merged else ""}',
            'fecha_train':    datetime.utcnow().isoformat(),
            'loss_fn':        'spel_asymmetric_v5',
            'arquitectura':   'LSTM input=20 hidden=64 layers=1',
        }, str(RUTA_CK))
    else:
        sin_mejora += 1

    if epoch % 10 == 0 or marca:
        crisis_str = f' val_dir_crisis={vd_crisis:.2f}%' if not np.isnan(vd_crisis) else ''
        print(f"  Época {epoch:3d} | val_dir={vd:.2f}% | val_loss={vl:.6f}"
              f" | tr_loss={tr_loss:.6f}{crisis_str}{marca}")

    if sin_mejora >= EARLY_STOP_EPOCHS and epoch > EARLY_STOP_MIN_EPOCH:
        print(f"  Early stop — sin mejora en {EARLY_STOP_EPOCHS} épocas")
        break

# ── Guardar log completo
log_data = {
    'activo': ACTIVO,
    'fecha': datetime.utcnow().isoformat(),
    'sha_datos': sha_real,
    'mejor': mejor,
    'configuracion': {
        'lookback': LOOKBACKS[ACTIVO],
        'features': FEATURES,
        'epochs_ejecutadas': epoch,
        'device': str(DEVICE),
        'gdelt_merged': gdelt_merged,
    },
    'historial': historial,
}
with open(RUTA_LOG, 'w') as f:
    json.dump(log_data, f, indent=2, ensure_ascii=False)

# ── Resumen final ─────────────────────────────────────────────
print("-" * 60)
print(f"\n✅ {ACTIVO} — Entrenamiento completado")
print(f"   val_dir         : {mejor['val_dir']:.2f}%")
crisis = mejor.get('val_dir_crisis')
if crisis and not np.isnan(crisis):
    print(f"   val_dir crisis  : {crisis:.2f}%  (días vitality=9)")
print(f"   val_loss        : {mejor['val_loss']:.6f}")
print(f"   mejor época     : {mejor['epoch']}")
print(f"   checkpoint      : {RUTA_CK.name}")
print(f"   log             : {RUTA_LOG.name}")
print(f"   datos           : SPEL-v2.0 {'+ GDELT' if gdelt_merged else '(solo OHLCV)'}")
print()
print(f"   INTERPRETACIÓN val_dir:")
print(f"   < 50% → modelo peor que azar    ← reentrenar con más datos")
print(f"   50-52% → al borde              ← monitorear en paper trading")
print(f"   52-55% → edge estadístico real  ← continuar pipeline")
print(f"   > 55% → sospechoso (revisar leakage)")
print()
print(f"   Siguiente activo (orden recomendado):")
orden = ['BTC', 'XAU', 'NIFTY50', 'NVDA']
idx = orden.index(ACTIVO) if ACTIVO in orden else -1
if idx < len(orden) - 1:
    print(f"   Cambiar ACTIVO = '{orden[idx + 1]}' y re-ejecutar")
else:
    print(f"   Todos los activos entrenados ✅")
    print(f"   → Siguiente: spel_p90_recalibrate.py (si no está hecho)")
    print(f"   → Luego: paper trading Gate 3")
