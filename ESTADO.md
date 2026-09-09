# SPEL — ESTADO DEL PROYECTO

> **Este archivo es la única fuente de verdad sobre en qué fase estamos.**
> Se lee primero en cada chat nuevo. Se actualiza al final de cada sesión, nunca a mitad.
> Si algo en un chat contradice lo que dice acá, este archivo gana — a menos que
> el chat verifique contra GitHub real y lo actualice con evidencia.

**Última actualización:** 6 sep 2026 — cierra Fase 1 con el resultado de la validación
de la máscara. El archivo llevaba desde el 18 de agosto diciendo 402 tests mientras
`main` llegaba a 661: 19 días de desactualización, exactamente la misma señal de
gobernanza que este archivo ya se había señalado a sí mismo el 17 y el 18 de agosto y
que volvió a ocurrir. La regla del punto 6 de "cómo actualizar" existe por esto y no
alcanzó; queda como incógnita abierta si hace falta un chequeo automático.

**Ver también:** `FASE2_NOTAS_ARQUITECTURA_MODELO.md` (raíz del repo) — glosario y
opciones de arquitectura de modelo (LSTM vs. árboles/ensambles), separado de este
archivo a propósito para no mezclar "estado actual" con "notas de investigación".

---

## 🚦 SEMÁFORO DE FASES

```
FASE 1 — ingestion/ + core/scoring.py     🔵 CERRADA — resultado NEGATIVO medido
FASE 2 — Modelo                            ⚪ NO INICIADA — reorientada, ver abajo
FASE 3 — visualization/ (grafo)            ⚪ NO INICIADA
FASE 4 — execution/ + Deriv real           🟡 Actuator confirmado, gate F2 firme
FASE 5 — Escala (Supabase, Flet)           ⚪ NO INICIADA
FASE 6 — Motor streaming multi-timeframe   🟡 Infraestructura lista, señal sin construir
```

🔵 **CERRADA no es 🟢 LISTA.** Fase 1 se cierra porque su pregunta quedó contestada, y
la respuesta fue que no. El pipeline funciona, está medido y tiene `n` suficiente; lo
que no funciona es la hipótesis que ese pipeline existía para probar.

---

## 📍 MÓDULOS REALES EN `main` HOY (verificado, no listado de memoria)

**661 tests** (663 recolectados, 2 con `skipif`: el `live` de TwelveData y uno de
persistencia). Verificado corriendo la suite, no contado de memoria. Todo lo de abajo
corre en clon 100% ajeno, venv limpio desde `requirements.txt`, 10 corridas seguidas
sin intermitencia.

> El número que este archivo traía era **402**, del 18 de agosto. La diferencia son 19
> días de trabajo que el archivo no reflejó.

| Módulo | Qué hace | Tests | Estado |
|---|---|---:|---|
| `core/scoring.py` | `entropy_state` (Capa 1), `godel_active`, vitality_tesla, nash_frozen_7d, gold_score_bma, classify_gdelt_event | 163 | ✅ |
| `core/monte_carlo.py` | Validación GBM — NO entrena, simulación pura en cada llamada | 22 | ✅ |
| `core/price_signals.py` | te_score (proxy TE) + backbone_score (EMA20/63) | 12 | ✅ |
| `ingestion/adapters.py` | DerivAdapter + TwelveDataAdapter + contrato de datos | 65 + 29 | ✅ |
| `ingestion/sources.py` | **Punto de composición** — `build_price_sources()`; `SourceInventory` distingue capacidad ausente de error | 11 | ✅ |
| `ingestion/gdelt.py` + `_aggregation` + `_series` | Pipeline GDELT completo, persistencia JSONL | 41 | ✅ |
| `ingestion/training_dataset.py` | Une OHLCV + serie GDELT, forward-fill, coverage_ratio explícito | 7 | ✅ |
| `ingestion/source_registry.py` | Registro versionado de cobertura por fuente | 34 | ✅ |
| `orchestration/cycle.py` | Corre vitality/nash/godel sobre 5 activos; sella `godel_criteria_version` | 19 | ✅ |
| `execution/circuit_breaker.py` + `execution_guard.py` | Guardrails duros — congelados hasta F4 | 31 | ✅ |
| `governance/persistence.py` + `secrets.py` | 4 streams, SecretKey único | 28 | ✅ |
| `tools/measure_godel_samples.py` | Mide el `n` post-máscara. **El tool que produjo el cierre de Fase 1** | 75 | ✅ |
| `tools/provider_coverage.py` + `import_gdelt_entropy.py` + `audit_data_lake.py` | Inventario de proveedores, import histórico de entropía, auditoría del lake | 58 + 36 + 32 | ✅ |
| `tools/heartbeat.py` + `.github/workflows/heartbeat.yml` | Trigger `schedule:` real — **desactivado a propósito**, ver Fase 6 | — | ✅ código, 🔴 apagado |

**No existe todavía, confirmado por ausencia real (no supuesto):** grep de
`^class.*Adapter` en `ingestion/adapters.py` da **dos** implementaciones concretas de
`BaseAdapter` — `DerivAdapter` y `TwelveDataAdapter` — más la base abstracta y las
excepciones. Cero `AlpacaAdapter`, cero `TiingoAdapter`. De los 4 activos del legacy,
TwelveData cubre BTC (`BTC/USD`) y abre la puerta a acciones (`AAPL` verificado); **XAU
y NIFTY50 siguen sin fuente**, y XAU específicamente quedó fuera del mapa por falta de
evidencia de que el plan gratuito lo cubra, no por olvido. Ningún trainer de LSTM.
Ningún ruteo de órdenes de ningún tipo.

**Los fixtures de TwelveData ya son capturas reales** (2026-08-23, literales): EUR/USD,
BTC/USD y AAPL en 1day. Reemplazan a los sintéticos con los que se escribió el adapter, y
**trajeron un hallazgo que desmiente el supuesto anterior: BTC/USD NO trae `volume`.** De
los tres instrumentos, el único con volumen es AAPL — la regla intuitiva "forex no,
cripto y acciones sí" es falsa.

El adapter no necesitó ni un cambio, y eso no es suerte: `volume_available` se deriva de
si la clave vino en la respuesta, nunca de una regla por clase de activo. Verificado por
mutación —sustituir la derivación observada por una regla declarada deja **4 de 28 tests
en rojo**, y el primero en caer es el que parsea la captura real de BTC. Una regla
declarada habría marcado BTC con volumen disponible y el relleno `0.0` habría entrado al
pipeline como si fuera un dato.

Quedan dos fixtures sintéticos, marcados como tales y con motivo: un contrafáctico
deliberado (forex *con* volumen — su valor está en que no puede existir en la realidad
observada, y es el que atrapa la regla declarada en la dirección inversa) y uno intradía
por necesidad (las tres capturas son diarias, y hay dos comportamientos intradía que
probar que dependen del argumento `timeframe`, no del payload).

🟡 **Sigue amarillo, no verde**, y por una razón más chica que antes: el test `live`
(marker `live`, skipif sobre la credencial) todavía no corrió nunca con clave real. Lo que
falta confirmar contra la API viva es el camino que ningún fixture ejercita — el cliente
httpx que el adapter abre y cierra solo (los tests offline inyectan el suyo), la
autenticación aceptada de verdad, y la forma intradía.

**Desde PR-4 ya hay cómo correrlo:** cargar `TWELVEDATA_API_KEY` en los Secrets del repo
y disparar `live-tests.yml` a mano (Actions → SPEL Live Tests → Run workflow). Es una
acción de un minuto, y es lo único que separa a este adapter de ✅.

**~~Hallazgo relacionado — no hay ningún punto de composición.~~ CERRADO (PR-4).** El
hallazgo del 18 ago decía: no es que falte un adapter, falta el lugar donde algo se arma
y corre de verdad. Eran tres mitades del mismo hueco, y las tres están cerradas:

| Mitad del hueco | Cómo estaba | Cómo está |
|---|---|---|
| Nada instancia un adapter fuera de tests | `DerivAdapter(` solo en su propia definición | `ingestion/sources.py::build_price_sources()` construye los dos |
| `load_secret()` no se llama desde producción | los 2 hits fuera de `tests/` eran docstrings | `sources.py` es el primer —y único— llamador real |
| Ningún workflow inyecta secretos | cero `secrets.` y cero `env:` en `.github/workflows/` | `live-tests.yml` inyecta `TWELVEDATA_API_KEY` por `env:` de job |

**El punto de composición es UNO, y hay un test que lo mantiene así.** Los adapters
reciben credenciales por constructor y nunca leen el entorno; `test_sources.py` verifica
**con AST** (no con grep, que daría falso positivo con los docstrings que hablan del tema)
que `ingestion/adapters.py` no importe `governance.*` ni `os`. Si mañana hay tres lugares
que resuelven credenciales, "¿por qué no arrancó tal fuente?" vuelve a ser una búsqueda en
vez de una lectura.

**Una fuente sin credencial es capacidad ausente, no error.** `build_price_sources()` nunca
lanza por una credencial faltante: devuelve un `SourceInventory` con lo que sí se pudo
construir y, para lo demás, el motivo nombrando la variable exacta
(`"faltan DERIV_API_TOKEN, DERIV_APP_ID"`). Deriv necesita las dos credenciales y el motivo
dice cuál falta — tener el token y que falte el `app_id` es un caso real, y un
"credenciales incompletas" obligaría a adivinar entre las dos.

**Lo que sigue faltando** es distinto y más chico: nada llama todavía a
`build_price_sources()` desde un ciclo real. `orchestration/cycle.py` corre scoring sobre
datos que recibe, no sobre datos que va a buscar. Esa es la conexión que falta ahora — ya
no "no hay dónde armar las piezas", sino "las piezas armadas no se usan todavía".

---

## 🔵 FASE 1 CERRADA — el resultado, y por qué es negativo

Medido el **4-sep-2026** sobre datos reales, criterio `4.0.0-entropy_state_p66`.
Detalle completo y método en `decision-log.md`.

**El `n` alcanzó.** BTC **1.211** post-máscara (5/5 folds estables), XAU **823** (4/5).
Los dos superan el umbral de `DEFENDIBLE`. Eso importa para leer el resultado: no es
"no había muestras suficientes para saber", es un negativo medido con potencia.

**Dirección — la máscara no discrimina.**

| activo | dentro del régimen | fuera | p |
|---|---|---|---|
| BTC | 51,81% [49,14–54,47] | 52,53% [50,79–54,26] | 0,68 |
| XAU | 50,98% [47,78–54,18] | 52,60% [50,58–54,61] | 0,42 |

Los intervalos se solapan casi por completo y, en los dos activos, la tasa **dentro** del
régimen es más baja que fuera. Autocorrelación de retornos en BTC: −0,028 dentro,
−0,026 fuera.

**Magnitud — sí discrimina, y solo en BTC.** Mann-Whitney: BTC ratio de volatilidad
**1,246**, p = 3,4×10⁻⁹. XAU ratio 1,115, p = 0,56.

**Qué significa.** Coincide con la literatura sobre índices de incertidumbre construidos
desde noticias: predicen magnitud, no signo — el EPU correlaciona 0,73 con el VIX, que
es un índice de volatilidad. La entropía geopolítica mide **cuánta turbulencia hay**, no
**hacia dónde va el precio**. La hipótesis original estaba mal formulada: se le pedía a
la señal algo que este tipo de índice no hace.

**Lo que NO invalida.** El pipeline de ingestion, la persistencia, el contrato de datos,
la integridad temporal y la máscara como tal siguen siendo correctos y medidos. Lo que
cae es el uso que se les estaba dando.

---

## 🔒 CONTRATO DE DATOS OHLCV

Tres reglas que cualquier adapter nuevo tiene que respetar. No son estilo — cada una
existe porque su ausencia produce un fallo silencioso, que es la clase de bug que ya
costó meses en este proyecto.

**1. `require_closed` tiene semántica condicional, y es a propósito.** No significa
"siempre valida el cierre", significa "valida el cierre cuando es posible saberlo". Con
`granularity_s` presente rechaza velas abiertas; con `granularity_s=None` omite *solo*
esa verificación — columnas, tipos, UTC, orden, NaN y OHLC siguen activas. La
granularidad **nunca se infiere del espaciado entre timestamps**: un dataset con huecos
legítimos (fin de semana forex, feriados) daría una inferencia equivocada, y una
granularidad equivocada rechaza velas buenas o acepta abiertas. El call site que obliga
a que `None` sea válido es real: `ingestion/training_dataset.py:119` valida un dataset ya
construido, sin acceso a la granularidad original.

**2. `df.attrs` es transporte de UN SOLO SALTO, nunca almacenamiento.** El adapter
escribe la metadata de calidad en `attrs`; `AdapterChain` la levanta a `AdapterResult`
inmediatamente después del fetch, y ahí muere. Medido en pandas 3.0.5, no supuesto:

| Operación | `attrs` sobrevive |
|---|---|
| `copy()` | ✅ |
| `sort_values()` | ✅ |
| `concat()` | ✅ |
| `reset_index()` | ✅ |
| `merge()` | 🔴 **se pierde** |

`training_dataset.py` hace exactamente un join OHLCV↔GDELT. Usar `attrs` como
almacenamiento durable perdería la procedencia justo donde más importa —al armar el
dataset de entrenamiento— y en silencio, sin excepción ni warning. Por eso los campos
viven en el dataclass.

**3. `pandas>=2.2,<4` es la única dependencia pineada.** `attrs` es API documentada como
experimental por pandas y el diseño depende de su comportamiento exacto; sin pin, una
resolución distinta en CI podría cambiarlo sin que nadie toque el código. `numpy`,
`httpx`, `websockets` y `pytest` siguen sin pinear — nada del contrato depende de ellos.

**Red de seguridad armada.** `DerivAdapter.fetch_ohlcv()` llama a `validate_ohlcv_schema()`
con `granularity_s` aunque `_to_dataframe()` ya corrió el filtro: es redundante a
propósito, y en el camino normal no debería dispararse nunca. Si dispara, el filtro falló
o alguien lo removió. Verificado por mutación: quitar el argumento del call site deja
**64 de 65 tests en verde**; el único que lo detecta es
`test_deriv_arma_la_red_de_seguridad_pasando_granularity_s`, que verifica el argumento y
no el efecto justamente por eso.

**Nomenclatura — cuatro términos que no son sinónimos:**

| Término | Qué es | Ejemplo |
|---|---|---|
| `source` | el PROVEEDOR de los datos | `"deriv"`, `"twelvedata"` |
| `symbol` | el INSTRUMENTO | `"EURUSD"`, `"VOL75"` |
| `adapter_name` | qué adapter produjo el resultado (`AdapterResult`) | `"deriv"` |
| `is_fallback` | vino de una fuente de respaldo — **distinto de `is_degraded`**: un respaldo puede funcionar perfecto | `True` + `is_degraded=False` |

Ni `source` ni `symbol` deben recibir jamás algo derivado de un secreto: los dos se
escriben en el log.

---

## 🔬 FASE 2 — REORIENTADA por el resultado de la validación

**El modelo NO debe ser un clasificador direccional filtrado por entropía.** Esa era la
arquitectura implícita en todo lo anterior, y la medición del 4-sep la descarta: la
máscara no separa días direccionalmente predecibles de días que no lo son (p = 0,68 y
0,42).

**La vía con fundamento medido es dimensionamiento de posición.** Un régimen que
multiplica la volatilidad por 1,25 con p = 3,4×10⁻⁹ es información accionable para
decidir *cuánto* arriesgar. No lo es para decidir *de qué lado*. Todo lo que sigue en
esta sección se escribió antes de esa medición y hay que leerlo con eso en mente: sigue
siendo cierto como auditoría del legacy, y ya no describe el plan.

**Advertencia sobre el `n`:** el `n` de 1.211 y 823 fue medido para una pregunta
direccional (binomial sobre aciertos). Una pregunta sobre magnitud tiene otra potencia y
otro umbral; el `n` no se hereda entre preguntas distintas.

Auditado contra el legacy real (18 ago), sin cambios desde entonces:

- `te_score` y `backbone_score`: **listos**, portados con 2 bugs reales corregidos
  (`core/price_signals.py`).
- `godel_score`: depende de `val_dir`, salida de un **LSTM entrenado** — arquitectura
  canónica "Regla 13", **bloqueada por guardián real** (`enforce_lstm_architecture()`
  en el legacy lanza `RuntimeError` ante cualquier desvío de `input_size=20,
  hidden_size=64, num_layers=1` — existen 14 checkpoints `.pt` atados a esa forma
  exacta). No hay nada que portar hasta que ese modelo aprenda algo real.
- Accuracy base histórica confirmada (legacy, `LSTM_BASE_ACCURACY`): NVDA 0.550, BTC
  0.528, XAU 0.547, NIFTY50 0.625 — el "~0.50" que bloquea Fase 4 puede ser ese mismo
  techo bajo original, no necesariamente una regresión nueva.
- Causa raíz real del estancamiento (Altair, 18 ago): parquets fuente con columnas de
  fecha en formatos distintos entre sí ('/' vs '-') — datos no normalizados
  alimentando el entrenamiento sin detectarse a tiempo. **Ya no puede repetirse en la
  parte nueva**: Deriv entrega epoch (sin ambigüedad de formato posible),
  `validate_ohlcv_schema()` rechaza cualquier timestamp que no sea UTC estricto y
  ordenado — verificado con test de regresión directo.
- Clase de bug MÁS AMPLIA encontrada en el legacy (`spel_trainer_audit.py`,
  BUG-LA-01): normalizar con estadísticas del dataset completo en vez de solo train,
  o mezclar datos temporales antes del split. Documentado en
  `ingestion/training_dataset.py` para cuando se escriba el trainer — no resuelto
  todavía porque ese código no existe.
- `ingestion/training_dataset.py` ya construido: une OHLCV + serie GDELT con
  forward-fill, listo para alimentar cualquier arquitectura que se elija.

**Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md` para las opciones reales (LSTM vs.
Random Forest/XGBoost/etc.) — nada decidido todavía, es investigación, no un plan.**

---

## 🗂️ CÓDIGO LEGACY — sin cambios, ya estaba correcto

```
archive/legacy-pre-20260813              → 74 módulos originales, intactos
archive/dashboard-data-pre-20260813      → cache de GitHub Actions
archive/feature-cache-pre-20260813       → cache de features
archive/model-cache-pre-20260813         → cache de modelos (14 checkpoints .pt)
archive/limpieza-legado-99-pre-20260813  → última limpieza previa al reinicio
```

Regla fija sin excepción: nada bajo `ingestion/`, `core/`, `execution/`,
`orchestration/` o `visualization/` importa desde ninguna rama `archive/*`.

---

## ⚠️ GOBERNANZA DE DOCUMENTOS — hallazgo del 17 ago, TODAVÍA sin resolver

Sigue pendiente, no se resolvió solo con el tiempo: `SPEL_PERSISTENCE_STATE.md` y
`SPEL_PERSISTENCIA_v2.md` en Drive raíz siguen ahí, sin archivar, compitiendo con
este archivo. Este patch reemplaza el contenido de `SPEL_PERSISTENCE_STATE.md` con
un espejo literal de este archivo (marcado como espejo, no editable ahí) — pero
`SPEL_PERSISTENCIA_v2.md` sigue sin archivar. Acción pendiente para la próxima
sesión, no lo resolví en esta.

---

## ❓ INCÓGNITAS REALES — sin resolver, no inventadas para llenar espacio

1. **¿GitHub Actions ya verificó una descarga real de GDELT?** Nunca confirmado —
   cada intento de chequear la pestaña Actions vía API chocó con rate limit sin
   autenticar. Sigue siendo la verificación más urgente pendiente.
2. **¿GDELT tiene cobertura completa de 2026?** El auditor legacy
   (`spel_auditoria_total.py`) tenía un chequeo específico para esto
   (`GDELT_GAP_2026`) — no se corrió el equivalente contra el pipeline nuevo.
3. **¿El motor GDELT (Fases 1-5) sigue siendo el principal, o el motor rápido
   (Fase 6) cambia la prioridad?** Pregunta abierta desde el 17 ago, todavía sin
   que Altair la confirme.
4. **`p66_entropy_global_default`** (necesario para `godel_active` vía
   `orchestration/cycle.py`): sin valor por defecto a propósito — `compute_adaptive_percentile`
   documenta que para este umbral no hay default legacy confirmado. Cada llamada real
   necesita decidir qué número usar. *(Se llamaba `p90_entropy_global_default` hasta la
   versión 4.0.0 del criterio; el nombre viejo describía un término que nunca cambió un
   resultado — ver `decision-log.md`, 6-sep.)*
5. **DEU como único proxy de "Eurozona/BCE"** en `GOBIERNO_COUNTRY_FILTERS`: ¿alcanza
   solo, o hace falta sumar FRA/ITA? Sin backtest, documentado como pendiente desde
   que se escribió `classify_gdelt_event`.
6. **GBPUSD/USDJPY/USDCHF/AUDUSD** ya tienen precio real (`DerivAdapter`) pero NO
   tienen clasificación GDELT — `FX_GOBIERNO_ONLY_ASSETS` solo cubre EURUSD. Decidir
   qué país no-USA representa a cada banco central (BoE/BoJ/SNB/RBA) sigue pendiente.
7. **Transfer Entropy real (Schreiber) vs. el proxy portado**: existe una versión más
   rigurosa en `spel_math_engine.py` (con Hurst, backend `pyinform`), pero ese archivo
   tiene 177 referencias rotas a numpy/polars sin auditar — no se tocó esta sesión.
8. **Índices de Volatilidad (VOL10-VOL100)**: tienen precio real, cero vía de scoring
   — GDELT no aplica por diseño (Fase 6), pero no existe todavía una alternativa
   técnica pura para ellos. `orchestration/cycle.py` los excluye a propósito.
9. **Isolation Forest**: el legacy ya lo usaba para detectar anomalías en la entropía
   GDELT (`PARAM_ISO`, umbral 0.6) — es de la misma familia que Random Forest. Nunca
   se portó al repo nuevo. Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md`.
10. **Alpaca**: cero código en el repo nuevo, solo la decisión de mantenerlo en modo
    paper (17 ago). Cuando el amigo de Altair complete la verificación con Banco
    Pichincha, hace falta construir el adapter desde cero — no existe ni un stub.

---

## 🖥️ SPEL_Control_Panel.ipynb — mejoras concretas para producción

No auditado línea por línea esta sesión (34.6 KB, no se justificó el costo de leerlo
completo todavía) — estas son mejoras identificadas por los síntomas reales que ya
aparecieron, no una revisión exhaustiva:

1. **Menú 4 (aplicar patch)**: defenderse de `.git/rebase-apply still exists`
   corriendo `git am --abort` automáticamente si ese directorio existe, antes de
   intentar aplicar — ya pasó una vez esta sesión, se resolvió reintentando a mano.
2. **Menú 7 (ver estado/historial)**: que lea el contenido real de este archivo en
   vez de reconstruir el estado a mano desde git log — reduce el riesgo de que
   ESTADO.md y lo que el panel muestra diverjan otra vez.
3. **Descarga de datos históricos para entrenamiento** (nuevo requisito, 18 ago):
   el flujo actual de `DerivAdapter` trae velas recientes para scoring en vivo, no
   un backfill masivo de meses/años para entrenar. Antes de que Fase 2 pueda
   arrancar de verdad, hace falta confirmar si `ticks_history` de Deriv soporta
   pedir historia profunda con `count` alto, o si hace falta paginar con `start`/`end`
   — no verificado todavía, es la primera pregunta técnica real cuando se retome
   Fase 2.
4. **Separar "modo patch" de "modo entrenamiento"**: cuando exista un trainer real,
   correrlo desde el mismo panel de aplicar-patches mezcla dos flujos de trabajo
   distintos (uno es minutos, el otro puede ser horas). Mejor un menú aparte, no
   una opción más en la lista actual.

---

## ▶️ PRÓXIMO PASO CONCRETO

**Decidir la forma de Fase 2 sobre dimensionamiento de posición**, ahora que la vía
direccional quedó descartada con medición. Antes de escribir código hace falta contestar
una pregunta que todavía no tiene respuesta: qué métrica valida un modelo de
dimensionamiento, dado que la accuracy direccional ya no aplica. El `n` de 1.211/823 no
se hereda — fue medido para una pregunta binomial sobre dirección.

*(La Incógnita #1 —si GDELT ya corrió de verdad en GitHub Actions— sigue abierta y sigue
importando para ingestion, pero ya no bloquea el cierre de Fase 1: la validación se
corrió sobre datos reales, así que el pipeline demostró producir datos usables.)*

---

## 📝 CÓMO ACTUALIZAR ESTE ARCHIVO

Al final de cada sesión de código (no a mitad):
1. Actualizar la tabla de módulos con lo que se verificó contra GitHub real.
2. Mover cualquier decisión nueva a `decision-log.md`, no acá — este archivo es
   estado, no bitácora de decisiones.
3. Actualizar "Próximo paso concreto" — una sola cosa, no una lista de deseos.
4. Commitear este archivo junto con el código de esa sesión, mismo commit o el
   siguiente inmediato — la lección del 17 ago fue exactamente no hacer esto.
5. Nunca dejar este archivo diciendo algo que no se verificó — si algo quedó a
   medias, decirlo explícitamente como 🟡, no como ✅.
6. Si este archivo lleva más de ~5 días sin tocarse mientras hay patches nuevos en
   `main`, esa es la misma señal de circularidad que ya dispara "cortar y empezar de
   nuevo con foco" — trátese como tal.
