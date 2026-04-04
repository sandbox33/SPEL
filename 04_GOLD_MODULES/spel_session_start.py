"""
SPEL — Session Starter v1.0
Pegar esta celda AL INICIO de cada sesión Colab.
Monta Drive, verifica SHAs, carga todos los módulos, reporta estado.

Uso:
    exec(open('/content/drive/MyDrive/ORDEN/SPEL 3.0/scripts/spel_session_start.py').read())
"""

# ── 1. MONTAR DRIVE ───────────────────────────────────────────
from google.colab import drive
drive.mount('/content/drive', force_remount=False)

import sys, os, json, hashlib, types, numpy as np
from datetime import datetime, timezone
from pathlib import Path

# ── 2. RUTAS CANÓNICAS ───────────────────────────────────────
ROOT      = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
CORE      = ROOT / 'codigo/core'
SCRIPTS   = ROOT / 'scripts'
META_PATH = ROOT / 'meta/SPEL_META.json'
SHA_PATH  = ROOT / 'meta/SHA_REGISTRY.json'
CKPT_DIR  = ROOT / 'checkpoints'
DATA_LAKE = ROOT / 'data_lake'
LOGS_DIR  = ROOT / 'logs'

sys.path.insert(0, str(CORE))
sys.path.insert(0, str(SCRIPTS))

ASSETS = ['BTC', 'XAU', 'NIFTY50', 'NVDA']

def sha12(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:12]

# ── 3. CARGAR META Y REGISTRY ────────────────────────────────
reg  = json.load(open(SHA_PATH))
meta = json.load(open(META_PATH))

# ── 4. VERIFICAR SHAs ─────────────────────────────────────────
print("══ SPEL Session Start ══════════════════════════════")
print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("════════════════════════════════════════════════════\n")

sha_ok = True
for asset in ASSETS:
    pq      = DATA_LAKE / f'{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
    actual  = sha12(str(pq))
    expected= reg[asset]['sha_v5']
    match   = actual == expected
    sha_ok  = sha_ok and match
    print(f"  {'✅' if match else '❌'} {asset}: {actual} | hasta: {reg[asset].get('ingest_s24_date','?')[:10]}")

print()
if not sha_ok:
    print("  ❌ SHA mismatch — correr spel_ingest_incremental.py antes de continuar")
else:
    print("  ✅ Integridad de datos verificada")

# ── 5. CARGAR MÓDULOS CORE ────────────────────────────────────
print("\n── Módulos core ──────────────────────────────────")
_modules = {}
for mod_name in [
    'spel_math_engine', 'spel_backbone_engine', 'spel_trading_router',
    'capa_c_inference', 'critical_loss_optimized', 'godel_bound',
    'spel_modules', 'spel_logger',
]:
    try:
        _modules[mod_name] = __import__(mod_name)
        print(f"  ✅ {mod_name}")
    except Exception as e:
        print(f"  ❌ {mod_name}: {e}")

# Aliases de conveniencia
MathEngine     = _modules.get('spel_math_engine')
Backbone       = _modules.get('spel_backbone_engine')
Router         = _modules.get('spel_trading_router')
Inference      = _modules.get('capa_c_inference')
GodelBound     = _modules.get('godel_bound')

# ── 6. ESTADO DE CHECKPOINTS ──────────────────────────────────
print("\n── Checkpoints ───────────────────────────────────")
ckpt_registry = meta.get('checkpoint_registry', {})
for asset in ASSETS:
    if asset in ckpt_registry:
        r   = ckpt_registry[asset]
        pth = CKPT_DIR / r['filename']
        exists = '✅' if pth.exists() else '❌ NO ENCONTRADO'
        print(f"  {exists} {asset}: {r['filename']}")
        print(f"       val={r['val_dir']:.4f} | metric={r.get('metric','val_dir')} | v={r['version']}")
    else:
        print(f"  ❌ {asset}: sin registro en META")

# ── 7. CARGAR INFERENCE ENGINE CON PATCHES ────────────────────
print("\n── Inference engine ──────────────────────────────")

def _patch_cargar_activo(engine):
    """Patch dinámico: cargar_activo lee de META en lugar de dict hardcodeado."""
    def cargar_activo_patched(self, activo):
        registry = self.meta.get('checkpoint_registry', {})
        if activo not in registry:
            import capa_c_inference as ci
            legacy = getattr(ci, '_CHECKPOINTS_CANONICOS', {})
            if activo not in legacy:
                print(f"[INFERENCE] ❌ '{activo}' no registrado")
                self._health = "OFFLINE"; return False
            nombre = legacy[activo]
        else:
            nombre = registry[activo]['filename']
        ruta   = self.spel_path / "checkpoints" / nombre
        from capa_c_inference import safe_load_checkpoint, LSTMConfig
        modelo = safe_load_checkpoint(ruta, LSTMConfig())
        if modelo is None:
            self._health = "OFFLINE"; return False
        self._modelo = modelo
        self._activo_cargado = activo
        self._health = "LIVE"
        print(f"[INFERENCE] ✅ {activo}: {nombre}")
        return True
    engine.cargar_activo = types.MethodType(cargar_activo_patched, engine)

def _patch_extraer_features(engine, scaler):
    """Patch PATH B: Z-score desde scaler del checkpoint."""
    import numpy as np
    COLS = meta.get('feature_columns', [])
    def extraer_features_zscore(self, df, lookback):
        datos = df.tail(lookback).select(COLS[:20]).to_numpy().astype(np.float32)
        if datos.shape[0] < lookback: return None
        return np.clip(scaler.transform(datos), -5.0, 5.0).astype(np.float32)
    engine._extraer_features = types.MethodType(extraer_features_zscore, engine)

import torch, polars as pl
from capa_c_inference import SPELInferenceEngine

ENGINES = {}
for asset in ASSETS:
    engine = SPELInferenceEngine(spel_path=ROOT, meta=meta)
    _patch_cargar_activo(engine)
    ok = engine.cargar_activo(asset)
    if ok and asset in ckpt_registry:
        ckpt_path = CKPT_DIR / ckpt_registry[asset]['filename']
        ckpt      = torch.load(str(ckpt_path), map_location='cpu', weights_only=False)
        _patch_extraer_features(engine, ckpt['scaler'])
        ENGINES[asset] = engine

# ── 8. FUNCIÓN PRINCIPAL: SCORE COMPLETO ──────────────────────

def get_signal(asset: str, verbose: bool = True) -> dict:
    """
    Pipeline completo: parquet → inference → backbone → router → DecisionTrading

    Returns DecisionTrading con:
      modo, score_oro, godel_activo, kiereccion,
      kelly_fraccion, viable, razon
    """
    if asset not in ENGINES:
        return {'error': f'{asset} no tiene engine cargado'}

    engine = ENGINES[asset]
    df     = pl.read_parquet(
        str(DATA_LAKE / f'{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet')
    ).sort('date')

    # Capa 1: Inference
    lstm_out = engine.inferir(df)

    # Capa 2: Backbone (Hurst + Bayesian + Kelly)
    from spel_backbone_engine import SPELBackbone
    backbone = SPELBackbone(kelly_fraction=0.25, tp_rr_ratio=2.5)
    math_eng = _modules['spel_math_engine']

    # Score de Oro simplificado (hasta que MathEngine tenga LazyFrames frescos)
    entropy   = lstm_out.get('entropy_shannon', 1.0)
    p90       = lstm_out.get('p90_threshold', 1.19)
    val_dir   = lstm_out.get('val_dir', 0.5)
    godel_on  = lstm_out.get('godel_activo', False)

    # Dirección: val_dir > 0.5 = LONG, < 0.5 = SHORT
    # BTC: usar momentum_63d si está disponible
    if asset == 'BTC':
        mom = df['log_return'].rolling_mean(window_size=63).tail(1)[0]
        direccion = 'LONG' if (mom is not None and mom > 0) else 'SHORT'
    else:
        direccion = 'LONG' if val_dir > 0.5 else 'SHORT'

    score_resultado = {
        'score':       int(val_dir * 100),
        'direccion':   direccion,
        'fakeout':     False,
        'godel_activo': godel_on,
    }

    # Capa 3: Router
    from spel_trading_router import SPELTradingRouter
    router   = SPELTradingRouter()
    decision = router.route(
        activo          = asset,
        score_resultado = score_resultado,
        lstm_output     = lstm_out,
        natural_score   = float(val_dir),
        capital         = 50.0,
    )

    if verbose:
        print(f"\n── Señal {asset} ──────────────────────────────")
        print(f"  status:      {lstm_out.get('status')}")
        print(f"  godel:       {godel_on} (entropy={entropy:.4f} P90={p90:.4f})")
        print(f"  val_dir:     {val_dir:.4f}")
        print(f"  direccion:   {direccion}")
        print(f"  modo:        {decision.modo}")
        print(f"  viable:      {decision.viable}")
        print(f"  kelly:       {decision.kelly_fraccion:.4f}")
        for r in decision.razon:
            print(f"  > {r}")

    return decision


# ── 9. UTILIDADES DE SESIÓN ───────────────────────────────────

def reload_scripts():
    """Recarga scripts desde Drive sin reiniciar el kernel."""
    for script in SCRIPTS.glob('*.py'):
        if script.stem in sys.modules:
            del sys.modules[script.stem]
    print("✅ Scripts recargados desde Drive")

def save_session_log(notes: str = ""):
    """Guarda un log de la sesión actual en logs/."""
    LOGS_DIR.mkdir(exist_ok=True)
    log = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sha_registry': {a: reg[a]['sha_v5'] for a in ASSETS},
        'checkpoints':  {a: ckpt_registry.get(a,{}).get('filename') for a in ASSETS},
        'notes':        notes,
    }
    fname = LOGS_DIR / f"session_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    json.dump(log, open(fname,'w'), indent=2)
    print(f"✅ Log guardado: {fname.name}")

def show_map():
    """Muestra el mapa de módulos y rutas del sistema."""
    print("""
══ MAPA SPEL v2.0 ════════════════════════════════════════════

DATOS
  data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet
  data_lake/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet
  meta/SHA_REGISTRY.json     ← SHAs canónicas post-ingest S24
  meta/SPEL_META.json        ← feature_columns, P90s, checkpoint_registry
  meta/godel_thresholds_v2.json

MÓDULOS CORE (codigo/core/)
  spel_math_engine.py        ← Transfer Entropy, Hurst, Gödel signals
  spel_backbone_engine.py    ← Bayesian filter, Kelly, niveles estructurales
  spel_trading_router.py     ← DecisionTrading: modo, dirección, kelly
  capa_c_inference.py        ← SPELInferenceEngine, SPELLSTMModel
  critical_loss_optimized.py ← CriticalNatureLoss (NO TOCAR - R6)
  godel_bound.py             ← Gödel OR dinámico (NO TOCAR - R6)
  gdelt_foundation.py        ← GDELT base (NO TOCAR - R6)

SCRIPTS (scripts/)
  spel_session_start.py      ← ESTE ARCHIVO — arranque de sesión
  spel_preflight_s24.py      ← 13 checks de integridad
  spel_ingest_incremental.py ← OHLCV + GDELT incremental
  spel_trainer_btc_optionC.py← Trainer BTC regime-conditional
  spel_patch_coordinated.py  ← Trainer v3 core (XAU/NIFTY50/NVDA) + patches
  spel_audit_pipeline.py     ← Auditoría sistémica de discrepancias
  spel_trainer_audit.py      ← Auditoría del trainer (BUG-TENSOR-DOC)

CHECKPOINTS (checkpoints/)
  BTC_LSTM_v3c_F1_0.4821.pt  ← v3c regime-conditional ΔF1=+0.134
  XAU_LSTM_v3_godel_*.pt     ← v3 Gödel-masked Δ=+0.035
  NIFTY50_LSTM_v3_godel_*.pt ← v3 Gödel-masked Δ=+0.107
  NVDA_LSTM_v3_godel_*.pt    ← v3 Gödel-masked Δ=+0.061

PIPELINE DE SEÑAL (esta sesión)
  parquet → SPELInferenceEngine.inferir()
          → SPELBackbone.evaluate()
          → SPELTradingRouter.route()
          → DecisionTrading {modo, direccion, kelly, viable}

DASHBOARD (próximo)
  ojo_de_dios_v23.py → v24
  Panel: score por activo | Gödel activo | modo | dirección

SHA POST-INGEST S24
  BTC:     2f9fc2276d68  (4073 filas → 2026-03-16)
  XAU:     23c6b3d1ea0e  (2744 filas → 2026-03-16)
  NIFTY50: 9945fc117921  (2743 filas → 2026-03-13)
  NVDA:    73667850184b  (2743 filas → 2026-03-13)

REGLAS ABSOLUTAS
  R3:  SHA verificada antes de cualquier entrenamiento
  R6:  gdelt_foundation.py, critical_loss_optimized.py — NO TOCAR
  R8:  Gödel usa OR, nunca AND
  R13: LSTM input=20 inamovible
  R23: Diagnosticar antes de fixear. Un fix a la vez.

BUGS ACTIVOS
  GDELT-2026: gap llenado con ingest incremental ✅
  BUG-TENSOR-DOC: cerrado — high/low son las 2 features faltantes ✅
  SHA-YML: spel_github_sync.yml tiene SHAs viejas — actualizar
══════════════════════════════════════════════════════════════
""")


# ── 10. RESUMEN FINAL ─────────────────────────────────────────
print("\n── Funciones disponibles ─────────────────────────")
print("  get_signal('BTC')      → señal completa con DecisionTrading")
print("  get_signal('XAU')      → ídem")
print("  show_map()             → mapa completo de módulos y rutas")
print("  save_session_log(msg)  → guarda log de sesión en logs/")
print("  reload_scripts()       → recarga scripts desde Drive")
print("  run_preflight()        → 13 checks de integridad (si está cargado)")
print("\n  ENGINES disponibles:", list(ENGINES.keys()))
print("\n════════════════════════════════════════════════════")
print(f"  Sistema {'✅ LIVE' if sha_ok else '⚠️  STALE'} — listo para operar")
print("════════════════════════════════════════════════════\n")
