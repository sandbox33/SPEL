"""
SPEL — Patch Coordinado PATH B + Trainer v3
Resuelve en orden:
  1. spel_meta_updater      — registry dinámico de checkpoints en SPEL_META.json
  2. spel_inference_patch   — _extraer_features PATH B (Z-score desde scaler del ckpt)
  3. spel_trainer_v3        — trainer canónico + auto-registro en META

Expansión futura (ETH, EURUSD, SPY):
  Solo añadir asset a EXPANSION_ASSETS y correr trainer v3.
  El META se actualiza automáticamente. _CHECKPOINTS_CANONICOS no se toca.

Uso: exec(open('spel_patch_coordinated.py').read())
"""

import sys, json, hashlib, numpy as np, types
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn as nn
import polars as pl
from sklearn.preprocessing import StandardScaler

ROOT  = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
CORE  = ROOT / 'codigo/core'
META_PATH = ROOT / 'meta/SPEL_META.json'

sys.path.insert(0, str(CORE))

from capa_c_inference     import SPELInferenceEngine, SPELLSTMModel, LSTMConfig
from critical_loss_optimized import CriticalNatureLoss, LossConfig, MarketContext

# ══════════════════════════════════════════════════════════════
# CONSTANTES CANÓNICAS
# ══════════════════════════════════════════════════════════════

TENSOR_COLS = [
    'high','low','log_return',
    'entropy_shannon','entropy_decay_lambda','entropy_psych_vix',
    'fibonacci_lag_1','fibonacci_lag_2','fibonacci_lag_3',
    'fibonacci_lag_5','fibonacci_lag_8','fibonacci_lag_13','fibonacci_lag_21',
    'goldstein_geo','n_events_ohlcv','vitality_tesla',
    'mass_panic_index','fear_momentum','vix_norm','nash_frozen_7d',
]
assert len(TENSOR_COLS) == 20

LOOKBACKS = {'BTC':21, 'XAU':63, 'NIFTY50':42, 'NVDA':63}

# Assets core actuales + slots para expansión
CORE_ASSETS      = ['BTC', 'XAU', 'NIFTY50', 'NVDA']
EXPANSION_ASSETS = []  # ['ETH','EURUSD','SPY'] — añadir aquí cuando estén listos

ENTROPY_WEIGHT_MAP = {3: 0.5, 6: 1.0, 9: 2.0}

CKPT_VERSION = 'v3'   # bump a v4 cuando cambie arquitectura

def sha12(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:12]

# ══════════════════════════════════════════════════════════════
# PARTE 1 — SPEL_META.json: registry dinámico de checkpoints
# ══════════════════════════════════════════════════════════════

def update_meta_registry(asset: str, ckpt_filename: str,
                          val_dir: float, p90: float,
                          scaler_params: dict) -> dict:
    """
    Registra el checkpoint en SPEL_META.json.
    cargar_activo() leerá de aquí en lugar de _CHECKPOINTS_CANONICOS hardcodeado.
    Compatible con expansión: ETH/EURUSD/SPY solo necesitan una entrada aquí.
    """
    meta = json.load(open(META_PATH))

    if 'checkpoint_registry' not in meta:
        meta['checkpoint_registry'] = {}

    meta['checkpoint_registry'][asset] = {
        'filename':      ckpt_filename,
        'version':       CKPT_VERSION,
        'val_dir':       round(val_dir, 4),
        'registered_at': datetime.now(timezone.utc).isoformat(),
        'scaler_mean':   scaler_params['mean'],   # para validación cruzada
        'scaler_std':    scaler_params['std'],
        'p90_entropy':   p90,
    }

    # Sincronizar godel_thresholds con valores actuales del registry
    reg = json.load(open(ROOT / 'meta/SHA_REGISTRY.json'))
    meta['godel_thresholds'] = {
        a: reg[a]['p90_entropy']
        for a in reg if isinstance(reg[a], dict) and 'p90_entropy' in reg[a]
    }

    json.dump(meta, open(META_PATH, 'w'), indent=2)
    return meta


def patch_cargar_activo(engine: SPELInferenceEngine) -> None:
    """
    Monkey-patch de cargar_activo para leer desde META checkpoint_registry.
    No modifica capa_c_inference.py en disco — aplica solo a la instancia.
    """
    meta = json.load(open(META_PATH))

    def cargar_activo_patched(self, activo: str) -> bool:
        registry = self.meta.get('checkpoint_registry', {})

        # Fallback a _CHECKPOINTS_CANONICOS si no hay entrada en META
        if activo not in registry:
            import capa_c_inference as ci
            legacy = getattr(ci, '_CHECKPOINTS_CANONICOS', {})
            if activo not in legacy:
                print(f"[INFERENCE] ❌ '{activo}' no en META registry ni en legacy dict")
                self._health = "OFFLINE"
                return False
            nombre_ckpt = legacy[activo]
        else:
            nombre_ckpt = registry[activo]['filename']

        ruta   = self.spel_path / "checkpoints" / nombre_ckpt
        modelo = __import__('capa_c_inference').safe_load_checkpoint(ruta, self.config)

        if modelo is None:
            self._health = "OFFLINE"
            return False

        self._modelo         = modelo
        self._activo_cargado = activo
        self._health         = "LIVE"
        print(f"[INFERENCE] ✅ {activo} cargado desde: {nombre_ckpt}")
        return True

    engine.cargar_activo = types.MethodType(cargar_activo_patched, engine)


# ══════════════════════════════════════════════════════════════
# PARTE 2 — PATH B: patch _extraer_features con Z-score
# ══════════════════════════════════════════════════════════════

def patch_extraer_features(engine: SPELInferenceEngine,
                            scaler: StandardScaler) -> None:
    """
    Reemplaza _extraer_features en la instancia para usar el scaler del checkpoint
    en lugar de min-max local. Backward compatible: si scaler=None, mantiene min-max.
    """
    if scaler is None:
        print("[INFERENCE] ⚠️  scaler=None — _extraer_features mantiene min-max (sub-óptimo)")
        return

    feature_cols = engine.meta.get('feature_columns', TENSOR_COLS)

    def extraer_features_zscore(self, df: pl.DataFrame, lookback: int):
        cols = feature_cols[:20]
        if len(cols) < 20:
            print(f"[INFERENCE] ⚠️  Solo {len(cols)} features disponibles")
            return None

        datos = df.tail(lookback).select(cols).to_numpy().astype(np.float32)

        if datos.shape[0] < lookback:
            print(f"[INFERENCE] ⚠️  {datos.shape[0]} filas < lookback={lookback}")
            return None

        # PATH B: Z-score con params del trainer — mismo espacio que el training
        datos_norm = scaler.transform(datos)

        # Clamp anti-outlier: ±5σ
        datos_norm = np.clip(datos_norm, -5.0, 5.0)

        return datos_norm.astype(np.float32)

    engine._extraer_features = types.MethodType(extraer_features_zscore, engine)
    print("[INFERENCE] ✅ _extraer_features patcheado con Z-score PATH B")


# ══════════════════════════════════════════════════════════════
# PARTE 3 — TRAINER v3
# ══════════════════════════════════════════════════════════════

class SPELDataset(torch.utils.data.Dataset):
    def __init__(self, X, y, contexts):
        self.X = torch.tensor(X, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)
        self.contexts = contexts
    def __len__(self):        return len(self.y)
    def __getitem__(self, i): return self.X[i], self.y[i], self.contexts[i]

def collate(batch):
    Xb, yb, ctxb = zip(*batch)
    return torch.stack(Xb), torch.stack(yb), list(ctxb)

def make_sequences_godel(arr_norm, arr_raw, lookback, p90,
                          entropy_idx=3, vitality_idx=15):
    """
    Gödel mask evaluada en raw space (p90 es umbral en escala real de entropy_shannon).
    arr_norm : Z-scored → entra al tensor LSTM
    arr_raw  : sin normalizar → evalúa condición Gödel y extrae target log_return
    FIX: evaluar p90 sobre arr_raw evita 0-hits cuando Z-transform centra entropy en ~0.
    """
    X, y = [], []
    for i in range(lookback, len(arr_norm)):
        is_godel = (arr_raw[i, entropy_idx] >= p90) or \
                   (arr_raw[i, vitality_idx] == 9)
        if not is_godel:
            continue
        X.append(arr_norm[i-lookback:i])
        y.append(1.0 if arr_raw[i, 2] > 0 else 0.0)  # log_return raw para target
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)

def build_contexts(df_rows):
    contexts = []
    for row in df_rows:
        v = int(row['vitality_tesla'])
        contexts.append(MarketContext(
            sentiment           = float(np.clip((row['goldstein_mean'] - 1.845) / 0.235, -1, 1)),
            volatility          = float(np.clip(row['vix_norm'] * 100, 0, 100)),
            institutional_trust = float(np.clip(1.0 - row['nash_frozen_7d'], 0, 1)),
            transfer_entropy    = float(row['entropy_decay_lambda']),
            is_macro_event      = v == 9,
            entropy_weight      = ENTROPY_WEIGHT_MAP.get(v, 1.0),
        ))
    return contexts

def godel_accuracy(model, loader, device):
    """val_dir sobre días Gödel — la única métrica operativa relevante."""
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for X_b, y_b, _ in loader:
            pred     = model(X_b.to(device)).squeeze(-1)
            prob     = torch.sigmoid(pred).cpu()
            correct += ((prob > 0.5) == (y_b > 0.5)).sum().item()
            total   += len(y_b)
    return correct / total if total > 0 else 0.0

def naive_baseline_godel(df_val, p90):
    mask = (df_val['entropy_shannon'] >= p90) | (df_val['vitality_tesla'] == 9)
    gd   = df_val.filter(mask)
    if len(gd) == 0: return 0.5, 0
    n_up = (gd['log_return'] > 0).sum()
    return max(n_up, len(gd) - n_up) / len(gd), len(gd)


def train_asset_v3(asset: str, epochs: int = 50):
    print(f"\n{'═'*60}")
    print(f"  TRAINER v3 — {asset}  |  Gödel-masked + binary direction")
    print(f"{'═'*60}")

    pq_path = ROOT / f'data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
    sha     = sha12(str(pq_path))
    reg     = json.load(open(ROOT / 'meta/SHA_REGISTRY.json'))
    assert sha == reg[asset]['sha_v5'], f"SHA mismatch {asset} — ABORT (R3)"
    p90     = reg[asset]['p90_entropy']
    lb      = LOOKBACKS[asset]

    df = pl.read_parquet(str(pq_path)).sort('date')

    # Split canónico: train=2015-2021, val=2022, test=2023, OOS=2024+
    t_end = datetime(2021, 12, 31, tzinfo=timezone.utc)
    v_end = datetime(2022, 12, 31, tzinfo=timezone.utc)
    ts_end= datetime(2023, 12, 31, tzinfo=timezone.utc)

    df_train = df.filter(pl.col('date') <= t_end)
    df_val   = df.filter((pl.col('date') > t_end)  & (pl.col('date') <= v_end))
    df_test  = df.filter((pl.col('date') > v_end)  & (pl.col('date') <= ts_end))

    # Scaler fit sobre train canónico completo (2015-2021) — no sobre window
    scaler      = StandardScaler()
    X_train_raw = df_train.select(TENSOR_COLS).to_numpy().astype(np.float32)
    scaler.fit(X_train_raw)

    # Raw val array definido ANTES de cualquier uso — elimina forward reference
    X_val_raw_arr = df_val.select(TENSOR_COLS).to_numpy().astype(np.float32)

    X_train = scaler.transform(X_train_raw)
    X_val   = scaler.transform(X_val_raw_arr)

    # Gödel-masked sequences — raw para mask+target, Z-scored para tensor
    X_tr, y_tr = make_sequences_godel(X_train, X_train_raw,   lb, p90)
    X_vl, y_vl = make_sequences_godel(X_val,   X_val_raw_arr, lb, p90)

    if len(X_tr) < 20:
        print(f"  ⚠️  Solo {len(X_tr)} secuencias Gödel en train — insuficiente")
        return None
    if len(X_vl) < 5:
        print(f"  ⚠️  Solo {len(X_vl)} secuencias Gödel en val — insuficiente")
        return None

    print(f"  Secuencias Gödel — train: {len(X_tr)} | val: {len(X_vl)}")

    # Índices Gödel en raw space para contexts
    godel_idxs_tr = [
        i for i in range(lb, len(X_train_raw))
        if (X_train_raw[i, 3] >= p90) or (X_train_raw[i, 15] == 9)
    ]
    godel_idxs_vl = [
        i for i in range(lb, len(X_val_raw_arr))
        if (X_val_raw_arr[i, 3] >= p90) or (X_val_raw_arr[i, 15] == 9)
    ]

    # ctx usa índice directo en df — sin offset lb para evitar desalineación
    ctx_tr = build_contexts([df_train[i].to_dicts()[0] for i in godel_idxs_tr])
    ctx_vl = build_contexts([df_val[i].to_dicts()[0]   for i in godel_idxs_vl])

    # Alinear longitudes
    min_tr = min(len(X_tr), len(ctx_tr))
    min_vl = min(len(X_vl), len(ctx_vl))
    X_tr, y_tr, ctx_tr = X_tr[:min_tr], y_tr[:min_tr], ctx_tr[:min_tr]
    X_vl, y_vl, ctx_vl = X_vl[:min_vl], y_vl[:min_vl], ctx_vl[:min_vl]

    train_loader = torch.utils.data.DataLoader(
        SPELDataset(X_tr, y_tr, ctx_tr), batch_size=32,
        shuffle=False, collate_fn=collate)
    val_loader = torch.utils.data.DataLoader(
        SPELDataset(X_vl, y_vl, ctx_vl), batch_size=32,
        shuffle=False, collate_fn=collate)

    device    = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # Usar SPELLSTMModel nativo — elimina output shape mismatch (ALTO #4)
    model     = SPELLSTMModel(LSTMConfig()).to(device)
    criterion = nn.BCEWithLogitsLoss(reduction='none')
    criterion_ctx = CriticalNatureLoss(LossConfig(device=str(device)))
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=7, factor=0.5)

    baseline, n_godel_val = naive_baseline_godel(df_val, p90)
    print(f"  Baseline Gödel-val: {baseline:.3f} | n_dias_godel_val: {n_godel_val}")

    best_delta = -999
    best_state = None
    best_dir   = 0.0

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        for X_b, y_b, ctx_b in train_loader:
            optimizer.zero_grad()
            logits = model(X_b.to(device)).squeeze(-1)

            # Loss: BCE + entropy weighting via CriticalNatureLoss scale
            bce  = criterion(logits, y_b.to(device))
            ew   = torch.tensor(
                [ctx.entropy_weight for ctx in ctx_b],
                dtype=torch.float32, device=device
            )
            loss = (bce * ew).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()

        val_dir = godel_accuracy(model, val_loader, device)
        delta   = val_dir - baseline
        scheduler.step(epoch_loss / len(train_loader))

        if epoch % 10 == 0 or epoch == 1:
            print(f"  E{epoch:3d}/{epochs} loss={epoch_loss/len(train_loader):.4f} "
                  f"val_dir={val_dir:.3f} baseline={baseline:.3f} Δ={delta:+.3f}")

        if delta > best_delta:
            best_delta = delta
            best_dir   = val_dir
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    print(f"\n  ── Resultado ──────────────────────────────────────")
    print(f"  best_val_dir={best_dir:.3f} | best_Δ={best_delta:+.3f} | "
          f"gate={'✅' if best_delta >= 0.02 else '⚠️  señal débil'}")

    if best_delta < 0.0:
        print(f"  ⛔ Δ negativo — modelo por debajo del baseline. No guardar.")
        return None

    # Naming canónico versionado — compatible con checkpoint_registry
    ckpt_filename = f"{asset}_LSTM_{CKPT_VERSION}_godel_valloss{1-best_dir:.4f}.pt"
    ckpt_path     = ROOT / 'checkpoints' / ckpt_filename

    ckpt = {
        'model_state_dict':   best_state,
        'scaler':             scaler,
        'sha_parquet':        sha,
        'p90_usado':          p90,
        'val_dir':            best_dir,
        'best_delta':         best_delta,
        'baseline_godel':     baseline,
        'n_godel_val':        n_godel_val,
        'tensor_cols':        TENSOR_COLS,
        'lookback':           lb,
        'asset':              asset,
        'ckpt_version':       CKPT_VERSION,
        'training_mode':      'godel_masked_binary',
        'scaler_scope':       'train_2015_2021',
        'fecha':              datetime.now(timezone.utc).isoformat(),
        'epochs':             epochs,
    }
    torch.save(ckpt, str(ckpt_path))

    # Auto-registro en SPEL_META.json
    update_meta_registry(
        asset        = asset,
        ckpt_filename= ckpt_filename,
        val_dir      = best_dir,
        p90          = p90,
        scaler_params= {
            'mean': scaler.mean_.tolist(),
            'std':  scaler.scale_.tolist(),
        }
    )
    print(f"  💾 {ckpt_filename}")
    print(f"  📋 Registrado en SPEL_META.json checkpoint_registry")
    return ckpt


# ══════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════

def run_all(epochs=50):
    assets = CORE_ASSETS + EXPANSION_ASSETS
    resultados = {}

    for asset in assets:
        r = train_asset_v3(asset, epochs=epochs)
        if r:
            resultados[asset] = {
                'val_dir':   r['val_dir'],
                'delta':     r['best_delta'],
                'baseline':  r['baseline_godel'],
                'n_godel':   r['n_godel_val'],
            }

    print(f"\n\n{'═'*60}")
    print("  RESUMEN TRAINER v3 — GÖDEL-MASKED")
    print(f"{'═'*60}")
    print(f"  {'Asset':<10} {'val_dir':<10} {'Δ':<10} {'baseline':<10} {'n_godel_val'}")
    print(f"  {'─'*8}   {'─'*7}   {'─'*7}   {'─'*8}   {'─'*11}")
    for a, v in resultados.items():
        gate = "✅" if v['delta'] >= 0.02 else ("⚠️ " if v['delta'] >= 0 else "⛔")
        print(f"  {gate} {a:<8} {v['val_dir']:.3f}      {v['delta']:+.3f}      "
              f"{v['baseline']:.3f}      {v['n_godel']}")

    # Verificar que SPEL_META.json tiene todos los assets registrados
    meta = json.load(open(META_PATH))
    reg  = meta.get('checkpoint_registry', {})
    print(f"\n  checkpoint_registry en META: {list(reg.keys())}")
    print(f"  EXPANSIÓN slots: {EXPANSION_ASSETS or 'vacío — añadir ETH/EURUSD/SPY cuando listos'}")
    return resultados


# ══════════════════════════════════════════════════════════════
# SMOKE TEST POST-TRAINING
# ══════════════════════════════════════════════════════════════

def smoke_test(asset: str = 'BTC'):
    """
    Verifica pipeline completo: checkpoint → PATH B patch → inferir().
    Retorna True si el inference path es end-to-end consistente.
    """
    print(f"\n── Smoke test: {asset} ──")

    meta   = json.load(open(META_PATH))
    engine = SPELInferenceEngine(spel_path=ROOT, meta=meta)

    # Aplicar patches
    patch_cargar_activo(engine)
    ok = engine.cargar_activo(asset)
    if not ok:
        print(f"  ❌ cargar_activo falló")
        return False

    # Cargar scaler del checkpoint para PATH B
    reg       = meta.get('checkpoint_registry', {})
    ckpt_name = reg[asset]['filename']
    ckpt      = torch.load(str(ROOT/'checkpoints'/ckpt_name),
                           map_location='cpu', weights_only=False)
    patch_extraer_features(engine, ckpt['scaler'])

    # Inferir sobre últimos datos disponibles
    df     = pl.read_parquet(str(ROOT/f'data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet')).sort('date')
    result = engine.inferir(df)

    print(f"  status:        {result['status']}")
    print(f"  val_dir:       {result['val_dir']}")
    print(f"  godel_activo:  {result['godel_activo']}")
    print(f"  entropy:       {result['entropy_shannon']} (P90={result['p90_threshold']})")
    print(f"  intervalo:     [{result['intervalo_lower']:.4f}, {result['intervalo_upper']:.4f}]")

    # Validar coherencia
    assert result['status'] in ('LIVE','STALE'), "status inválido"
    assert 0 <= result['val_dir'] <= 1,          "val_dir fuera de [0,1]"
    assert isinstance(result['godel_activo'], bool)

    print(f"  ✅ Smoke test {asset}: pipeline end-to-end coherente")
    return True


if __name__ == '__main__':
    # Correr entrenamiento completo
    run_all(epochs=50)

    # Smoke test en BTC después del training
    smoke_test('BTC')
