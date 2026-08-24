# SPEL — Decision Log

Auditoría de decisiones de arquitectura (stream `DECISION_LOG`, Decisión #14). Cada entrada: fuente verificada, hallazgo, decisión tomada, validación pendiente. No se registran cosas ya cubiertas por docstring de código — esto es para decisiones que cruzan más de un archivo o que alguien en una sesión futura necesita encontrar sin tener que releer 5 patches.

---

## 2026-08-23 (PR-1) — Convención de nombres de secretos por proveedor

**Fuente:** documentación oficial de cada proveedor, y auditoría de `governance/secrets.py`
contra el repo real (grep, no memoria).

**Hallazgo:** los proveedores no llaman igual a su credencial. TwelveData y AlphaVantage
la llaman `apikey`; Tiingo y Deriv la llaman `token`; Alpaca emite un par (key + secret).
Uniformar los nombres del registro a la fuerza — todo a `_API_KEY`, por ejemplo — habría
sido más prolijo de leer y peor de auditar: buscar el nombre de la variable en la doc del
proveedor dejaría de dar resultado, que es justo lo que hace falta para verificar cada
adapter contra su documentación real.

Segundo hallazgo, del mismo grep: **no hay ningún punto de composición en el repo.** Nada
instancia un adapter fuera de tests, `load_secret()` no se llama desde producción (los dos
hits fuera de `tests/` son docstrings de `persistence.py`), y ningún workflow inyecta
secretos (cero `secrets.` y cero `env:` en `.github/workflows/`). Las piezas existen y
están probadas; nadie las conecta.

**Decisión:** el sufijo de cada clave espeja el nombre que el proveedor usa en su propia
doc (`_API_KEY` para apikey, `_API_TOKEN` para token, `_API_KEY` + `_SECRET_KEY` para el
par de Alpaca). Se registran `TWELVEDATA_API_KEY`, `ALPHAVANTAGE_API_KEY` y
`TIINGO_API_TOKEN` **antes** de que exista su adapter, a propósito: el registro es la
lista de secretos que el proyecto reconoce, no la de los que ya se usan —
`secrets_status_report()` los muestra ausentes hasta que se configuren, que es exactamente
la visibilidad que faltaba.

**No se tocó `load_secret()`.** La auditoría no encontró defecto: la prioridad
env → Colab → `SecretError` es correcta, no expone valores en el mensaje de error, y no
tiene la ofuscación de import que sí tenía el legado. Un test nuevo
(`test_secret_error_de_proveedor_no_incluye_el_valor`) fija esa última propiedad por
escrito, para que un "mejoremos el mensaje agregando contexto" falle en CI y no cuando un
token real llegue a un log.

**Descartado:** un nombre uniforme para todas las claves (ver Hallazgo). También se
descartó agregar los secretos al workflow en este PR — inyectar un secreto que ningún
código lee todavía es superficie de exposición sin beneficio; entra cuando entre el
adapter que lo consume.

**Validación pendiente:** las 3 claves no se probaron contra el endpoint real de ningún
proveedor — no hay adapter que las use todavía (PR-2). Que el nombre sea el correcto está
verificado contra la doc, no contra una respuesta HTTP 200.

---

## 2026-08-23 (PR-2) — Contrato de datos OHLCV: velas cerradas, metadata de calidad y transporte de attrs

**Fuente:** auditoría de `ingestion/adapters.py` contra el repo real, más comportamiento
de pandas medido en la versión instalada (3.0.5), no citado de memoria ni de la doc.

**Hallazgo 1 — la salvaguarda anti-fuga-temporal era un método privado de un solo
adapter.** `DerivAdapter._drop_unclosed_candle` funcionaba bien, pero como método privado
depende de que cada adapter nuevo se acuerde de reimplementarla. Un adapter que
simplemente no la tenga deja pasar la vela en formación disfrazada de dato histórico, sin
que nada lo detecte — que es exactamente la hipótesis #1 que Fase 2 tiene que descartar
(fuga de horizonte).

**Hallazgo 2 — `_to_dataframe` tenía un parámetro con el nombre equivocado.** Se llamaba
`source` y su único call site le pasaba el símbolo del usuario
(`_to_dataframe(..., source=symbol)`). El log resultante sí mostraba el instrumento
(`[deriv] ... para EURUSD`), así que no había pérdida de información hoy — el problema es
que el nombre decía "proveedor" y el valor era el instrumento, y al promover el filtro a
una función que recibe AMBOS por separado, arrastrar ese nombre habría hecho que el
proveedor se registrara como el símbolo. Es privado y ningún test lo invoca directo, así
que el rename salió gratis; en cuanto hubiera un segundo call site dejaría de serlo.

**Hallazgo 3 — un 0.0 de volumen es ambiguo y nada lo desambiguaba.** Deriv nunca reporta
volumen (su doc oficial define la vela con exactamente close/epoch/high/low/open), así
que el `0.0` que escribe el adapter es relleno. Sin una bandera explícita, ese relleno y
un 0.0 legítimo de "no se operó en esta vela" son indistinguibles aguas abajo, y
cualquier feature que promedie volumen mezcla las dos cosas en silencio.

**Hallazgo 4 — `df.attrs` sirve como transporte y NO como almacenamiento, medido.** En
pandas 3.0.5: sobrevive `copy()`, `sort_values()`, `concat()` y `reset_index()`, y **se
pierde en `merge()`**. `ingestion/training_dataset.py` hace exactamente un join
OHLCV↔GDELT, o sea que apoyarse en `attrs` para procedencia durable la perdería
justo al armar el dataset de entrenamiento, sin excepción ni warning.

**Decisiones:**

1. `drop_unclosed_candles()` pasa a ser función de módulo, con `source` obligatorio
   (proveedor), `symbol` opcional (instrumento) y `now_utc` inyectable. Lo último no es
   comodidad: sin reloj fijo, un fixture con una vela deliberadamente abierta se vuelve
   cerrada cuando el reloj avanza, y el test se vuelve intermitente.
2. `validate_ohlcv_schema()` gana `require_closed` / `granularity_s` / `now_utc`
   keyword-only, con defaults que no rompen ninguna llamada existente. Semántica
   condicional: valida el cierre *cuando es posible saberlo*. **La granularidad nunca se
   infiere del espaciado** — un dataset con huecos legítimos (fin de semana forex,
   feriados) daría una inferencia equivocada, y una granularidad equivocada es peor que
   ninguna. La verificación va al final, después de las estructurales: si falta
   `timestamp` o no es UTC, ese error es más informativo.
3. `AdapterResult` gana cuatro campos con default (`volume_available`,
   `timestamp_is_convention`, `is_fallback`, `provider_status`) en vez de un tipo nuevo.
   `is_fallback` es deliberadamente independiente de `is_degraded`: un respaldo puede
   funcionar perfecto. `AdapterChain` levanta los attrs con `.get()` y default
   conservador — un adapter que no escribe attrs no está incumpliendo el contrato, está
   no reportando esa dimensión.
4. Los cuatro campos entran también al log de auditoría persistido: sin ellos el log dice
   de dónde vino cada dato pero no con qué calidad, y esa es justo la pregunta que hay que
   poder contestar hacia atrás cuando un modelo se comporta raro.
5. `pandas>=2.2,<4` — única dependencia pineada, porque `attrs` es API que pandas
   documenta como experimental y el contrato depende de su comportamiento exacto. Rango
   amplio, no `==`: fija el límite donde una major podría romperlo, sin obligar a
   actualizar un número a mano cada mes.

**Estilo de anotaciones:** las firmas nuevas usan `X | None` (sintaxis moderna). Las 5
preexistentes con `Optional[X]` se dejan como están — migrarlas sería ruido en el diff de
este PR, sin ganancia funcional. El archivo ya tiene `from __future__ import annotations`,
así que las dos formas conviven sin problema.

**Descartado:** inferir la granularidad del espaciado entre timestamps (ver Hallazgo/
Decisión 2). También se descartó crear un tipo `DataQuality` separado para los cuatro
campos: son cuatro banderas planas, y un tipo nuevo agregaría una capa de indirección sin
resolver nada que el dataclass no resuelva ya.

**Validación pendiente:** la red de seguridad de `fetch_ohlcv()` es redundante a
propósito y en el camino normal no dispara nunca — está verificada por mutación (quitar
`granularity_s` del call site deja 64/65 tests en verde, solo la detecta el test que
inspecciona el argumento), pero no se observó dispararse contra un feed real. Y ningún
adapter escribe todavía `timestamp_is_convention=True` ni un `provider_status` distinto de
`"ok"`: esos dos caminos están probados con dobles, no contra un proveedor real — entra
con TwelveData (PR-3).

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

## 2026-08-17 — Confirmación: `execution/` (Actuator) entra a producción en Fase 4

**Fuente:** decisión directa de Altair, no un hallazgo de auditoría.

**Decisión:** `execution/circuit_breaker.py` y `execution/execution_guard.py` (31 tests,
congelados desde su construcción) quedan confirmados para pasar a uso activo cuando arranque
Fase 4 — no antes, la compuerta de Fase 2 sigue firme (capital real solo después de que el
paper trading lo demuestre, `governance/PRINCIPLES.md` #6). Esto no adelanta Fase 4, formaliza
qué pasa cuando llegue.

**Validación pendiente:** ninguna nueva — sigue dependiendo de que Fase 2 cierre primero.

---

## 2026-08-17 — Investigación: motor de streaming multi-timeframe (Deriv + Alpaca), DRL + ONNX

**Fuente:** propuesta de Altair + 6 búsquedas web verificadas esta sesión (no opinión sin
respaldo). Detalle completo en `BLUEPRINT.md`, Fase 6.

**Hallazgo:** los índices sintéticos de Deriv están diseñados para ser inmunes a noticias reales
(confirmado con la propia documentación de Deriv) — GDELT no aplica ahí, sí aplica en forex/oro
real. GitHub Actions tiene piso de 5 min en `cron` y no garantiza puntualidad — no sirve como
host de un motor de 1 minuto. Reinforcement Learning para trading tiene resultados de producción
reales en ejecución/hedging, no en alpha puro desde precio — alto riesgo de sobreajuste sin
walk-forward real.

**Decisión:** motor rápido (Deriv sintéticos + Alpaca) se trata como un Fase 6 en evaluación,
paralelo al motor GDELT (Fases 1-5), no un reemplazo. Host propuesto para el motor rápido: VM
gratuita Oracle Cloud Always Free (no Termux, no GitHub Actions). Componente de aprendizaje se
nombra `DRL` (no `RL`, que ya está tomado por Risk Limits en `axiom_master.xml`). ONNX se
revisita cuando exista un modelo entrenado que exportar, no antes.

**Descartado:** construir el motor rápido sobre `core/scoring.py` tal cual (día como unidad de
tiempo) — física de datos incompatible con 1-30 min, no es un ajuste de parámetro.

**Validación pendiente:** Altair debe confirmar si GDELT sigue siendo el motor principal con el
motor rápido en paralelo, o si cambia la prioridad — no se asumió ninguna de las dos en esta
sesión.

---

## 2026-08-17 — Deriv primero y único para capital real; Alpaca a paper hasta nuevo aviso

**Fuente:** decisión directa de Altair, motivada por una restricción real, no de ingeniería:
Deriv acepta depósito sin verificación de identidad; la cuenta que sí quedará verificada (para
poder retirar ganancias) depende de un tercero de confianza y toma tiempo. Detalle personal
completo en memoria de usuario, no en este repo — acá solo la consecuencia técnica.

**Decisión:** Deriv es el único broker autorizado para capital real al arrancar Fase 4. Alpaca
se mantiene funcional (se sigue construyendo y probando) pero **gateado a paper trading
exclusivamente** hasta que Altair confirme explícitamente lo contrario. Reflejado en
`BLUEPRINT.md`, Fase 4.

**Requisito para código futuro (no implementado todavía, no existe ruteo de órdenes de ningún
tipo aún):** cuando se escriba la lógica de ruteo de Fase 4, el bloqueo de órdenes reales hacia
Alpaca debe ser un guardrail duro (no una bandera de configuración que se pueda tocar por
accidente) — mismo principio que ya rige `execution_guard.py` y `circuit_breaker.py` (capa
no-IA, no depende de que el modelo "decida bien").

**Corrección de una entrada anterior de este log:** la entrada del 17 ago sobre Fase 6 proponía
Oracle Cloud Always Free como host del motor rápido — probado por Altair esa misma sesión, no
viable (fricción de signup). Reemplazado por un segundo trigger en GitHub Actions
(`.github/workflows/heartbeat.yml`, patch 0024) — ver ese patch para el detalle real, no la
entrada original de este log.

---

## Principios que se sostuvieron toda la sesión

- Ningún número entra sin fuente verificada contra el código real (no contra memoria de sesiones anteriores, no contra texto pegado sin auditar).
- Todo commit corre pytest antes de comitear. Todo patch se verifica en clon 100% ajeno vía `git am` antes de entregarse.
- Discrepancias encontradas (memoria vs. fuente real, texto externo vs. constantes reales) se registran explícitamente, no se resuelven en silencio.
- Un hallazgo de auditoría no crítico (#6, `drive_root`) se corrigió antes de seguir agregando trabajo encima, no se dejó como nota para "después".
