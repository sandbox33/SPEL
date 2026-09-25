# SPEL — ESTADO DEL PROYECTO

> **⚠️ ESTE ARCHIVO YA NO ES CANÓNICO. Es un ESPEJO.**
>
> El documento canónico es **`SPEL_MANUAL_OPERACION.md`**, en Drive.
> **Si este archivo y el manual divergen, GANA EL MANUAL** — sin excepción y sin
> necesidad de verificar cuál de los dos se escribió después.
>
> Qué sigue siendo cierto de la regla vieja: este archivo se lee primero en cada chat
> nuevo, se actualiza al final de cada sesión y nunca a mitad, y frente a un chat que
> lo contradiga sin evidencia, gana el archivo. Lo que cambió es el escalón de arriba:
> antes decía "la única fuente de verdad" y ya no lo es.
>
> Por qué se degrada a espejo en vez de borrarse: sigue siendo lo que un chat nuevo
> tiene a mano dentro del repo, sin salir a Drive. Un espejo con una regla de
> desempate explícita es más útil que dos documentos que se creen ambos canónicos —
> que es exactamente el problema de gobernanza que este archivo viene arrastrando
> desde el 17 de agosto (ver la sección de gobernanza más abajo).

**Última actualización:** 16 sep 2026 — retiro de la cadena `gold_score` a `research/`.

**Commit de referencia:** `dd9ea63` (merge del PR #26).

Desfase que este PR cierra: **dos PRs (#24 y #26)**, siete días. Es el desfase más
corto que este archivo registró desde que existe la regla — los anteriores fueron de
16 y 17 días. La regla del punto 6 sigue dependiendo de que alguien se acuerde
(Incógnita #11), pero acá se acordó.

El encabezado anterior decía "cierra 17 días de desactualización real" y describía el
mismo problema. Que haya vuelto a pasar, en el mismo archivo y con el mismo diagnóstico
ya escrito arriba, dice que **la regla del punto 6 de "cómo actualizar" no alcanza**:
está redactada como recordatorio y depende de que alguien se acuerde. Queda como
incógnita abierta si hace falta un chequeo automático (ver Incógnita #11).


**Ver también:** `FASE2_NOTAS_ARQUITECTURA_MODELO.md` (raíz del repo) — glosario y
opciones de arquitectura de modelo (LSTM vs. árboles/ensambles), separado de este
archivo a propósito para no mezclar "estado actual" con "notas de investigación".

---

## 🚦 SEMÁFORO DE FASES

```
FASE 1 — ingestion/ + core/scoring.py     🔵 CERRADA — checklist cumplido, resultado NEGATIVO
FASE 2 — Modelo                            ⚪ NO INICIADA — reorientada, ver abajo
FASE 3 — visualization/ (grafo)            ⚪ NO INICIADA
FASE 4 — execution/ + Deriv real           🟡 Actuator confirmado, gate F2 firme
FASE 5 — Escala (Supabase, Flet)           ⚪ NO INICIADA
FASE 6 — Motor streaming multi-timeframe   🟡 Infraestructura lista, señal sin construir
```

🔵 **CERRADA no es 🟢 LISTA.** Fase 1 se cierra porque su pregunta quedó contestada, y
la respuesta fue que no. El pipeline funciona, está medido y tiene `n` suficiente; lo
que no funciona es la hipótesis que ese pipeline existía para probar.

**Y el 16-sep se sacó la consecuencia.** El criterio de cierre de Fase 1 era que el
Gold Score *se calculara*, no que predijera — y se calculaba. Pero calcularlo todos
los días y emitirlo con una advertencia de cinco líneas pegada para que nadie lo usara
no era una salida del sistema: era ruido con escolta. La cadena se retiró a
`research/` (PR #27). El ciclo diario emite ahora el **régimen medido** y nada más.
Eso no reabre Fase 1 ni cambia su resultado; ordena el código para que coincida con
él.

---

## 📍 MÓDULOS REALES EN `main` HOY (verificado, no listado de memoria)

**725 recolectados en `tests/`, 723 pasan y 2 se saltan**, en **22 archivos**. Contado
corriendo `pytest --collect-only`, no de memoria. Los 2 `skip` son el test `live` de
TwelveData (`skipif` sobre la credencial) y uno de persistencia.

**Más 74 en `research/tests/`, que NO corren en el job que bloquea** — son los tests
del código retirado (ver la sección de `research/` más abajo). El total real del repo
es 799, pero mezclarlos en una sola cifra escondería justamente la distinción que el
retiro del 16-sep existe para marcar.

Este archivo decía **678**, de la actualización del 9 de septiembre.

> **Precisión sobre cómo se cuenta**, porque los tres números que circulan son
> distintos y los tres son "correctos" según qué se pregunte:
> - **556** — funciones `def test_` escritas a mano.
> - **678** — casos que pytest ejecuta, después de expandir `@pytest.mark.parametrize`.
> - **20** — archivos `test_*.py` (`tests/` tiene 21 archivos contando `__init__.py`).
>
> El número que vale para "¿cuánto cubre la suite?" es **678**, porque es lo que
> efectivamente corre. Un brief anterior citaba "649 en 21 archivos" y no reproduce
> con ninguna de las tres formas de contar sobre este commit.

Todo lo de abajo corrió en clon 100% ajeno, venv limpio desde `requirements.txt`, 10
corridas seguidas sin intermitencia.

| Módulo | Qué hace | Tests | Estado |
|---|---|---:|---|
| `core/scoring.py` | `entropy_state` (Capa 1), `godel_active`, `compute_godel_score`, `gold_score_bma`, vitality_tesla, nash_frozen_7d, classify_gdelt_event | 171 | ✅ |
| `core/monte_carlo.py` | Validación GBM — NO entrena, simulación pura en cada llamada | 22 | ✅ |
| `core/price_signals.py` | te_score (proxy TE) + backbone_score (EMA20/63) — **sin poder predictivo demostrado**, ver decision-log 6-sep | 12 | ⚠️ |
| `ingestion/adapters.py` | DerivAdapter + TwelveDataAdapter + contrato de datos | 65 + 29 | ✅ |
| `ingestion/sources.py` | **Punto de composición** — `build_price_sources()`; `SourceInventory` distingue capacidad ausente de error | 11 | ✅ |
| `ingestion/gdelt.py` + `_aggregation` + `_series` | Pipeline GDELT completo, persistencia JSONL | 14 + 15 + 18 | ✅ |
| `ingestion/run_gdelt.py` + `.github/workflows/gdelt.yml` | Ingesta diaria; CI escritor único en la rama `data`; reintenta los días que GDELT no había publicado | 59 + 12 | ✅ código, 🟡 primera corrida real pendiente de la siembra |
| `ingestion/frescura.py` | Alarma de la serie: rojo solo por hueco interno posterior a la marca de inicio | 18 | ✅ |
| `tools/verificar_siembra.py` | Compara BTC/XAU sembrados contra la serie medida | 14 | ✅ código, 🟡 no corrido contra la siembra real |
| `ingestion/deriv_ws.py` | Sesión de solo lectura con Deriv: `entorno` obligatorio, `real` con permiso aparte, lista blanca de siete mensajes | 29 | ✅ |
| `ingestion/sonda_instrumentos.py` + `.github/workflows/sonda.yml` | Contratos, multiplicadores, stake y comisión de BTC y oro, desde la API, siete días | 19 + 6 | ✅ código, 🟡 no corrido contra Deriv real (bloqueado desde el sandbox) |
| `ingestion/velas.py` + `.github/workflows/velas.yml` | Velas diarias de BTC y oro en la rama `data`, profundidad usable, `leer_velas()` en polars | 33 + 6 | ✅ código, 🟡 no corrido contra Deriv real |
| `core/preregistro_h1.py` + `research/preregistro_h1.md` | Reglas del experimento H1, fijadas antes del backtest | 26 | 🟡 dos cláusulas PENDIENTES del Admin |
| `ingestion/source_registry.py` | Registro versionado de cobertura por fuente | 34 | ✅ |
| `tools/measure_godel_samples.py` | Mide el `n` post-máscara | 75 | ✅ |
| `tools/provider_coverage.py` + `import_gdelt_entropy.py` + `audit_data_lake.py` | Inventario de proveedores, import histórico de entropía, auditoría del lake | 58 + 36 + 32 | ✅ |
| `ingestion/training_dataset.py` | Une OHLCV + serie GDELT, forward-fill, coverage_ratio explícito | 7 | ✅ |
| `orchestration/cycle.py` | Corre vitality/nash/godel sobre 5 activos; calcula `gold_score`; sella `godel_criteria_version` | 26 | ✅ |
| `execution/circuit_breaker.py` + `execution_guard.py` | Guardrails duros — congelados hasta F4 | 31 | ✅ |
| `governance/persistence.py` + `secrets.py` | 4 streams, SecretKey único | 28 | ✅ |
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

## 🔵 FASE 1 CERRADA — dos lecturas del mismo cierre

El checklist se cumplió **y** la hipótesis de fondo se refutó. Las dos cosas son ciertas
a la vez y ninguna sustituye a la otra, así que van las dos.

### 1. El criterio de BLUEPRINT.md: el Gold Score se calcula

`BLUEPRINT.md` fija el criterio textual:

> "`ingestion/` trae un OHLCV real de Deriv y un GDELT real, `core/scoring.py` calcula un
> **Gold Score real** a partir de eso, con un test que corre en CI y pasa."

El PR #19 (`compute_godel_score`) cerró la última pieza. Los tres componentes tienen
función real: `compute_godel_score` (core/scoring.py), `compute_transfer_entropy_proxy`
y `compute_backbone_score` (core/price_signals.py). El test de cierre corre en CI y
verifica **el valor** —0.539239 sobre cierres deterministas— no que no lance excepción.

**La salvedad, y no es letra chica: el criterio exige que el Gold Score SE CALCULE, no
que PREDIGA.**

- `te_score` y `backbone_score` **fueron medidos y no son significativos**: cero
  supervivientes a Bonferroni y a Benjamini-Hochberg, holdout p = 0,4133 y p = 0,5921,
  y un backtest de reversión que perdió el 99,2% del capital en BTC. Acta completa en
  `decision-log.md`, entrada del 6-sep.
- `godel_score` vale **0.0** mientras no exista un LSTM que sirva `val_dir`.
- Los pesos **0.40/0.30/0.30 nunca se calibraron**. La etiqueta "inamovible (Regla 13)"
  del legacy documenta gobernanza, no un ajuste empírico.

Y un hallazgo estructural del propio PR #19: el término `w_godel * godel_score` **no
puede aportar a ningún gold_score distinto de cero**, ni con un `val_dir` de 1.0 —
cuando la máscara dispara, el kill por `godel_active` pone el score en 0.0; cuando no
dispara, el componente vale 0.0 por definición. El peso de 0.40/0.55 está muerto. La
causa es una rama de kill que agregó el port y que ninguna de las dos fuentes legacy
tiene; queda como tarea aparte, con test que la fija.

### 2. El resultado de la validación: la máscara no discrimina dirección

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

### Cerrada ≠ exitosa

La fase cierra porque su checklist se cumplió. Lo que el sistema produce hoy es un número
calculado de punta a punta con funciones reales, y **no una señal operativa**. Todo
`gold_score` viaja con esa advertencia pegada en `gold_score_warning`.

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

**RESUELTO EN PARTE, el 9-sep: ya hay un canónico declarado.**
`SPEL_MANUAL_OPERACION.md` (Drive) es el documento canónico, y este archivo pasa a
espejo con regla de desempate explícita — **si divergen, gana el manual**. Ver el
encabezado.

Eso ataca la raíz del hallazgo del 17 de agosto, que no era "hay archivos de más" sino
"hay varios documentos que se creen la fuente de verdad y ninguno cede". Con un canónico
declarado, un documento de más es un espejo desactualizado —molesto— en vez de una
contradicción sin árbitro.

**Lo que sigue pendiente:** `SPEL_PERSISTENCE_STATE.md` y `SPEL_PERSISTENCIA_v2.md` en
Drive raíz siguen sin archivar. Ahora es una limpieza, no un problema de gobernanza: la
jerarquía ya está definida y esos dos quedan por debajo del manual igual que este
archivo. Sigue siendo acción en Drive, fuera del repo.

---

## ❓ INCÓGNITAS REALES — sin resolver, no inventadas para llenar espacio

1. ~~**¿GitHub Actions ya verificó una descarga real de GDELT?**~~ **CERRADA el
   9-sep, y la respuesta no es la que la pregunta esperaba: NO PUDO haberla
   verificado, porque ningún workflow invoca GDELT.** Verificado leyendo los tres:

   | workflow | qué corre | disparo |
   |---|---|---|
   | `tests.yml` | `python -m pytest tests/ -v` | push, pull_request |
   | `live-tests.yml` | `python -m pytest tests/ -m live -v` | workflow_dispatch |
   | `heartbeat.yml` | `python tools/heartbeat.py` | workflow_dispatch (el `schedule:` está comentado) |

   Ninguno toca GDELT. `tools/heartbeat.py` importa `core.monte_carlo` y nada más.

   La única mención de GDELT en todo `.github/workflows/` es un **comentario** en
   `tests.yml:13`, que dice cambiar el paso final por `python ingestion/run_gdelt.py`.
   **Ese archivo no existe** — un `grep` de `run_gdelt` en todo el repo devuelve
   exactamente ese comentario y nada más.

   **No era una verificación pendiente: era un entry point ausente.** La incógnita
   estuvo abierta desde el 17 de agosto preguntando por el resultado de algo que nunca
   se podía haber ejecutado, y el intento de contestarla mirando la pestaña Actions no
   iba a resolverla nunca — la respuesta no estaba en Actions, estaba en que falta el
   archivo. Se cierra como hallazgo, no como confirmación.

   **Lo que queda pendiente, ahora bien formulado:** escribir `ingestion/run_gdelt.py`
   (o el entry point que corresponda) y decidir si el `schedule:` de un workflow lo
   dispara. Es trabajo, no una consulta.

   *(Que la ingestion GDELT funciona ya está demostrado por otra vía: la entropía
   histórica de BTC y XAU —3.998 días por activo— está importada y es la que alimentó
   la medición de la máscara. Lo que falta es que corra sola en CI, no que corra.)*
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

11. **¿Hace falta un chequeo automático de desactualización de este archivo?** El punto
    6 de "cómo actualizar" dice que si pasan ~5 días con patches nuevos en `main` sin
    tocar este archivo, eso es señal de circularidad. La regla existe desde el 18 de
    agosto **y falló igual**: 16 días y 14 PRs de desfase, con el diagnóstico correcto
    ya escrito en el propio encabezado. Un recordatorio que depende de que alguien se
    acuerde no es un control. Una opción barata sería un job que compare la fecha del
    último commit de `ESTADO.md` contra la del último commit de `main` y falle o avise
    pasado un umbral. No implementado — es una decisión de Altair, no una tarea obvia.

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

## 📦 `research/` — EL CÓDIGO RETIRADO (nuevo, 16 sep 2026)

Paquete nuevo en la raíz. Guarda lo que el motor diario **ya no ejecuta** pero que
sigue siendo válido como hipótesis, con sus tests, importable y corrible.

| módulo | qué | por qué salió |
|---|---|---|
| `gold_score_chain.py` | `compute_gold_score_bma`, `compute_godel_score`, `compute_nash_frozen_7d` y sus tipos | depende de un LSTM que no existe; y el término Gödel no puede aportar (hallazgo PR #19) |
| `price_signals.py` | `compute_transfer_entropy_proxy`, `compute_backbone_score` | tesis direccional refutada el 4-sep |
| `cycle_gold_score.py` | el cableado que los componía dentro de `run_scoring_cycle` | viajó con lo que componía |

**No es `archive/*`.** Los retiros del PR #22 fueron a ramas de archivo, donde el
código deja de correr — correcto para algo que no vuelve. Acá la **condición de
reversión está escrita**: si Fase 2 entrena el LSTM que produce `val_dir`, la cadena
vuelve. Por eso tiene que seguir compilando contra el motor vivo.

`research/tests/test_aislamiento.py` fija la dirección: **el motor nunca importa de
`research/`**. Sin ese test el retiro sería una afirmación sobre carpetas, no sobre
quién llama a quién.

CI: `tests.yml` tiene un job `research` con `continue-on-error: true` — visible si se
rompe, sin poder frenar un merge. A mano: `pytest research/tests/ -q`.

Ver la entrada del 16-sep en `decision-log.md` para el razonamiento completo y la
salvedad que hay que resolver **antes** de revertir.

---

## ▶️ PRÓXIMO PASO CONCRETO

**Completar las dos cláusulas PENDIENTES de `research/preregistro_h1.md` y fusionar H1-A.**

La alternativa para Sharpe < 0,3, y qué pasa entre 0,3 y 0,5 o con PSR/DSR por debajo de
0,90. Sin eso el pre-registro no cierra, y declararlas después de ver un resultado es lo que
el documento existe para impedir. H1-B —el backtest— arranca siete días después de fusionar,
cuando la sonda haya medido la comisión siete veces.

*(Lo que estaba acá —"decidir qué mide el éxito de Fase 2"— lo contesta el pre-registro H1
con sus compuertas: Sharpe neto fuera de muestra, PSR y DSR. Queda abajo como historia.)*

**Antes: decidir qué mide el éxito de Fase 2, ahora que el ciclo diario emite solo régimen.**

El entry point de GDELT ya existe (`ingestion/run_gdelt.py`, PR #24) y el motor quedó
limpio de la cadena muerta (PR #27). Lo que queda sin contestar es lo que bloquea
arrancar Fase 2 de verdad: la accuracy direccional dejó de aplicar cuando Fase 1 cerró
en negativo, y no hay métrica que la reemplace. Sin eso, entrenar un modelo es entrenar
contra un criterio que nadie fijó.

Es una sola cosa, y es anterior a cualquier línea de código de modelo.

*(Se mueve acá desde la sección de Fase 2, donde figuraba como "decisión de diseño
pendiente". Deja de serlo: con Fase 1 cerrada y el motor ordenado, es el único
bloqueante que queda.)*

*(Una sola, como manda el punto 3 de "cómo actualizar este archivo". La otra decisión
pendiente —qué métrica valida un modelo de dimensionamiento, dado que la accuracy
direccional ya no aplica— es de diseño y vive en la sección de Fase 2, no acá. Fase 1 ya
está cerrada y no depende de ninguna de las dos.)*

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
