# SPEL S24 — Scripts de Auditoría y Setup

Estos 3 scripts se ejecutan en Colab en el orden exacto indicado.
Cada uno hace exactamente una cosa y genera un output verificable.

---

## ORDEN DE EJECUCIÓN

```
PASO 1 → spel_drive_cleanup.py      (limpia Drive)
PASO 2 → spel_preflight_s24.py      (pre-vuelo: verifica estado del sistema)
PASO 3 → spel_trainer_audit.py      (audita el trainer antes de entrenar)
[entrenar los 4 activos core]
PASO 4 → spel_posttraining_audit.py (verifica los checkpoints generados)
```

---

## INSTRUCCIONES POR SCRIPT

### 1. spel_drive_cleanup.py

Limpia carpetas obsoletas de Drive. Verifica SHA antes de borrar cualquier cosa.

```python
# En Colab — primero en dry-run para ver qué borraría:
!python spel_drive_cleanup.py --dry-run

# Cuando estés seguro — borrar de verdad:
!python spel_drive_cleanup.py
# (te pedirá escribir 'BORRAR' para confirmar)
```

**Output esperado:** ~X MB liberados, 4 parquets core intactos post-delete.

---

### 2. spel_preflight_s24.py

13 checks del estado del sistema. Corre al inicio de CADA sesión.

```python
!python spel_preflight_s24.py
```

**Output esperado:**
```
✅ Pasaron:    11
⚠️  Warnings:   2   ← BUG-TENSOR-DOC y posible legacy
❌ Fallaron:   0

✅ SISTEMA LISTO PARA CONTINUAR
```

**Si hay ❌:** PARAR. Leer el detalle del bug. Resolver antes de continuar.

**Warnings esperados (no bloquean):**
- `BUG-TENSOR-DOC`: el log documenta 18 features pero input_size=20.
  Se resuelve en el Paso 3 (auditoría del trainer).
- `Sin archivos legacy`: si ya los borraste en Paso 1, desaparece.

---

### 3. spel_trainer_audit.py

Lee `spel_trainer.py` y reporta bugs con números de línea exactos.

```python
# Si el trainer está en la ruta conocida:
!python spel_trainer_audit.py

# Si está en otra ruta:
!python spel_trainer_audit.py --trainer /content/drive/MyDrive/SPEL-v2.0/scripts/spel_trainer.py
```

**Output esperado si el trainer está limpio:**
```
P1 Selección features tensor:    ✅ OK  (o ⚠️ WARN por BUG-TENSOR-DOC)
P2 Normalización anti-lookahead: ✅ OK
P3 Split temporal:               ✅ OK
P4 Loss function:                ✅ OK
P5 Checkpoint metadata:         ✅ OK

✅ TRAINER LISTO PARA ENTRENAR
```

**Si hay ❌ BUGs:** el script muestra la línea exacta y el fix.
Aplicar un fix a la vez (R23). Re-ejecutar el auditor después de cada fix.

**BUG-TENSOR-DOC** — qué hacer cuando el auditor lo reporta:
1. Buscar en el trainer la lista de features (busca: `FEATURES`, `cols`, `feature_cols`)
2. Contar las features que selecciona
3. Si son 20 → el log está desactualizado, actualizar el log con las 2 features reales
4. Si son 18 o menos → el trainer tiene un bug, añadir las 2 features faltantes

---

### 4. spel_posttraining_audit.py

Verifica los checkpoints DESPUÉS de entrenar. Corre antes de usar los checkpoints.

```python
# Auditar todos los activos:
!python spel_posttraining_audit.py

# Solo un activo:
!python spel_posttraining_audit.py --asset BTC
```

**Output esperado:**
```
Activo     Estado          val_dir    SHA match    Bugs
BTC        ✅ LISTO         54.2%      ✅ match      0
XAU        ✅ LISTO         56.1%      ✅ match      0
NIFTY50    ✅ LISTO         52.8%      ✅ match      0
NVDA       ✅ LISTO         53.5%      ✅ match      0

✅ TODOS LOS CHECKPOINTS VÁLIDOS
```

**Gates que deben pasar:**
- val_dir > 52% en todos los activos
- Gödel coverage 30-48% en todos
- SHA del checkpoint == SHA del parquet en registry
- Scaler guardado con 20 features

---

## BUGS CONOCIDOS A RESOLVER EN S24

| ID | Descripción | Script que lo detecta | Fix |
|----|-------------|----------------------|-----|
| BUG-TENSOR-DOC | Log documenta 18 features, input_size=20 | spel_trainer_audit.py P1 | Leer trainer y añadir las 2 features al log |
| SHA-YML | spel_github_sync.yml tiene SHA incorrectos | (manual) | Abrir yml, reemplazar con SHA_REGISTRY.json |
| GDELT-2026 | Gap GDELT 2026 | spel_preflight_s24.py | gdelt_ingest_incremental.py (Paso 7) |

---

## DESPUÉS DE ESTOS PASOS

Una vez que `spel_posttraining_audit.py` pasa sin bugs:

1. **Harvest forex** — spel_harvester_v3.py con EURUSD, USDJPY, GBPUSD, USDCHF
2. **Entrenar forex** — misma secuencia, mismos auditors
3. **Harvest 15M** — XAU, EURUSD, BTC
4. **Construir spel_score_engine.py** — conecta las 4 capas
5. **Paper trading** — 63 días mínimo

---

*S24 Scripts · 11-Mar-2026 · Post-S23*
