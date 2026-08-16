# SPEL — Decision Log

Auditoría de decisiones de arquitectura (stream `DECISION_LOG`, Decisión #14). Cada entrada: fuente verificada, hallazgo, decisión tomada, validación pendiente. No se registran cosas ya cubiertas por docstring de código — esto es para decisiones que cruzan más de un archivo o que alguien en una sesión futura necesita encontrar sin tener que releer 5 patches.

---

## 2026-08-16 — Fix: `nash_frozen_7d` normalizaba con la misma ventana del std

**Fuente:** confirmado con números, no solo argumentado — 500 muestras aleatorias de micro-ruido (rango real ~0.0015).

**Hallazgo:** normalizar con min/max de los mismos 7 días usados para el std fuerza el rango a [0,1] siempre, sin importar la magnitud real de la variación. Con referencia de 7 días: 500/500 casos daban falso "no congelado". Con referencia de 60+ días: 500/500 correctos.

**Decisión:** desacoplar `entropy_window` (referencia, tan larga como haya historia) de `window_days` (cola fija de 7 para el std). Campo nuevo `insufficient_reference` cuando la referencia es < 3x `window_days`.

**Descartado:** coeficiente de variación (alternativa legacy en `spel_ingest_incremental.py`) — hubiera roto la calibración de `NASH_FROZEN_THRESHOLD=0.15`, hecha sobre escala normalizada [0,1], no sobre CV.

**Validación pendiente:** ¿3x es el múltiplo correcto, o hace falta más referencia en la práctica? Sin backtest.

---

## 2026-08-16 — Reincorporación: `legacy_entropy_threshold` en `gold_score_bma`

**Fuente:** `spel_bayesian_core.py::SHANNON_KILL_THRESHOLD=0.42`.

**Hallazgo:** `godel_active()` depende de `p90_entropy`, que en frío (poca historia) puede venir de `compute_adaptive_percentile()` en modo GLOBAL — un default sin backtest. Si ese default está mal calibrado, `godel_active()` puede fallar en dejar pasar entropías moderadas-altas.

**Decisión:** `legacy_entropy_threshold=0.42` como red de seguridad INDEPENDIENTE, no reemplazo. Prioridad confirmada empíricamente en las 3 combinaciones cruzadas: `godel_active > legacy_entropy_threshold > drift_control`. `None` desactiva.

**Nota histórica:** en el patch anterior (0007) se había reemplazado el umbral fijo por `godel_active()` puro, razonando Tamiz 3 (una implementación por concepto). Esta sesión revirtió parcialmente esa decisión — no por error de razonamiento, sino porque el caso de uso real (calibración en frío) no se había considerado. Ver benchmark A/B/C (0016) para los 5 escenarios donde A, B y C divergen.

**Validación pendiente:** benchmark A/B/C usa datos sintéticos. Falta correr contra datos reales cuando exista ingestion GDELT.

---

## 2026-08-16 — Adición: `compute_entropy_delta_lags` (no reemplaza niveles)

**Fuente:** ninguna — sin precedente legacy para esta forma específica (deltas vs. niveles).

**Hallazgo:** colinealidad entre los 7 lags Fibonacci cercanos era una hipótesis razonable (ya documentada como pendiente en el patch 0006), pero confirmarla necesita matriz de correlación sobre entropía real, que no existe (sin ingestion GDELT corriendo).

**Decisión:** función ADICIONAL (`ΔE_k = E_t - E_{t-k}`), no reemplazo ni reducción a subconjunto `{1,5,21}`. Elegir un subconjunto ahora hubiera sido un número sin evidencia — exactamente lo que este proyecto evita en cada decisión. Niveles y deltas coexisten para que F2 compare con datos reales.

**Validación pendiente:** comparar poder predictivo de niveles vs. deltas con datos reales antes de elegir default del pipeline de features.

---

## 2026-08-16 — Refactor: `AdapterChain.fetch_async()` nativo

**Fuente:** trampa de reentrancia documentada desde sesiones anteriores en el docstring de la clase.

**Hallazgo (medido, no argumentado):** la versión anterior llamaba `asyncio.run()` una vez POR INTENTO de reintento (hasta 3 por adapter). Confirmado con contador real en auditoría: la nueva hace exactamente 1 llamada total, sin importar reintentos internos.

**Decisión:** `fetch_async()` es la lógica real (async nativo, `await asyncio.sleep()` en vez de `time.sleep()` bloqueante). `fetch()` es wrapper delgado — detecta loop activo con `asyncio.get_running_loop()`, lanza `RuntimeError` explícito señalando `fetch_async()` si lo hay.

**Validación pendiente:** ninguna conocida — refactor ya verificado con test de reentrancia real (el propio test corre dentro de un loop activo, no un mock).

---

## 2026-08-16 — Fix: `drive_root()` hardcodeado en `governance/persistence.py`

**Fuente:** hallazgo de auditoría (#6), no reportado por el usuario — encontrado revisando coherencia contra `governance/secrets.py`.

**Hallazgo:** `DRIVE_ROOT` estaba fijo a la ruta de Colab de Altair, sin condicional. No rompía nada en el momento (el módulo solo declaraba rutas, sin I/O real), pero violaba el principio ya establecido en `secrets.py` (detección de entorno, nunca ruta fija) y hubiera fallado en cuanto GitHub Actions necesitara el stream.

**Decisión:** `drive_root()` sigue el mismo orden de prioridad que `secrets.py::load_secret()`: env var `SPEL_DRIVE_ROOT` → detección de Colab → fallback local (`.spel_drive_stream`, marcado explícitamente). Se evalúa en cada llamada, no al importar.

**Validación pendiente:** ninguna — comportamiento probado en los 3 niveles con `monkeypatch`, no solo declarado.

---

## Principios que se sostuvieron toda la sesión

- Ningún número entra sin fuente verificada contra el código real (no contra memoria de sesiones anteriores, no contra texto pegado sin auditar).
- Todo commit corre pytest antes de comitear. Todo patch se verifica en clon 100% ajeno vía `git am` antes de entregarse.
- Discrepancias encontradas (memoria vs. fuente real, texto externo vs. constantes reales) se registran explícitamente, no se resuelven en silencio.
- Un hallazgo de auditoría no crítico (#6, `drive_root`) se corrigió antes de seguir agregando trabajo encima, no se dejó como nota para "después".
