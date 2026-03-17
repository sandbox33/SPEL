# SPEL — S24: De Drive limpio a Forex Intraday en tiempo real
*Plan de acción concreto · 11 Mar 2026 · Post-S23*

> **Regla de esta sesión:** Cada paso tiene una acción exacta y un output verificable.
> No avanzar sin confirmar el output del paso anterior.
> Si algo no está en este plan, no se hace hoy.

---

## DIAGNÓSTICO HONESTO: DÓNDE ESTAMOS HOY

```
LO QUE FUNCIONA (no tocar):
  ✅ 4 parquets core v5.1 limpios con SHA verificados
  ✅ SHA_REGISTRY.json reconstruido y correcto
  ✅ GDELT 2015-2025 para NVDA, BTC, XAU, NIFTY50
  ✅ 14 módulos importando sin errores
  ✅ Auditor corregido — 0 críticos

LO QUE NO EXISTE AÚN (lo que necesitamos):
  ❌ Parquets forex (EURUSD, USDJPY, GBPUSD, USDCHF) → 0 filas
  ❌ P90 para activos forex → None en el registry
  ❌ GDELT para activos forex → no cosechado
  ❌ Datos 15M para ningún activo → no descargados
  ❌ spel_trainer.py → no auditado (puede tener lookahead)
  ❌ spel_score_engine.py → no construido
  ❌ Drive limpio → SPEL-v1.1 y basura aún ocupan espacio

EL PROBLEMA CENTRAL:
  Querer ir a forex intraday sin haber entrenado el modelo base es
  construir el tejado sin los cimientos. El LSTM es el que genera
  el bias direccional. Sin él, el sistema no tiene núcleo.
  
  El orden correcto no es negociable:
  
  LIMPIAR DRIVE → AUDITAR TRAINER → ENTRENAR CORE →
  COSECHAR FOREX DIARIO → ENTRENAR FOREX → COSECHAR 15M → INTRADAY
```

---

## PASO 1 — LIMPIAR DRIVE
**Duración: 20-30 minutos en Colab**
**Resultado: Drive con solo lo que sirve, espacio liberado**

### 1.1 — Qué BORRAR (sin miedo, está todo en SPEL-v2.0)

```python
# Ejecutar en Colab — celdas independientes, una por carpeta
# VERIFICAR primero que SPEL-v2.0 existe y tiene los 4 parquets antes de borrar

import os, shutil
from google.colab import drive
drive.mount('/content/drive')

DRIVE = '/content/drive/MyDrive'

# ── BORRAR COMPLETO ──────────────────────────────────────────
BORRAR = [
    f'{DRIVE}/SPEL-v1.1',              # DEPRECADA desde S20
    f'{DRIVE}/_SPEL_CUARENTENA',       # contenedor de rescate S20 — ya no sirve
    f'{DRIVE}/SPEL_PROD',              # raíz antigua contaminada
    f'{DRIVE}/spel_root',              # runtime anterior (se regenera)
    f'{DRIVE}/SPEL-v2.0/checkpoints',  # vacío o checkpoints sucios
                                       # (los buenos se regeneran después del entrenamiento)
]

# VERIFICAR que SPEL-v2.0 core existe ANTES de borrar nada
CORE_REQUIRED = [
    f'{DRIVE}/SPEL-v2.0/meta/SHA_REGISTRY.json',
    f'{DRIVE}/SPEL-v2.0/data_lake/BTC/ohlcv/aggregated/BTC_ohlcv_v5.parquet',
    f'{DRIVE}/SPEL-v2.0/data_lake/XAU/ohlcv/aggregated/XAU_ohlcv_v5.parquet',
    f'{DRIVE}/SPEL-v2.0/data_lake/NVDA/ohlcv/aggregated/NVDA_ohlcv_v5.parquet',
    f'{DRIVE}/SPEL-v2.0/data_lake/NIFTY50/ohlcv/aggregated/NIFTY50_ohlcv_v5.parquet',
]

for path in CORE_REQUIRED:
    assert os.path.exists(path), f"FALTA: {path} — NO BORRAR HASTA RESOLVER"
    print(f"✅ {path}")

print("\n✅ Core verificado. Procediendo con limpieza...\n")

# BORRAR
for path in BORRAR:
    if os.path.exists(path):
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        print(f"🗑️  Borrado: {path}")
    else:
        print(f"⚠️  No encontrado (ya estaba limpio): {path}")
```

### 1.2 — Qué CONSERVAR en SPEL-v2.0 (estructura final limpia)

```
/content/drive/MyDrive/SPEL-v2.0/          ← TODO LO QUE QUEDA
│
├── codigo/core/                            ← 14 módulos auditados. NO TOCAR.
│   ├── spel_math_engine.py
│   ├── spel_backbone_engine.py
│   ├── spel_orchestrator_v9.py
│   ├── spel_data_harvester.py
│   ├── spel_bulk_harvester.py
│   ├── spel_meta_guardian.py
│   ├── gdelt_foundation.py                 ← NO TOCAR (R6)
│   ├── godel_bound.py
│   ├── critical_loss_optimized.py          ← NO TOCAR (R6)
│   ├── capa_c_inference.py
│   ├── base_adapter.py
│   └── spel_modules.py
│
├── codigo/interface/
│   ├── ojo_de_dios_v23.py
│   └── spel_hud.py
│
├── data_lake/
│   ├── NVDA/ohlcv/aggregated/NVDA_ohlcv_v5.parquet   ✅ SHA: f496c377c7ae
│   ├── NVDA/gdelt/raw/NVDA_gdelt_entropy.parquet
│   ├── BTC/ohlcv/aggregated/BTC_ohlcv_v5.parquet     ✅ SHA: 899052347d73
│   ├── BTC/gdelt/raw/BTC_gdelt_entropy.parquet
│   ├── XAU/ohlcv/aggregated/XAU_ohlcv_v5.parquet     ✅ SHA: d3acbf6342bc
│   ├── XAU/gdelt/raw/XAU_gdelt_entropy.parquet
│   └── NIFTY50/ohlcv/aggregated/NIFTY50_ohlcv_v5.parquet ✅ SHA: 981989b7024d
│   └── NIFTY50/gdelt/raw/NIFTY50_gdelt_entropy.parquet
│
├── meta/
│   ├── SHA_REGISTRY.json                   ← FUENTE DE VERDAD
│   ├── godel_thresholds_v2.json
│   └── auditoria_total_20260311_1736.json
│
├── scripts/                                ← Solo los auditados en S22-S23
│   ├── spel_auditoria_total.py
│   ├── spel_p90_recalibrate.py
│   ├── spel_harvester_v3.py
│   └── spel_github_sync.yml               ← pendiente fix SHA
│
├── checkpoints/                            ← VACÍO hasta entrenar
├── state/
└── logs/
```

### 1.3 — Verificación post-limpieza

```python
# Correr después de borrar para confirmar estado
import json, polars as pl, hashlib

SHA_REG = json.load(open(f'{DRIVE}/SPEL-v2.0/meta/SHA_REGISTRY.json'))

for asset in ['NVDA', 'BTC', 'XAU', 'NIFTY50']:
    path = f'{DRIVE}/SPEL-v2.0/data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet'
    sha = hashlib.sha256(open(path,'rb').read()).hexdigest()[:12]
    expected = SHA_REG[asset]['sha_v5']
    status = '✅' if sha == expected else '❌ MISMATCH'
    print(f"{status} {asset}: {sha} (esperado: {expected})")

print("\n✅ Drive limpio y verificado. Espacio liberado.")
```

**Output esperado: 4 líneas con ✅**
**Si alguna da ❌: PARAR. No continuar hasta resolver.**

---

## PASO 2 — AUDITAR EL TRAINER (antes de correrlo)
**Duración: 1-2 horas**
**Resultado: certeza de que el entrenamiento no tiene lookahead ni bugs silenciosos**

Este paso es el más importante del documento.
Un trainer con lookahead produce resultados que parecen buenos pero no lo son.
Cuando el modelo opera en tiempo real con datos que nunca vio, falla.

### 2.1 — Abrir spel_trainer.py y verificar estos 5 puntos en orden

```
PUNTO 1 — Selección de features (líneas del DataLoader o Dataset)
  Buscar dónde selecciona columnas del parquet.
  Verificar que usa EXACTAMENTE estas 20:
    entropy_shannon, entropy_decay_lambda, entropy_psych_vix,
    fibonacci_lag_1, fibonacci_lag_2, fibonacci_lag_3,
    fibonacci_lag_5, fibonacci_lag_8, fibonacci_lag_13, fibonacci_lag_21,
    goldstein_geo, n_events_ohlcv, vitality_tesla,
    mass_panic_index, fear_momentum, vix_norm,
    nash_frozen_7d, log_return  ← 18... faltan 2

  ESPERA — el tensor es 20 pero solo hay 18 features nombradas en el log.
  Revisar spel_trainer.py para identificar las 2 que faltan.
  Candidatas: open_norm o close_norm (versiones normalizadas de open/close)
  Registrar exactamente cuáles son las 20 en el trainer.

  ⚠️ Si el trainer usa goldstein_mean, tone_variance o zipf_concentration
     como features de entrenamiento → BUG. Esas 3 NO entran al tensor (R13).

PUNTO 2 — Normalización (la más crítica)
  Buscar: StandardScaler, .fit(), .transform(), mean(), std()
  
  CORRECTO:   scaler.fit(X_train) → luego scaler.transform(X_val y X_test)
  INCORRECTO: scaler.fit(X_completo) antes del split → lookahead garantizado
  
  Si el trainer normaliza sobre todo el dataset → BUG-LA-01 vuelve.
  Fix: mover el .fit() a después del split temporal.

PUNTO 3 — Split temporal
  Buscar: train_test_split, iloc, date filter, cut_date
  
  CORRECTO:
    train = df[df['date'] <= '2021-12-31']
    val   = df[(df['date'] >= '2022-01-01') & (df['date'] <= '2022-12-31')]
    test  = df[df['date'] >= '2023-01-01']
  
  INCORRECTO: cualquier shuffle antes del split, o split por índice en lugar de fecha.

PUNTO 4 — Loss function
  Buscar: import de critical_loss_optimized o definición inline de la loss.
  
  CORRECTO:   from critical_loss_optimized import spel_loss
  INCORRECTO: def spel_loss(...): definida en el trainer mismo
  
  Si hay una reimplementación inline → comparar con el original.
  Si son idénticas: aceptar con comentario de advertencia.
  Si difieren en algo: usar el original, no el inline.

PUNTO 5 — Checkpoint guardado
  Buscar: torch.save(), pickle.dump(), o equivalente
  
  CORRECTO (lo que debe guardar):
    {
      'model_state_dict': model.state_dict(),
      'scaler':           scaler,           # para normalizar en inferencia
      'sha_parquet':      sha_del_parquet,  # trazabilidad
      'p90_usado':        P90[asset],       # Gödel config
      'val_dir':          val_dir,          # métricas
      'val_loss':         val_loss,
      'fecha':            datetime.now().isoformat(),
      'asset':            asset,
    }
  
  INCORRECTO: guardar solo model.state_dict() sin scaler ni metadata.
  Sin el scaler, el modelo en inferencia normaliza con parámetros diferentes
  a los del entrenamiento → predicciones incorrectas.
```

### 2.2 — Registro de hallazgos

```
Abrir spel_trainer.py y llenar esta tabla:

| Punto | Estado | Líneas afectadas | Acción |
|-------|--------|-----------------|--------|
| 1. Features 20 | OK / BUG | L___ | ___  |
| 2. Normalización | OK / BUG | L___ | ___  |
| 3. Split temporal | OK / BUG | L___ | ___  |
| 4. Loss function | OK / BUG | L___ | ___  |
| 5. Checkpoint | OK / BUG | L___ | ___  |

Reparar los BUGs uno por uno (R23: un fix a la vez, verificar antes del siguiente).
```

---

## PASO 3 — ENTRENAMIENTO CORE (4 activos base)
**Duración: 2-4 horas según hardware Colab**
**Resultado: 4 checkpoints limpios — el núcleo del sistema**

### 3.1 — Pre-vuelo antes de entrenar

```python
# Verificar que todo está listo — correr antes de entrenar
checks = {
    'SHA_REGISTRY no vacío':    lambda: bool(SHA_REG),
    'SHA parquets match':       lambda: all(sha_match(a) for a in ASSETS),
    'Trainer auditado':         lambda: trainer_audit_passed,  # del Paso 2
    'checkpoints/ vacío':       lambda: len(os.listdir(CHECKPOINTS_DIR)) == 0,
    '0 críticos auditoría':     lambda: auditoria['criticos'] == 0,
}
for nombre, check in checks.items():
    assert check(), f"❌ FALLÓ: {nombre}"
    print(f"✅ {nombre}")
```

### 3.2 — Orden de entrenamiento y qué registrar

```
ORDEN: BTC → XAU → NIFTY50 → NVDA

BTC primero:  lookback=21d, 3998 filas, converge rápido.
              Si el trainer tiene un bug escondido, lo verás aquí
              antes de gastar tiempo en activos más complejos.

XAU segundo: lookback=63d, geopolítica fuerte.
              val_dir esperado: >54% (edge validado = 55.6%)
              Si val_dir < 52% en XAU → el trainer tiene un problema,
              no el modelo. XAU es el activo con más señal GDELT.

NIFTY50:     SYNTHETIC_INDEX — volume=0.0.
              Verifica que el modelo aprende sin Volume Profile.

NVDA último: Si los tres anteriores están bien, NVDA será limpio.

TABLA DE REGISTRO — llenar durante entrenamiento:
┌──────────┬─────────┬──────────┬────────────────┬──────────────┐
│ Activo   │ val_dir │ val_loss │ godel_coverage │ sha_parquet  │
├──────────┼─────────┼──────────┼────────────────┼──────────────┤
│ BTC      │         │          │                │ 899052347d73 │
│ XAU      │         │          │                │ d3acbf6342bc │
│ NIFTY50  │         │          │                │ 981989b7024d │
│ NVDA     │         │          │                │ f496c377c7ae │
└──────────┴─────────┴──────────┴────────────────┴──────────────┘

GATES (binarios — no hay "casi"):
  val_dir > 52%:           si falla → bug en trainer, no en datos
  godel_coverage 30-48%:   si < 10% → P90 no se aplica correctamente
                           si > 60% → P90 demasiado bajo, recalibrar
  checkpoint tiene scaler: si falta → inferencia producirá basura
```

---

## PASO 4 — COSECHAR DATOS FOREX DIARIO
**Duración: 30 minutos**
**Resultado: 4 nuevos parquets forex en canonical v5.1**

Solo después de tener los 4 checkpoints core limpios.
Los activos forex usarán el contexto macro GDELT para su bias direccional.

### 4.1 — Configurar spel_harvester_v3.py para forex

```python
# Añadir al harvester los 4 pares forex
# NO modificar la lógica existente — solo añadir entradas al universo

FOREX_CONFIG = {
    'EURUSD': {
        'ticker':       'EURUSD=X',
        'volume_type':  'TICK_PROXY',   # R16 — no hay volumen OTC real
        'asset_class':  'FOREX',
        'lookback':     21,
        'gdelt_keywords': ['euro', 'european central bank', 'ECB', 'eurozone',
                           'federal reserve', 'dollar', 'USD', 'EUR'],
    },
    'USDJPY': {
        'ticker':       'USDJPY=X',
        'volume_type':  'TICK_PROXY',
        'asset_class':  'FOREX',
        'lookback':     21,
        'gdelt_keywords': ['japan', 'yen', 'bank of japan', 'BOJ', 'USDJPY',
                           'nikkei', 'japanese economy'],
    },
    'GBPUSD': {
        'ticker':       'GBPUSD=X',
        'volume_type':  'TICK_PROXY',
        'asset_class':  'FOREX',
        'lookback':     21,
        'gdelt_keywords': ['uk', 'britain', 'pound', 'bank of england', 'BOE',
                           'brexit', 'sterling'],
    },
    'USDCHF': {
        'ticker':       'USDCHF=X',
        'volume_type':  'TICK_PROXY',
        'asset_class':  'FOREX',
        'lookback':     21,
        'gdelt_keywords': ['switzerland', 'swiss franc', 'SNB', 'safe haven',
                           'geopolitical risk', 'crisis'],
    },
}

# Después del harvest:
# Verificar que cada parquet tiene:
#   → cols == 30 (schema v5.1)
#   → volume dtype == Float64, valores >= 0 (puede ser tick proxy, no 0.0 sentinel)
#   → date dtype == datetime[ms, UTC]
#   → 0 nulls en las 20 features del tensor
```

### 4.2 — Calcular P90 para los 4 pares forex

```python
# Después del harvest, ejecutar recalibración P90 para los nuevos activos
# spel_p90_recalibrate.py ya está corregido (S23)
# Solo añadir los 4 pares forex a la lista de activos y correr

for asset in ['EURUSD', 'USDJPY', 'GBPUSD', 'USDCHF']:
    p90 = recalibrate_p90(asset, cutoff='2023-12-31')
    activation = calculate_godel_activation(asset, p90)
    print(f"{asset}: P90={p90:.4f}, activación={activation:.1%}")
    
    # Actualizar SHA_REGISTRY con el nuevo activo
    SHA_REG[asset] = {
        'sha_v5':       file_sha(parquet_path(asset)),
        'schema':       'canonical_v5.1',
        'n_cols':       30,
        'session':      'S24',
        'p90_entropy':  p90,
        'volume_type':  'TICK_PROXY',
    }

# Guardar SHA_REGISTRY actualizado
json.dump(SHA_REG, open(SHA_REGISTRY_PATH, 'w'), indent=2)
print("✅ SHA_REGISTRY actualizado con activos forex")
```

### 4.3 — Entrenar LSTM para los 4 pares forex

```
Mismo proceso que Paso 3 pero para forex.
Diferencia importante: TICK_PROXY

  → El peso de Volume Profile en Score de Oro es 15% (no 30%)
  → La loss asimétrica es la misma
  → El tensor LSTM es el mismo (20 features — R13 no cambia)
  → vitality_tesla viene del GDELT cosechado con las keywords del par

TABLA DE REGISTRO FOREX:
┌──────────┬─────────┬──────────┬────────────────┬──────────┐
│ Activo   │ val_dir │ val_loss │ godel_coverage │ SHA      │
├──────────┼─────────┼──────────┼────────────────┼──────────┤
│ EURUSD   │         │          │                │          │
│ USDJPY   │         │          │                │          │
│ GBPUSD   │         │          │                │          │
│ USDCHF   │         │          │                │          │
└──────────┴─────────┴──────────┴────────────────┴──────────┘
```

---

## PASO 5 — COSECHAR DATOS 15 MINUTOS
**Duración: 1 hora de configuración + descarga**
**Resultado: datos intraday listos para análisis de estructura**

Solo después de tener checkpoints forex entrenados.
Los datos 15M no son para reentrenar el LSTM — son para la Capa 3 y Capa 4
del sistema de trading (Volume Profile, CVD, VWAP, nuki detector).

### 5.1 — Activos prioritarios para 15M

```
PRIORIDAD 1 (empezar aquí):
  XAU_15m   — mayor edge GDELT documentado, sesión NYSE/COMEX clara
  EURUSD_15m — mayor liquidez forex, sesiones bien definidas
  BTC_15m   — 24/7, datos siempre disponibles

PRIORIDAD 2 (después):
  USDJPY_15m
  GBPUSD_15m
  NVDA_15m   — solo durante NYSE 13:30-20:00 UTC

Fuentes de datos 15M:
  XAU:    yfinance GC=F intervalo='15m', period='60d' máximo disponible
  EURUSD: yfinance EURUSD=X intervalo='15m'
  BTC:    CCXT binance BTC/USDT '15m' (sin geo-restriction si VPN/API key)
          Fallback: yfinance BTC-USD intervalo='15m'
```

### 5.2 — Schema de parquets 15M

```python
# Schema DISTINTO al daily — NO mezclar (R11 se extiende a timeframes)

SCHEMA_15M = {
    'date':        'datetime[ms, UTC]',  # R5
    'open':        'Float64',
    'high':        'Float64',
    'low':         'Float64',
    'close':       'Float64',
    'volume':      'Float64',            # tick proxy para forex
    'session':     'String',             # 'ASIA' | 'LONDON' | 'NY' | 'OVERLAP'
    'asset':       'String',
    'volume_type': 'String',
}

# Ruta de almacenamiento:
# /data_lake/{ASSET}/ohlcv/intraday/{ASSET}_15m.parquet
# 
# Retención: últimos 90 días rolling
# (suficiente para Volume Profile compuesto + lookback visual)
# Más de 90 días: los parquets diarios son la fuente histórica,
# no los intraday (ocuparían demasiado espacio en Drive)
```

### 5.3 — Descarga inicial y validación

```python
import yfinance as yf
import polars as pl

def harvest_15m(ticker: str, asset: str, volume_type: str) -> pl.DataFrame:
    raw = yf.download(ticker, period='60d', interval='15m', auto_adjust=True)
    
    df = pl.DataFrame({
        'date':        pl.Series(raw.index).cast(pl.Datetime('ms', 'UTC')),
        'open':        raw['Open'].values,
        'high':        raw['High'].values,
        'low':         raw['Low'].values,
        'close':       raw['Close'].values,
        'volume':      raw['Volume'].values.astype(float),
        'asset':       [asset] * len(raw),
        'volume_type': [volume_type] * len(raw),
    })
    
    # Añadir etiqueta de sesión
    df = df.with_columns(
        pl.col('date').map_elements(classify_session).alias('session')
    )
    
    # Validar antes de guardar
    assert df.null_count().sum_horizontal().sum() == 0, "NaN detectados"
    assert df['date'].dtype == pl.Datetime('ms', 'UTC'), "dtype date incorrecto"
    
    return df

def classify_session(dt) -> str:
    hour = dt.hour
    if 0 <= hour < 8:   return 'ASIA'
    if 8 <= hour < 13:  return 'LONDON'
    if 13 <= hour < 17: return 'OVERLAP'
    if 17 <= hour < 21: return 'NY'
    return 'OFF'
```

---

## PASO 6 — CONSTRUIR spel_score_engine.py (señales en tiempo real)
**Duración: 1 sesión**
**Resultado: dado un activo y sus datos del día → señal accionable**

Este es el módulo que conecta todo. Sin él, el sistema produce
análisis pero no señales operativas.

```python
# spel_score_engine.py — interfaz mínima viable

def score(asset: str, df_daily: pl.DataFrame,
          df_15m: pl.DataFrame = None) -> dict:
    """
    Input:
      asset:     'EURUSD' | 'XAU' | 'BTC' | etc.
      df_daily:  DataFrame v5.1 con al menos lookback[asset] filas diarias
      df_15m:    Opcional — si se pasa, enriquece con microestructura

    Output:
      {
        'score':           int 0-100,
        'modo':            'NO_TRADE' | 'SCALP' | 'INTRADAY' | 'SWING' | 'CRISIS_CONTRA',
        'bias':            'LONG' | 'SHORT' | 'NEUTRAL',
        'kelly_fraction':  float,   # nunca > 0.25
        'godel_active':    bool,
        'entropy_pct':     float,
        'vitality':        int,
        'reasoning':       str,     # por qué se generó esta señal
        'sha_parquet':     str,
        'timestamp':       str,
      }
    """
    # Validar SHA antes de calcular (R3)
    assert file_sha(get_path(asset)) == SHA_REG[asset]['sha_v5']
    
    # Capa 1: régimen macro (ya existe)
    macro  = macro_bias(asset, df_daily)
    modo   = route_trading_mode(asset, macro['entropy_pct'], 
                                 macro['vitality'], macro['nash_frozen'])
    
    # Capa 3: microestructura (si hay datos 15M)
    micro = {}
    if df_15m is not None and len(df_15m) >= 50:
        micro = {
            'vwap':    calculate_vwap(df_15m),
            'poc':     calculate_poc(df_15m),
            'cvd':     calculate_cvd(df_15m),
        }
    
    # Score combinado
    godel_score  = 100 if macro['godel_active'] else 30
    te_score     = int(abs(macro['te_direction']) * 100)
    vol_score    = calculate_volume_score(df_daily, asset) if micro else 0
    
    pesos = SCORE_WEIGHTS[SHA_REG[asset]['volume_type']]
    score_total = int(
        pesos['godel'] * godel_score +
        pesos['te']    * te_score    +
        pesos['vol']   * vol_score
    )
    
    # Kelly
    kelly = min(backbone.kelly_fraction(asset, val_dir=get_val_dir(asset)), 0.25)
    if modo in ('SCALP', 'INTRADAY'): kelly *= 0.50
    
    return {
        'score':          score_total,
        'modo':           modo,
        'bias':           macro['bias'],
        'kelly_fraction': kelly,
        'godel_active':   macro['godel_active'],
        'entropy_pct':    macro['entropy_pct'],
        'vitality':       macro['vitality'],
        'reasoning':      build_reasoning(macro, micro, modo),
        'sha_parquet':    SHA_REG[asset]['sha_v5'],
        'timestamp':      datetime.now().isoformat(),
    }
```

---

## PASO 7 — GDELT EN TIEMPO REAL (el sensor principal)
**Duración: configuración única + cron automático**
**Resultado: entropía GDELT actualizada cada día automáticamente**

GDELT publica datos cada 15 minutos en:
`http://data.gdeltproject.org/gdeltv2/lastupdate.txt`

Para SPEL (que opera en timeframe diario y 15M con contexto diario),
la actualización diaria al cierre UTC es suficiente.

```python
# gdelt_ingest_incremental.py
# Correr: 1 vez al día a las 23:55 UTC (cron en GitHub Actions)

def ingest_gdelt_daily(asset: str, keywords: list) -> None:
    """
    Descarga el resumen GDELT del día.
    Calcula entropy_shannon, vitality_tesla, nash_frozen_7d, etc.
    Hace APPEND al parquet GDELT existente (no reescribe).
    Actualiza SHA_REGISTRY.
    """
    today = datetime.utcnow().date()
    
    # Descargar eventos GDELT del día
    gdelt_url = f"http://data.gdeltproject.org/gdeltv2/{today:%Y%m%d}000000.export.CSV.zip"
    events = download_and_filter(gdelt_url, keywords)
    
    # Calcular features de entropía del día
    new_row = {
        'date':              pl.lit(today).cast(pl.Datetime('ms', 'UTC')),
        'asset':             asset,
        'entropy_shannon':   shannon_entropy(events),
        'n_events':          len(events),
        'goldstein_mean':    events['GoldsteinScale'].mean(),
        'tone_variance':     events['AvgTone'].var(),
        'vitality_tesla':    classify_vitality(events),
        'nash_frozen_7d':    calculate_nash_frozen(events, window=7),
        'zipf_concentration': zipf_exponent(events),
    }
    
    # Append al parquet existente (no reescribir)
    gdelt_path = f'{DATA_LAKE}/{asset}/gdelt/raw/{asset}_gdelt_entropy.parquet'
    existing = pl.read_parquet(gdelt_path)
    
    # Verificar que no hay duplicado de fecha
    if today not in existing['date']:
        updated = pl.concat([existing, pl.DataFrame([new_row])])
        updated.write_parquet(gdelt_path)
        
        # Actualizar SHA en registry
        SHA_REG[asset]['sha_gdelt'] = file_sha(gdelt_path)
        SHA_REG[asset]['gdelt_max_date'] = str(today)
        json.dump(SHA_REG, open(SHA_REGISTRY_PATH, 'w'), indent=2)
        print(f"✅ GDELT actualizado: {asset} {today}")
    else:
        print(f"⚠️  GDELT ya tiene {today} para {asset} — skip")
```

---

## RESUMEN: SECUENCIA COMPLETA

```
HOY (esta sesión):

  ├── PASO 1: Limpiar Drive                    [20-30 min]
  │     └── Output: Drive limpio, 4 SHA verificados, espacio liberado
  │
  ├── PASO 2: Auditar spel_trainer.py           [1-2h]
  │     └── Output: tabla de 5 puntos, bugs identificados y corregidos
  │
  └── PASO 3: Entrenar 4 activos core           [2-4h]
        └── Output: 4 checkpoints en /checkpoints/ con SHA y val_dir

PRÓXIMA SESIÓN:

  ├── PASO 4: Harvest forex diario + P90        [30 min]
  │     └── Output: 4 nuevos parquets forex en SHA_REGISTRY
  │
  ├── PASO 4b: Entrenar 4 pares forex           [1-2h]
  │     └── Output: 4 checkpoints forex
  │
  └── PASO 5: Harvest 15M para XAU, EURUSD, BTC [1h]
        └── Output: parquets 15M en /data_lake/{ASSET}/ohlcv/intraday/

SEMANA 2:

  ├── PASO 6: spel_score_engine.py              [1 sesión]
  │     └── Output: score() retorna señal para cualquier activo < 2s
  │
  └── PASO 7: GDELT incremental + cron          [1 sesión]
        └── Output: entropía actualizada automáticamente cada día

A PARTIR DE AHÍ:
  → Sistema genera señales diarias y en 15M
  → Paper trading con EURUSD/XAU/BTC simultáneo
  → 63 días de paper → gate a live
```

---

## LO QUE NO HACEMOS HOY

```
❌ No construimos spel_market_structure.py todavía
   (BOS/CHoCH, Order Blocks) → después del primer paper trading

❌ No conectamos GitHub Actions todavía
   → después de tener score_engine funcionando

❌ No vamos a live todavía
   → 63 días de paper es el gate, sin excepciones

❌ No tocamos gdelt_foundation.py
   → R6 inamovible, el ingest incremental es un script separado

❌ No añadimos más activos al universo todavía
   → 4 core + 4 forex es suficiente para validar el sistema
   → SPX, NDX, DAX, ETH, SOL → después de que forex esté funcionando
```

---

*S24 Plan · 11-Mar-2026 · Post-S23*
*Secuencia: Drive limpio → Trainer auditado → Core entrenado → Forex → 15M → Score Engine → Paper*
*Próximo log: v34 · después de completar Pasos 1-3*
